from __future__ import annotations

import argparse
import json
import mimetypes
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from youtube_recent_downloader import DEFAULT_STATE_FILE, PROJECT_ROOT, load_dotenv, load_state, save_state
    from tiktok_oauth import get_valid_access_token
except ModuleNotFoundError:  # pragma: no cover
    from .youtube_recent_downloader import DEFAULT_STATE_FILE, PROJECT_ROOT, load_dotenv, load_state, save_state
    from .tiktok_oauth import get_valid_access_token


TIKTOK_API_BASE = "https://open.tiktokapis.com"
DIRECT_POST_INIT_ENDPOINT = f"{TIKTOK_API_BASE}/v2/post/publish/video/init/"
INBOX_UPLOAD_INIT_ENDPOINT = f"{TIKTOK_API_BASE}/v2/post/publish/inbox/video/init/"
POST_STATUS_ENDPOINT = f"{TIKTOK_API_BASE}/v2/post/publish/status/fetch/"
CREATOR_INFO_ENDPOINT = f"{TIKTOK_API_BASE}/v2/post/publish/creator_info/query/"
DEFAULT_PUBLISH_STATE_FILE = PROJECT_ROOT / ".state" / "tiktok_publish_state.json"
MIN_CHUNK_SIZE = 5 * 1024 * 1024
MAX_CHUNK_SIZE = 64 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 16 * 1024 * 1024


@dataclass(frozen=True)
class ChunkPlan:
    video_size: int
    chunk_size: int
    total_chunk_count: int


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def mime_type_for_video(path: Path) -> str:
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed in {"video/mp4", "video/quicktime", "video/webm"}:
        return guessed
    if path.suffix.lower() in {".mov", ".qt"}:
        return "video/quicktime"
    if path.suffix.lower() == ".webm":
        return "video/webm"
    return "video/mp4"


def make_chunk_plan(video_size: int, preferred_chunk_size: int = DEFAULT_CHUNK_SIZE) -> ChunkPlan:
    if video_size <= 0:
        raise ValueError("La video est vide.")
    if video_size < MIN_CHUNK_SIZE:
        return ChunkPlan(video_size=video_size, chunk_size=video_size, total_chunk_count=1)

    chunk_size = max(MIN_CHUNK_SIZE, min(preferred_chunk_size, MAX_CHUNK_SIZE))
    if video_size <= chunk_size:
        return ChunkPlan(video_size=video_size, chunk_size=video_size, total_chunk_count=1)
    total_chunk_count = max(1, video_size // chunk_size)
    return ChunkPlan(video_size=video_size, chunk_size=chunk_size, total_chunk_count=total_chunk_count)


def http_json(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"TikTok HTTP {exc.code}: {error_body}") from exc


def init_direct_post(
    video_path: Path,
    token: str,
    *,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    is_aigc: bool,
    chunk_size: int,
) -> dict[str, Any]:
    plan = make_chunk_plan(video_path.stat().st_size, chunk_size)
    payload = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": 1000,
            "is_aigc": is_aigc,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": plan.video_size,
            "chunk_size": plan.chunk_size,
            "total_chunk_count": plan.total_chunk_count,
        },
    }
    response = http_json(DIRECT_POST_INIT_ENDPOINT, token, payload)
    error = response.get("error", {})
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok init error: {error.get('code')} {error.get('message')}")
    data = response.get("data", {})
    if not data.get("publish_id") or not data.get("upload_url"):
        raise RuntimeError(f"TikTok init response incomplete: {response}")
    return {"response": response, "chunk_plan": plan}


def init_inbox_upload(
    video_path: Path,
    token: str,
    *,
    chunk_size: int,
) -> dict[str, Any]:
    plan = make_chunk_plan(video_path.stat().st_size, chunk_size)
    payload = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": plan.video_size,
            "chunk_size": plan.chunk_size,
            "total_chunk_count": plan.total_chunk_count,
        },
    }
    response = http_json(INBOX_UPLOAD_INIT_ENDPOINT, token, payload)
    error = response.get("error", {})
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok inbox init error: {error.get('code')} {error.get('message')}")
    data = response.get("data", {})
    if not data.get("publish_id") or not data.get("upload_url"):
        raise RuntimeError(f"TikTok inbox init response incomplete: {response}")
    return {"response": response, "chunk_plan": plan}


def query_creator_info(token: str) -> dict[str, Any]:
    response = http_json(CREATOR_INFO_ENDPOINT, token, {})
    error = response.get("error", {})
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok creator info error: {error.get('code')} {error.get('message')}")
    return response.get("data", {})


def validate_creator_can_publish(creator_info: dict[str, Any], privacy_level: str) -> None:
    options = creator_info.get("privacy_level_options") or []
    if options and privacy_level not in options:
        raise RuntimeError(
            f"Privacy TikTok invalide: {privacy_level}. Options autorisees: {', '.join(options)}"
        )


def upload_video_chunks(upload_url: str, video_path: Path, plan: ChunkPlan) -> None:
    mime_type = mime_type_for_video(video_path)
    with video_path.open("rb") as file:
        for chunk_index in range(plan.total_chunk_count):
            start = chunk_index * plan.chunk_size
            if chunk_index == plan.total_chunk_count - 1:
                end = plan.video_size - 1
            else:
                end = min(start + plan.chunk_size, plan.video_size) - 1
            size = end - start + 1
            file.seek(start)
            data = file.read(size)
            request = urllib.request.Request(
                upload_url,
                data=data,
                method="PUT",
                headers={
                    "Content-Type": mime_type,
                    "Content-Length": str(size),
                    "Content-Range": f"bytes {start}-{end}/{plan.video_size}",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    if response.status not in {200, 201, 206}:
                        raise RuntimeError(f"TikTok upload HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"TikTok upload HTTP {exc.code}: {body}") from exc


def fetch_publish_status(token: str, publish_id: str) -> dict[str, Any]:
    response = http_json(POST_STATUS_ENDPOINT, token, {"publish_id": publish_id})
    error = response.get("error", {})
    if error.get("code") not in {None, "ok"}:
        raise RuntimeError(f"TikTok status error: {error.get('code')} {error.get('message')}")
    return response


def build_caption(video_record: dict[str, Any], template: str) -> str:
    title = str(video_record.get("title") or "Nouveau clip")
    channel = str(video_record.get("channel") or "")
    niche = str(video_record.get("niche") or "")
    caption = template.format(title=title, channel=channel, niche=niche).strip()
    return caption[:2200]


def discover_ready_shorts(video_state: dict[str, Any]) -> list[tuple[str, dict[str, Any], Path]]:
    ready: list[tuple[str, dict[str, Any], Path]] = []
    for video_id, record in video_state.items():
        if record.get("render_status") != "rendered":
            continue
        for short_path in record.get("shorts", []):
            path = Path(short_path)
            if path.exists():
                ready.append((video_id, record, path))
    return ready


def publish_short(
    short_path: Path,
    token: str,
    *,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    is_aigc: bool,
    chunk_size: int,
) -> dict[str, Any]:
    creator_info = query_creator_info(token)
    validate_creator_can_publish(creator_info, privacy_level)
    init_payload = init_direct_post(
        short_path,
        token,
        title=title,
        privacy_level=privacy_level,
        disable_comment=disable_comment,
        disable_duet=disable_duet,
        disable_stitch=disable_stitch,
        is_aigc=is_aigc,
        chunk_size=chunk_size,
    )
    response = init_payload["response"]
    plan = init_payload["chunk_plan"]
    data = response["data"]
    upload_video_chunks(data["upload_url"], short_path, plan)
    return {
        "publish_id": data["publish_id"],
        "upload_url_received": True,
        "chunk_size": plan.chunk_size,
        "total_chunk_count": plan.total_chunk_count,
        "video_size": plan.video_size,
        "creator_username": creator_info.get("creator_username"),
    }


def upload_short_to_inbox(
    short_path: Path,
    token: str,
    *,
    chunk_size: int,
) -> dict[str, Any]:
    init_payload = init_inbox_upload(short_path, token, chunk_size=chunk_size)
    response = init_payload["response"]
    plan = init_payload["chunk_plan"]
    data = response["data"]
    upload_video_chunks(data["upload_url"], short_path, plan)
    return {
        "publish_id": data["publish_id"],
        "upload_url_received": True,
        "chunk_size": plan.chunk_size,
        "total_chunk_count": plan.total_chunk_count,
        "video_size": plan.video_size,
    }


def normalize_scopes(scope: str) -> set[str]:
    return {part.strip() for part in scope.replace(" ", ",").split(",") if part.strip()}


def choose_publish_mode(requested_mode: str, token_scopes: str) -> str:
    mode = requested_mode.strip().lower()
    if mode in {"direct", "upload"}:
        return mode
    scopes = normalize_scopes(token_scopes)
    if "video.publish" in scopes:
        return "direct"
    if "video.upload" in scopes:
        return "upload"
    return "direct"


def publish_or_upload_short(
    short_path: Path,
    token: str,
    *,
    mode: str,
    token_scopes: str,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    is_aigc: bool,
    chunk_size: int,
) -> dict[str, Any]:
    selected_mode = choose_publish_mode(mode, token_scopes)
    if selected_mode == "upload":
        result = upload_short_to_inbox(short_path, token, chunk_size=chunk_size)
        return {**result, "mode": "upload", "status": "uploaded_to_tiktok_inbox"}
    result = publish_short(
        short_path,
        token,
        title=title,
        privacy_level=privacy_level,
        disable_comment=disable_comment,
        disable_duet=disable_duet,
        disable_stitch=disable_stitch,
        is_aigc=is_aigc,
        chunk_size=chunk_size,
    )
    return {**result, "mode": "direct", "status": "uploaded_to_tiktok"}


def run(args: argparse.Namespace) -> int:
    token = args.access_token or os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token and not args.dry_run:
        try:
            token = get_valid_access_token()
        except RuntimeError:
            token = ""
    publish_state = load_json(args.publish_state_file, {"published": {}})
    published = publish_state.setdefault("published", {})

    youtube_state = load_state(args.youtube_state_file)
    ready_shorts = discover_ready_shorts(youtube_state.get("videos", {}))

    if not token and not args.dry_run:
        print("TikTok non configure: ajoute TIKTOK_ACCESS_TOKEN dans .env")
        print(f"Shorts prets a publier: {len(ready_shorts)}")
        return 2
    if not token and args.dry_run:
        print("TikTok non configure: dry-run seulement")

    limit = args.limit if args.limit and args.limit > 0 else len(ready_shorts)
    published_count = 0
    skipped_count = 0

    for video_id, record, short_path in ready_shorts:
        short_key = str(short_path.resolve())
        if short_key in published and not args.force:
            skipped_count += 1
            continue
        if published_count >= limit:
            break

        caption = args.caption or build_caption(record, args.caption_template)
        if args.dry_run:
            print(f"A publier: {short_path} | {caption}")
            continue

        print(f"Publication TikTok: {short_path}")
        try:
            result = publish_or_upload_short(
                short_path,
                token,
                mode=args.publish_mode,
                token_scopes=os.getenv("TIKTOK_SCOPES", ""),
                title=caption,
                privacy_level=args.privacy_level,
                disable_comment=args.disable_comment,
                disable_duet=args.disable_duet,
                disable_stitch=args.disable_stitch,
                is_aigc=args.is_aigc,
                chunk_size=args.chunk_size,
            )
            published[short_key] = {
                **result,
                "video_id": video_id,
                "caption": caption,
                "privacy_level": args.privacy_level,
                "status": result.get("status", "uploaded_to_tiktok"),
                "created_at": now_iso(),
            }
            save_json(args.publish_state_file, publish_state)
            published_count += 1
        except Exception as exc:
            published[short_key] = {
                "video_id": video_id,
                "caption": caption,
                "status": "failed",
                "error": str(exc),
                "created_at": now_iso(),
            }
            save_json(args.publish_state_file, publish_state)
            print(f"Erreur publication TikTok: {exc}")

    print("Resume TikTok")
    print(f"- shorts prets: {len(ready_shorts)}")
    print(f"- publies/uploades: {published_count}")
    print(f"- deja connus: {skipped_count}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publie les shorts rendus via l'API officielle TikTok.")
    parser.add_argument("--youtube-state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--publish-state-file", type=Path, default=DEFAULT_PUBLISH_STATE_FILE)
    parser.add_argument("--access-token", default="")
    parser.add_argument("--privacy-level", default=os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"))
    parser.add_argument("--caption", default="")
    parser.add_argument(
        "--caption-template",
        default=os.getenv("TIKTOK_CAPTION_TEMPLATE", "{title} #{niche} #fyp"),
    )
    parser.add_argument("--limit", type=int, default=int(os.getenv("TIKTOK_PUBLISH_LIMIT", "1")))
    parser.add_argument(
        "--publish-mode",
        choices=["auto", "direct", "upload"],
        default=os.getenv("TIKTOK_PUBLISH_MODE", "auto"),
        help="auto choisit Direct Post avec video.publish, sinon upload inbox avec video.upload.",
    )
    parser.add_argument("--chunk-size", type=int, default=int(os.getenv("TIKTOK_CHUNK_SIZE", str(DEFAULT_CHUNK_SIZE))))
    parser.add_argument("--disable-comment", action="store_true", default=os.getenv("TIKTOK_DISABLE_COMMENT", "0") == "1")
    parser.add_argument("--disable-duet", action="store_true", default=os.getenv("TIKTOK_DISABLE_DUET", "0") == "1")
    parser.add_argument("--disable-stitch", action="store_true", default=os.getenv("TIKTOK_DISABLE_STITCH", "0") == "1")
    parser.add_argument("--is-aigc", action="store_true", default=os.getenv("TIKTOK_IS_AIGC", "0") == "1")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    load_dotenv()
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
