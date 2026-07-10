from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import yt_dlp
except ImportError:  # pragma: no cover - exercised only in a missing dependency env
    yt_dlp = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANNELS_FILE = PROJECT_ROOT / "data" / "top_100_youtubeurs_fr_by_niche.tsv"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "downloads" / "youtube"
DEFAULT_STATE_FILE = PROJECT_ROOT / ".state" / "youtube_recent_downloader.json"
DEFAULT_SATISFYING_ROOT = PROJECT_ROOT / "videos_satisfaisantes"
DEFAULT_TIKTOK_PUBLISH_STATE_FILE = PROJECT_ROOT / ".state" / "tiktok_publish_state.json"


@dataclass(frozen=True)
class CreatorRow:
    rank: str
    channel: str
    niche: str
    channel_url: str | None = None


@dataclass(frozen=True)
class ChannelResolution:
    channel_url: str
    channel_id: str | None = None


@dataclass(frozen=True)
class VideoCandidate:
    video_id: str
    title: str
    url: str
    uploaded_at: datetime | None
    channel_name: str
    niche: str


@dataclass(frozen=True)
class DownloadResult:
    return_code: int
    video_path: Path | None = None


@dataclass(frozen=True)
class ClipResult:
    clips_dir: Path
    clips: list[Path]
    segment_seconds: int
    source_duration_seconds: float


@dataclass(frozen=True)
class RenderResult:
    shorts_dir: Path
    shorts: list[Path]
    layout: str
    satisfying_source: Path | None
    satisfying_sources: list[Path]


def slugify(value: str, fallback: str = "unknown") -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or fallback


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_path(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    path = Path(raw_value) if raw_value else default
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_value)


def parse_channel_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"youtube\.com/channel/([^/?#]+)", url)
    return match.group(1) if match else None


def parse_upload_datetime(entry: dict[str, Any]) -> datetime | None:
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    if timestamp:
        return datetime.fromtimestamp(int(timestamp), tz=UTC)

    upload_date = entry.get("upload_date")
    if upload_date and re.fullmatch(r"\d{8}", str(upload_date)):
        return datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=UTC)

    return None


def normalize_allowed_niches(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def load_creator_rows(path: Path, limit: int | None = None, allowed_niches: set[str] | None = None) -> list[CreatorRow]:
    rows: list[CreatorRow] = []
    allowed_niches = allowed_niches or set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for raw in reader:
            channel = (raw.get("channel") or "").strip()
            niche = (raw.get("niche_fr") or raw.get("niche") or "").strip()
            if not channel or not niche:
                continue
            if allowed_niches and niche not in allowed_niches:
                continue

            rows.append(
                CreatorRow(
                    rank=(raw.get("filtered_rank") or raw.get("rank") or "").strip(),
                    channel=channel,
                    niche=niche,
                    channel_url=(raw.get("channel_url") or "").strip() or None,
                )
            )
            if limit and len(rows) >= limit:
                break
    return rows


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"channels": {}, "downloaded_video_ids": [], "videos": {}}
    with path.open("r", encoding="utf-8") as file:
        state = json.load(file)
    state.setdefault("channels", {})
    state.setdefault("downloaded_video_ids", [])
    state.setdefault("videos", {})
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False, sort_keys=True)


def record_video_state(
    state: dict[str, Any],
    candidate: VideoCandidate,
    status: str,
    *,
    video_path: Path | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(tz=UTC).isoformat()
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "video_id": candidate.video_id,
            "title": candidate.title,
            "url": candidate.url,
            "channel": candidate.channel_name,
            "niche": candidate.niche,
            "uploaded_at": candidate.uploaded_at.isoformat() if candidate.uploaded_at else None,
            "status": status,
            "last_seen_at": now,
        }
    )
    if status == "downloaded":
        video_record["downloaded_at"] = now
    if video_path:
        video_record["downloaded_path"] = str(video_path)
    if error:
        video_record["error"] = error


def record_clip_state(
    state: dict[str, Any],
    candidate: VideoCandidate,
    clip_result: ClipResult,
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "clip_status": "split",
            "clips_dir": str(clip_result.clips_dir),
            "clips_count": len(clip_result.clips),
            "clip_segment_seconds": clip_result.segment_seconds,
            "source_duration_seconds": round(clip_result.source_duration_seconds, 3),
            "clips": [str(path) for path in clip_result.clips],
            "split_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def record_render_state(
    state: dict[str, Any],
    candidate: VideoCandidate,
    render_result: RenderResult,
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "render_status": "rendered",
            "shorts_dir": str(render_result.shorts_dir),
            "shorts_count": len(render_result.shorts),
            "shorts_layout": render_result.layout,
            "satisfying_source": str(render_result.satisfying_source)
            if render_result.satisfying_source
            else None,
            "satisfying_sources": [str(path) for path in render_result.satisfying_sources],
            "shorts": [str(path) for path in render_result.shorts],
            "rendered_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def record_render_error(
    state: dict[str, Any],
    candidate: VideoCandidate,
    error: str,
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "render_status": "failed",
            "render_error": error,
            "rendered_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def record_tiktok_publish_state(
    state: dict[str, Any],
    candidate: VideoCandidate,
    publish_records: list[dict[str, Any]],
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "tiktok_publish_status": "published" if publish_records else "not_published",
            "tiktok_publish_count": len(publish_records),
            "tiktok_publish_records": publish_records,
            "tiktok_published_at": datetime.now(tz=UTC).isoformat() if publish_records else None,
        }
    )


def record_tiktok_publish_error(
    state: dict[str, Any],
    candidate: VideoCandidate,
    error: str,
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "tiktok_publish_status": "failed",
            "tiktok_publish_error": error,
            "tiktok_published_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def record_pipeline_progress(
    state: dict[str, Any],
    candidate: VideoCandidate,
    *,
    stage: str,
    current_part: int | None = None,
    total_parts: int | None = None,
    message: str = "",
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "pipeline_stage": stage,
            "pipeline_current_part": current_part,
            "pipeline_total_parts": total_parts,
            "pipeline_message": message,
            "pipeline_updated_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop_at = stop_at.resolve()
    current = path.parent.resolve()
    while current != stop_at and stop_at in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def cleanup_generated_after_publish(
    state: dict[str, Any],
    candidate: VideoCandidate,
    *,
    source_video: Path | None,
    clip_result: ClipResult | None,
    render_result: RenderResult | None,
    output_root: Path,
) -> list[str]:
    removed: list[str] = []
    if source_video:
        source_video = source_video.resolve()
        if source_video.exists():
            for sibling in list(source_video.parent.iterdir()):
                if sibling.is_file() and sibling.stem.startswith(source_video.stem):
                    sibling.unlink()
                    removed.append(str(sibling))
        remove_empty_parents(source_video, output_root)

    for directory in (clip_result.clips_dir if clip_result else None, render_result.shorts_dir if render_result else None):
        if directory and directory.exists():
            shutil.rmtree(directory)
            removed.append(str(directory))

    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "cleanup_status": "cleaned" if removed else "nothing_to_clean",
            "cleanup_removed_paths": removed,
            "cleanup_at": datetime.now(tz=UTC).isoformat(),
        }
    )
    return removed


def record_cleanup_error(state: dict[str, Any], candidate: VideoCandidate, error: str) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "cleanup_status": "failed",
            "cleanup_error": error,
            "cleanup_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def record_clip_error(
    state: dict[str, Any],
    candidate: VideoCandidate,
    error: str,
) -> None:
    video_record = state.setdefault("videos", {}).setdefault(candidate.video_id, {})
    video_record.update(
        {
            "clip_status": "failed",
            "clip_error": error,
            "split_at": datetime.now(tz=UTC).isoformat(),
        }
    )


def ensure_yt_dlp_available() -> None:
    if yt_dlp is None:
        raise RuntimeError(
            "yt-dlp n'est pas installe. Lance: python -m pip install -r requirements.txt"
        )


def ydl_base_options(args: argparse.Namespace, *, quiet: bool = True) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": quiet,
        "no_warnings": quiet,
        "ignoreerrors": True,
        "skip_download": True,
    }
    if args.cookies_from_browser:
        options["cookiesfrombrowser"] = (args.cookies_from_browser,)
    return options


def pick_best_search_entry(entries: list[dict[str, Any]], channel_name: str) -> dict[str, Any] | None:
    expected = normalize_for_match(channel_name)
    best_score = -1
    best_entry: dict[str, Any] | None = None

    for entry in entries:
        uploader = normalize_for_match(str(entry.get("uploader") or entry.get("channel") or ""))
        title = normalize_for_match(str(entry.get("title") or ""))
        score = 0
        if expected and uploader:
            if expected == uploader:
                score += 100
            elif expected in uploader or uploader in expected:
                score += 75
        if expected and title and expected in title:
            score += 20
        if entry.get("channel_id") or entry.get("uploader_id"):
            score += 5

        if score > best_score:
            best_score = score
            best_entry = entry

    return best_entry


def resolve_channel_url(
    row: CreatorRow,
    state: dict[str, Any],
    args: argparse.Namespace,
) -> ChannelResolution | None:
    if row.channel_url:
        cleaned = row.channel_url.rstrip("/")
        return ChannelResolution(cleaned, parse_channel_id_from_url(cleaned))

    cached = state["channels"].get(row.channel, {})
    if cached.get("channel_url") and not args.force_resolve:
        cleaned = str(cached["channel_url"]).rstrip("/")
        channel_id = cached.get("channel_id") or parse_channel_id_from_url(cleaned)
        if channel_id and not cached.get("channel_id"):
            cached["channel_id"] = channel_id
        return ChannelResolution(cleaned, str(channel_id) if channel_id else None)

    ensure_yt_dlp_available()
    query = f"ytsearch5:{row.channel} chaine youtube officielle"
    with yt_dlp.YoutubeDL(ydl_base_options(args, quiet=True)) as ydl:
        result = ydl.extract_info(query, download=False)

    entries = [entry for entry in (result or {}).get("entries", []) if entry]
    picked = pick_best_search_entry(entries, row.channel)
    if not picked:
        return None

    channel_url = picked.get("channel_url") or picked.get("uploader_url")
    channel_id = picked.get("channel_id") or picked.get("uploader_id")
    if not channel_url and channel_id:
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
    if not channel_url:
        return None

    state["channels"][row.channel] = {
        "channel_url": str(channel_url).rstrip("/"),
        "channel_id": channel_id,
        "resolved_from": query,
        "resolved_uploader": picked.get("uploader") or picked.get("channel"),
        "resolved_at": datetime.now(tz=UTC).isoformat(),
    }
    return ChannelResolution(str(channel_url).rstrip("/"), str(channel_id) if channel_id else None)


def channel_videos_url(channel_url: str) -> str:
    cleaned = channel_url.rstrip("/")
    if cleaned.endswith("/videos"):
        return cleaned
    return f"{cleaned}/videos"


def parse_feed_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def fetch_feed_candidates(
    row: CreatorRow,
    channel_id: str,
    args: argparse.Namespace,
) -> list[VideoCandidate] | None:
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    request = urllib.request.Request(
        feed_url,
        headers={"User-Agent": "Mozilla/5.0 tiktok-auto-project/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            xml_data = response.read()
    except OSError:
        return None

    root = ET.fromstring(xml_data)
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    cutoff = datetime.now(tz=UTC) - timedelta(hours=args.since_hours)
    candidates: list[VideoCandidate] = []

    for entry in root.findall("atom:entry", namespaces)[: args.max_videos_per_channel]:
        video_id = entry.findtext("yt:videoId", namespaces=namespaces)
        title = entry.findtext("atom:title", namespaces=namespaces) or video_id or ""
        published = parse_feed_datetime(entry.findtext("atom:published", namespaces=namespaces))
        link = entry.find("atom:link", namespaces)
        url = link.attrib.get("href") if link is not None else None
        if not video_id:
            continue
        if published is None and not args.include_undated:
            continue
        if published is not None and published < cutoff:
            continue

        candidates.append(
            VideoCandidate(
                video_id=video_id,
                title=title,
                url=url or f"https://www.youtube.com/watch?v={video_id}",
                uploaded_at=published,
                channel_name=row.channel,
                niche=row.niche,
            )
        )

    return candidates


def fetch_recent_candidates(
    row: CreatorRow,
    resolution: ChannelResolution,
    args: argparse.Namespace,
) -> list[VideoCandidate]:
    if resolution.channel_id:
        feed_candidates = fetch_feed_candidates(row, resolution.channel_id, args)
        if feed_candidates is not None:
            return feed_candidates

    ensure_yt_dlp_available()
    options = ydl_base_options(args, quiet=True)
    options.update(
        {
            "extract_flat": "in_playlist",
            "playlistend": args.max_videos_per_channel,
        }
    )

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(channel_videos_url(resolution.channel_url), download=False)

    entries = [entry for entry in (info or {}).get("entries", []) if entry]
    cutoff = datetime.now(tz=UTC) - timedelta(hours=args.since_hours)
    candidates: list[VideoCandidate] = []
    metadata_options = ydl_base_options(args, quiet=True)

    for entry in entries:
        video_id = str(entry.get("id") or entry.get("url") or "").strip()
        if not video_id:
            continue

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        with yt_dlp.YoutubeDL(metadata_options) as ydl:
            detailed = ydl.extract_info(video_url, download=False) or entry

        uploaded_at = parse_upload_datetime(detailed)
        if uploaded_at is None and not args.include_undated:
            continue
        if uploaded_at is not None and uploaded_at < cutoff:
            continue

        candidates.append(
            VideoCandidate(
                video_id=video_id,
                title=str(detailed.get("title") or entry.get("title") or video_id),
                url=str(detailed.get("webpage_url") or video_url),
                uploaded_at=uploaded_at,
                channel_name=row.channel,
                niche=row.niche,
            )
        )

    return candidates


def fetch_video_candidate_from_url(video_url: str, args: argparse.Namespace) -> VideoCandidate:
    ensure_yt_dlp_available()
    with yt_dlp.YoutubeDL(ydl_base_options(args, quiet=True)) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info:
        raise RuntimeError("Impossible de lire les infos de cette video YouTube.")

    video_id = str(info.get("id") or "").strip()
    if not video_id:
        raise RuntimeError("Impossible de trouver l'id de cette video YouTube.")

    return VideoCandidate(
        video_id=video_id,
        title=str(info.get("title") or video_id),
        url=str(info.get("webpage_url") or video_url),
        uploaded_at=parse_upload_datetime(info),
        channel_name=str(info.get("uploader") or info.get("channel") or args.manual_channel),
        niche=str(args.manual_niche),
    )


def channel_output_dir(output_root: Path, candidate: VideoCandidate) -> Path:
    niche_dir = output_root / slugify(candidate.niche)
    channel_dir = niche_dir / slugify(candidate.channel_name)
    return channel_dir


def find_downloaded_video_file(channel_dir: Path, video_id: str) -> Path | None:
    video_suffixes = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    matches = [
        path
        for path in channel_dir.glob("*")
        if path.is_file()
        and f"[{video_id}]" in path.stem
        and path.suffix.lower() in video_suffixes
        and not path.name.endswith(".part")
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def download_video(candidate: VideoCandidate, output_root: Path, args: argparse.Namespace) -> DownloadResult:
    ensure_yt_dlp_available()
    channel_dir = channel_output_dir(output_root, candidate)
    channel_dir.mkdir(parents=True, exist_ok=True)

    options: dict[str, Any] = {
        "format": args.format,
        "merge_output_format": "mp4",
        "outtmpl": str(channel_dir / "%(upload_date)s - %(title).180s [%(id)s].%(ext)s"),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "writeinfojson": True,
        "writethumbnail": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "quiet": False,
        "no_warnings": False,
    }
    if args.cookies_from_browser:
        options["cookiesfrombrowser"] = (args.cookies_from_browser,)

    with yt_dlp.YoutubeDL(options) as ydl:
        result_code = int(ydl.download([candidate.url]) or 0)

    return DownloadResult(
        return_code=result_code,
        video_path=find_downloaded_video_file(channel_dir, candidate.video_id),
    )


def ensure_ffmpeg_available() -> None:
    missing = [binary for binary in ("ffmpeg", "ffprobe") if shutil.which(binary) is None]
    if missing:
        raise RuntimeError(
            "FFmpeg/FFprobe manque. Installe FFmpeg et ajoute-le au PATH: "
            + ", ".join(missing)
        )


def probe_duration_seconds(video_path: Path) -> float:
    ensure_ffmpeg_available()
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def clip_output_dir(video_path: Path, video_id: str) -> Path:
    safe_name = slugify(video_path.stem, fallback=video_id)
    return video_path.parent / "clips" / f"{safe_name}-{video_id}"


def shorts_output_dir(video_path: Path, video_id: str) -> Path:
    safe_name = slugify(video_path.stem, fallback=video_id)
    return video_path.parent / "shorts" / f"{safe_name}-{video_id}"


def split_video_into_segments(
    video_path: Path,
    video_id: str,
    segment_seconds: int,
) -> ClipResult:
    if segment_seconds <= 0:
        raise ValueError("La duree des segments doit etre positive.")

    duration = probe_duration_seconds(video_path)
    clips_dir = clip_output_dir(video_path, video_id)
    clips_dir.mkdir(parents=True, exist_ok=True)

    existing_clips = sorted(clips_dir.glob("part-*.mp4"))
    if existing_clips:
        return ClipResult(
            clips_dir=clips_dir,
            clips=existing_clips,
            segment_seconds=segment_seconds,
            source_duration_seconds=duration,
        )

    clips: list[Path] = []
    start = 0.0
    part_index = 1
    while start < duration:
        part_duration = min(float(segment_seconds), max(duration - start, 0.0))
        if part_duration <= 0:
            break

        output_path = clips_dir / f"part-{part_index:03d}.mp4"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{part_duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.run(command, check=True)
        clips.append(output_path)
        start += float(segment_seconds)
        part_index += 1

    return ClipResult(
        clips_dir=clips_dir,
        clips=clips,
        segment_seconds=segment_seconds,
        source_duration_seconds=duration,
    )


def segment_count_for_duration(duration: float, segment_seconds: int) -> int:
    if duration <= 0 or segment_seconds <= 0:
        return 0
    return int((duration + segment_seconds - 0.001) // segment_seconds)


def create_video_segment(
    video_path: Path,
    video_id: str,
    *,
    part_index: int,
    start_seconds: float,
    duration_seconds: float,
) -> Path:
    clips_dir = clip_output_dir(video_path, video_id)
    clips_dir.mkdir(parents=True, exist_ok=True)
    output_path = clips_dir / f"part-{part_index:03d}.mp4"
    if output_path.exists():
        return output_path
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{duration_seconds:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    return output_path


def find_satisfying_videos(root: Path) -> list[Path]:
    suffixes = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes)


def pick_satisfying_video(root: Path) -> Path | None:
    videos = find_satisfying_videos(root)
    if not videos:
        return None
    return random.SystemRandom().choice(videos)


def ffmpeg_escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def render_vertical_short(
    main_clip: Path,
    satisfying_video: Path,
    output_path: Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> None:
    ensure_ffmpeg_available()
    half_height = height // 2
    duration = probe_duration_seconds(main_clip)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    filter_complex = (
        f"[0:v]split=2[main][mainbg];"
        f"[mainbg]scale={width}:{half_height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{half_height},boxblur=24:1[mainblur];"
        f"[main]scale={width}:{half_height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{half_height}:(ow-iw)/2:(oh-ih)/2:color=black@0[mainfit];"
        f"[mainblur][mainfit]overlay=(W-w)/2:(H-h)/2[top];"
        f"[1:v]scale={width}:{half_height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{half_height},setsar=1[bottom];"
        f"[top][bottom]vstack=inputs=2[v]"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(main_clip),
        "-stream_loop",
        "-1",
        "-i",
        str(satisfying_video),
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def render_vertical_short_part(
    video_path: Path,
    video_id: str,
    part_index: int,
    main_clip: Path,
    satisfying_video: Path,
) -> Path:
    shorts_dir = shorts_output_dir(video_path, video_id)
    shorts_dir.mkdir(parents=True, exist_ok=True)
    output_path = shorts_dir / f"short-{part_index:03d}.mp4"
    if not output_path.exists():
        render_vertical_short(main_clip, satisfying_video, output_path)
    return output_path


def render_vertical_shorts(
    video_path: Path,
    video_id: str,
    clips: list[Path],
    satisfying_root: Path,
) -> RenderResult:
    satisfying_videos = find_satisfying_videos(satisfying_root)
    shorts_dir = shorts_output_dir(video_path, video_id)
    shorts_dir.mkdir(parents=True, exist_ok=True)

    if not satisfying_videos:
        return RenderResult(
            shorts_dir=shorts_dir,
            shorts=[],
            layout="vertical_split_top_main_bottom_satisfying",
            satisfying_source=None,
            satisfying_sources=[],
        )

    rendered: list[Path] = []
    chosen_sources: list[Path] = []
    chooser = random.SystemRandom()
    for index, clip in enumerate(clips, start=1):
        output_path = shorts_dir / f"short-{index:03d}.mp4"
        satisfying_video = chooser.choice(satisfying_videos)
        if not output_path.exists():
            render_vertical_short(clip, satisfying_video, output_path)
        rendered.append(output_path)
        chosen_sources.append(satisfying_video)

    return RenderResult(
        shorts_dir=shorts_dir,
        shorts=rendered,
        layout="vertical_split_top_main_bottom_satisfying",
        satisfying_source=chosen_sources[0] if chosen_sources else None,
        satisfying_sources=chosen_sources,
    )


def build_tiktok_caption(candidate: VideoCandidate, template: str) -> str:
    caption = template.format(
        title=candidate.title,
        channel=candidate.channel_name,
        niche=candidate.niche,
    ).strip()
    return caption[:2200]


def tiktok_publish_context(args: argparse.Namespace) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    try:
        from tiktok_oauth import get_valid_access_token, load_token
        from tiktok_publisher import load_json
    except ModuleNotFoundError:  # pragma: no cover - used when imported as src.youtube_recent_downloader
        from .tiktok_oauth import get_valid_access_token, load_token
        from .tiktok_publisher import load_json

    token = args.tiktok_access_token or os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        token = get_valid_access_token()
    token_payload = load_token()
    token_scopes = str(token_payload.get("scope") or os.getenv("TIKTOK_SCOPES", ""))
    publish_state = load_json(args.tiktok_publish_state_file, {"published": {}})
    published = publish_state.setdefault("published", {})
    return token, token_scopes, publish_state, published


def publish_single_short_to_tiktok(
    candidate: VideoCandidate,
    short_path: Path,
    args: argparse.Namespace,
    *,
    token: str,
    token_scopes: str,
    publish_state: dict[str, Any],
    published: dict[str, Any],
) -> dict[str, Any]:
    try:
        from tiktok_publisher import publish_or_upload_short, save_json
    except ModuleNotFoundError:  # pragma: no cover - used when imported as src.youtube_recent_downloader
        from .tiktok_publisher import publish_or_upload_short, save_json

    short_key = str(short_path.resolve())
    if short_key in published and not args.tiktok_force_publish:
        return {**published[short_key], "skipped": True}

    caption = build_tiktok_caption(candidate, args.tiktok_caption_template)
    result = publish_or_upload_short(
        short_path,
        token,
        mode=args.tiktok_publish_mode,
        token_scopes=token_scopes,
        title=caption,
        privacy_level=args.tiktok_privacy_level,
        disable_comment=args.tiktok_disable_comment,
        disable_duet=args.tiktok_disable_duet,
        disable_stitch=args.tiktok_disable_stitch,
        is_aigc=args.tiktok_is_aigc,
        chunk_size=args.tiktok_chunk_size,
    )
    record = {
        **result,
        "short_path": short_key,
        "video_id": candidate.video_id,
        "caption": caption,
        "privacy_level": args.tiktok_privacy_level,
        "status": result.get("status", "uploaded_to_tiktok"),
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    published[short_key] = record
    save_json(args.tiktok_publish_state_file, publish_state)
    return record


def publish_rendered_shorts_to_tiktok(
    candidate: VideoCandidate,
    shorts: list[Path],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    token, token_scopes, publish_state, published = tiktok_publish_context(args)
    records: list[dict[str, Any]] = []
    publish_candidates = shorts[: args.tiktok_publish_limit]
    delay_min = min(args.tiktok_publish_delay_min_seconds, args.tiktok_publish_delay_max_seconds)
    delay_max = max(args.tiktok_publish_delay_min_seconds, args.tiktok_publish_delay_max_seconds)

    for index, short_path in enumerate(publish_candidates):
        record = publish_single_short_to_tiktok(
            candidate,
            short_path,
            args,
            token=token,
            token_scopes=token_scopes,
            publish_state=publish_state,
            published=published,
        )
        records.append(record)
        remaining = any(
            str(path.resolve()) not in published or args.tiktok_force_publish
            for path in publish_candidates[index + 1 :]
        )
        if remaining:
            delay = random.randint(delay_min, delay_max)
            if delay > 0:
                print(f"  Attente avant prochain envoi TikTok: {delay}s")
                time.sleep(delay)

    return records


def print_candidate(prefix: str, candidate: VideoCandidate) -> None:
    uploaded = candidate.uploaded_at.isoformat() if candidate.uploaded_at else "date inconnue"
    print(
        f"{prefix} [{candidate.niche}] {candidate.channel_name} - "
        f"{candidate.title} ({uploaded}) {candidate.url}"
    )


def process_streaming_shorts(
    candidate: VideoCandidate,
    video_path: Path,
    args: argparse.Namespace,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    duration = probe_duration_seconds(video_path)
    total_parts = segment_count_for_duration(duration, args.clip_segment_seconds)
    clips_dir = clip_output_dir(video_path, candidate.video_id)
    shorts_dir = shorts_output_dir(video_path, candidate.video_id)
    satisfying_videos = find_satisfying_videos(args.satisfying_root)
    if not satisfying_videos:
        raise RuntimeError(f"Aucune video satisfying disponible dans {args.satisfying_root}")

    publish_limit = min(args.tiktok_publish_limit, total_parts) if args.auto_publish_tiktok else total_parts
    chooser = random.SystemRandom()
    token = token_scopes = ""
    publish_state: dict[str, Any] = {"published": {}}
    published: dict[str, Any] = publish_state["published"]
    if args.auto_publish_tiktok:
        token, token_scopes, publish_state, published = tiktok_publish_context(args)

    clips: list[Path] = []
    shorts: list[Path] = []
    satisfying_sources: list[Path] = []
    publish_records: list[dict[str, Any]] = []
    publish_failed = False
    delay_min = min(args.tiktok_publish_delay_min_seconds, args.tiktok_publish_delay_max_seconds)
    delay_max = max(args.tiktok_publish_delay_min_seconds, args.tiktok_publish_delay_max_seconds)

    for part_index in range(1, publish_limit + 1):
        start = float((part_index - 1) * args.clip_segment_seconds)
        part_duration = min(float(args.clip_segment_seconds), max(duration - start, 0.0))
        if part_duration <= 0:
            break

        record_pipeline_progress(
            state,
            candidate,
            stage="clipping",
            current_part=part_index,
            total_parts=publish_limit,
            message=f"Creation du fragment {part_index}/{publish_limit}",
        )
        save_state(args.state_file, state)
        clip_path = create_video_segment(
            video_path,
            candidate.video_id,
            part_index=part_index,
            start_seconds=start,
            duration_seconds=part_duration,
        )
        clips.append(clip_path)
        record_clip_state(
            state,
            candidate,
            ClipResult(
                clips_dir=clips_dir,
                clips=clips,
                segment_seconds=args.clip_segment_seconds,
                source_duration_seconds=duration,
            ),
        )
        save_state(args.state_file, state)

        record_pipeline_progress(
            state,
            candidate,
            stage="rendering",
            current_part=part_index,
            total_parts=publish_limit,
            message=f"Rendu vertical {part_index}/{publish_limit}",
        )
        save_state(args.state_file, state)
        satisfying_video = chooser.choice(satisfying_videos)
        short_path = render_vertical_short_part(
            video_path,
            candidate.video_id,
            part_index,
            clip_path,
            satisfying_video,
        )
        shorts.append(short_path)
        satisfying_sources.append(satisfying_video)
        record_render_state(
            state,
            candidate,
            RenderResult(
                shorts_dir=shorts_dir,
                shorts=shorts,
                layout="vertical_split_top_main_bottom_satisfying",
                satisfying_source=satisfying_sources[0],
                satisfying_sources=satisfying_sources,
            ),
        )
        save_state(args.state_file, state)

        if args.auto_publish_tiktok:
            record_pipeline_progress(
                state,
                candidate,
                stage="publishing",
                current_part=part_index,
                total_parts=publish_limit,
                message=f"Publication TikTok {part_index}/{publish_limit}",
            )
            save_state(args.state_file, state)
            try:
                record = publish_single_short_to_tiktok(
                    candidate,
                    short_path,
                    args,
                    token=token,
                    token_scopes=token_scopes,
                    publish_state=publish_state,
                    published=published,
                )
            except Exception as publish_exc:
                error = str(publish_exc)
                record_tiktok_publish_error(state, candidate, error)
                record_pipeline_progress(
                    state,
                    candidate,
                    stage="tiktok_failed",
                    current_part=part_index,
                    total_parts=publish_limit,
                    message=error,
                )
                save_state(args.state_file, state)
                print(f"  Echec publication TikTok: {publish_exc}", file=sys.stderr)
                publish_failed = True
                break
            publish_records.append(record)
            record_tiktok_publish_state(state, candidate, publish_records)
            save_state(args.state_file, state)

            if args.cleanup_after_publish and not args.keep_published_shorts:
                try:
                    clip_path.unlink(missing_ok=True)
                    short_path.unlink(missing_ok=True)
                except OSError as exc:
                    record_cleanup_error(state, candidate, str(exc))
                    save_state(args.state_file, state)

            if part_index < publish_limit:
                delay = random.randint(delay_min, delay_max)
                record_pipeline_progress(
                    state,
                    candidate,
                    stage="waiting",
                    current_part=part_index,
                    total_parts=publish_limit,
                    message=f"Attente {delay}s avant le prochain short",
                )
                save_state(args.state_file, state)
                if delay > 0:
                    print(f"  Attente avant prochain envoi TikTok: {delay}s")
                    time.sleep(delay)

    if not publish_failed:
        record_pipeline_progress(
            state,
            candidate,
            stage="completed",
            current_part=publish_limit,
            total_parts=publish_limit,
            message="Pipeline termine",
        )
    if args.auto_publish_tiktok and publish_records and args.cleanup_after_publish:
        try:
            cleanup_generated_after_publish(
                state,
                candidate,
                source_video=video_path,
                clip_result=ClipResult(clips_dir, [], args.clip_segment_seconds, duration),
                render_result=RenderResult(shorts_dir, [], "vertical_split_top_main_bottom_satisfying", None, []),
                output_root=args.output_root,
            )
        except Exception as exc:
            record_cleanup_error(state, candidate, str(exc))
    save_state(args.state_file, state)
    return publish_records


def process_download_candidate(
    candidate: VideoCandidate,
    args: argparse.Namespace,
    state: dict[str, Any],
    downloaded_ids: set[str],
) -> str:
    if candidate.video_id in downloaded_ids:
        print_candidate("  Deja telecharge:", candidate)
        record_video_state(state, candidate, "already_downloaded")
        save_state(args.state_file, state)
        return "skipped"

    if args.dry_run:
        print_candidate("  A telecharger:", candidate)
        record_video_state(state, candidate, "detected")
        save_state(args.state_file, state)
        return "detected"

    print_candidate("  Telechargement:", candidate)
    download_result = download_video(candidate, args.output_root, args)
    if download_result.return_code != 0:
        error = f"Echec yt-dlp, code: {download_result.return_code}"
        record_video_state(state, candidate, "failed", error=error)
        save_state(args.state_file, state)
        print(f"  {error}", file=sys.stderr)
        return "failed"

    downloaded_ids.add(candidate.video_id)
    state["downloaded_video_ids"] = sorted(downloaded_ids)
    record_video_state(state, candidate, "downloaded", video_path=download_result.video_path)

    if download_result.video_path and not args.skip_split:
        try:
            if args.auto_publish_tiktok and not args.skip_vertical_render:
                print("  Pipeline optimise: clip -> rendu -> TikTok, short par short")
                process_streaming_shorts(candidate, download_result.video_path, args, state)
                save_state(args.state_file, state)
                return "downloaded"

            print(
                "  Decoupage en parties de "
                f"{args.clip_segment_seconds}s: {download_result.video_path}"
            )
            clip_result = split_video_into_segments(
                download_result.video_path,
                candidate.video_id,
                args.clip_segment_seconds,
            )
            record_clip_state(state, candidate, clip_result)
            print(f"  Clips crees: {len(clip_result.clips)} dans {clip_result.clips_dir}")
            if not args.skip_vertical_render:
                render_result = render_vertical_shorts(
                    download_result.video_path,
                    candidate.video_id,
                    clip_result.clips,
                    args.satisfying_root,
                )
                if render_result.satisfying_source is None:
                    print(
                        "  Aucun fond satisfying trouve. Ajoute des videos dans: "
                        f"{args.satisfying_root}"
                    )
                    record_render_error(state, candidate, "Aucune video satisfying disponible")
                else:
                    record_render_state(state, candidate, render_result)
                    print(
                        "  Shorts verticaux crees: "
                        f"{len(render_result.shorts)} dans {render_result.shorts_dir}"
                    )
                    if args.auto_publish_tiktok:
                        print("  Publication automatique TikTok...")
                        try:
                            publish_records = publish_rendered_shorts_to_tiktok(
                                candidate,
                                render_result.shorts,
                                args,
                            )
                            record_tiktok_publish_state(state, candidate, publish_records)
                            print(f"  Shorts envoyes a TikTok: {len(publish_records)}")
                            if publish_records and args.cleanup_after_publish:
                                try:
                                    removed = cleanup_generated_after_publish(
                                        state,
                                        candidate,
                                        source_video=download_result.video_path,
                                        clip_result=clip_result,
                                        render_result=render_result,
                                        output_root=args.output_root,
                                    )
                                    print(f"  Nettoyage stockage: {len(removed)} elements supprimes")
                                except Exception as cleanup_exc:
                                    record_cleanup_error(state, candidate, str(cleanup_exc))
                                    print(f"  Nettoyage stockage echoue: {cleanup_exc}", file=sys.stderr)
                        except Exception as publish_exc:
                            record_tiktok_publish_error(state, candidate, str(publish_exc))
                            print(f"  Echec publication TikTok: {publish_exc}", file=sys.stderr)
        except Exception as exc:
            error = f"Echec decoupage/rendu video: {exc}"
            record_clip_error(state, candidate, error)
            record_render_error(state, candidate, error)
            print(f"  {error}", file=sys.stderr)
    elif not download_result.video_path:
        error = "Fichier video telecharge introuvable pour le decoupage"
        record_clip_error(state, candidate, error)
        print(f"  {error}", file=sys.stderr)

    save_state(args.state_file, state)
    return "downloaded"


def run_single_video(args: argparse.Namespace, state: dict[str, Any], downloaded_ids: set[str]) -> int:
    candidate = fetch_video_candidate_from_url(args.video_url, args)
    print("Mode lien YouTube manuel")
    print(f"Stockage videos: {args.output_root}")
    state["channels"].setdefault(candidate.channel_name, {})
    state["channels"][candidate.channel_name].update(
        {
            "niche": candidate.niche,
            "last_checked_at": datetime.now(tz=UTC).isoformat(),
            "last_result": "manual_video",
            "last_video_id": candidate.video_id,
            "last_video_title": candidate.title,
            "last_video_url": candidate.url,
            "last_video_uploaded_at": candidate.uploaded_at.isoformat()
            if candidate.uploaded_at
            else None,
        }
    )
    status = process_download_candidate(candidate, args, state, downloaded_ids)
    print("\nResume")
    print("- videos candidates: 1")
    print(f"- statut: {status}")
    if args.dry_run:
        print("- mode dry-run: aucun fichier video n'a ete telecharge")
    save_state(args.state_file, state)
    return 0


def run(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    downloaded_ids = set(state.get("downloaded_video_ids", []))
    if args.video_url:
        return run_single_video(args, state, downloaded_ids)

    allowed_niches = normalize_allowed_niches(args.allowed_niches)
    rows = load_creator_rows(args.channels_file, limit=args.limit, allowed_niches=allowed_niches)

    print(f"Createurs charges: {len(rows)}")
    if allowed_niches:
        print(f"Niches autorisees: {', '.join(sorted(allowed_niches))}")
    print(f"Fenetre de verification: dernieres {args.since_hours}h")
    print(f"Stockage videos: {args.output_root}")

    total_candidates = 0
    total_downloaded = 0
    total_skipped = 0
    total_unresolved = 0

    for index, row in enumerate(rows, start=1):
        print(f"\n[{index}/{len(rows)}] {row.channel} ({row.niche})")
        try:
            resolution = resolve_channel_url(row, state, args)
            if not resolution:
                print("  Impossible de trouver l'URL de chaine automatiquement.")
                total_unresolved += 1
                continue

            candidates = fetch_recent_candidates(row, resolution, args)
            if not candidates:
                print("  Rien de recent trouve.")
                state["channels"].setdefault(row.channel, {})
                state["channels"][row.channel].update(
                    {
                        "niche": row.niche,
                        "last_checked_at": datetime.now(tz=UTC).isoformat(),
                        "last_result": "no_recent_video",
                    }
                )
                save_state(args.state_file, state)
                continue

            for candidate in candidates:
                state["channels"].setdefault(row.channel, {})
                state["channels"][row.channel].update(
                    {
                        "niche": row.niche,
                        "last_checked_at": datetime.now(tz=UTC).isoformat(),
                        "last_result": "recent_video_found",
                        "last_video_id": candidate.video_id,
                        "last_video_title": candidate.title,
                        "last_video_url": candidate.url,
                        "last_video_uploaded_at": candidate.uploaded_at.isoformat()
                        if candidate.uploaded_at
                        else None,
                    }
                )

                if candidate.video_id in downloaded_ids:
                    print_candidate("  Deja telecharge:", candidate)
                    record_video_state(state, candidate, "already_downloaded")
                    save_state(args.state_file, state)
                    total_skipped += 1
                    continue

                total_candidates += 1
                if args.dry_run:
                    print_candidate("  A telecharger:", candidate)
                    record_video_state(state, candidate, "detected")
                    save_state(args.state_file, state)
                    continue

                print_candidate("  Telechargement:", candidate)
                download_result = download_video(candidate, args.output_root, args)
                if download_result.return_code == 0:
                    downloaded_ids.add(candidate.video_id)
                    state["downloaded_video_ids"] = sorted(downloaded_ids)
                    record_video_state(state, candidate, "downloaded", video_path=download_result.video_path)
                    if download_result.video_path and not args.skip_split:
                        try:
                            if args.auto_publish_tiktok and not args.skip_vertical_render:
                                print("  Pipeline optimise: clip -> rendu -> TikTok, short par short")
                                publish_records = process_streaming_shorts(
                                    candidate,
                                    download_result.video_path,
                                    args,
                                    state,
                                )
                                print(
                                    "  Shorts envoyes a TikTok: "
                                    f"{len(publish_records)}"
                                )
                                total_downloaded += 1
                                save_state(args.state_file, state)
                                continue

                            print(
                                "  Decoupage en parties de "
                                f"{args.clip_segment_seconds}s: {download_result.video_path}"
                            )
                            clip_result = split_video_into_segments(
                                download_result.video_path,
                                candidate.video_id,
                                args.clip_segment_seconds,
                            )
                            record_clip_state(state, candidate, clip_result)
                            print(
                                "  Clips crees: "
                                f"{len(clip_result.clips)} dans {clip_result.clips_dir}"
                            )
                            if not args.skip_vertical_render:
                                render_result = render_vertical_shorts(
                                    download_result.video_path,
                                    candidate.video_id,
                                    clip_result.clips,
                                    args.satisfying_root,
                                )
                                if render_result.satisfying_source is None:
                                    print(
                                        "  Aucun fond satisfying trouve. Ajoute des videos dans: "
                                        f"{args.satisfying_root}"
                                    )
                                    record_render_error(
                                        state,
                                        candidate,
                                        "Aucune video satisfying disponible",
                                    )
                                else:
                                    record_render_state(state, candidate, render_result)
                                    print(
                                        "  Shorts verticaux crees: "
                                        f"{len(render_result.shorts)} dans {render_result.shorts_dir}"
                                    )
                                    if args.auto_publish_tiktok:
                                        print("  Publication automatique TikTok...")
                                        try:
                                            publish_records = publish_rendered_shorts_to_tiktok(
                                                candidate,
                                                render_result.shorts,
                                                args,
                                            )
                                            record_tiktok_publish_state(
                                                state,
                                                candidate,
                                                publish_records,
                                            )
                                            print(
                                                "  Shorts envoyes a TikTok: "
                                                f"{len(publish_records)}"
                                            )
                                            if publish_records and args.cleanup_after_publish:
                                                try:
                                                    removed = cleanup_generated_after_publish(
                                                        state,
                                                        candidate,
                                                        source_video=download_result.video_path,
                                                        clip_result=clip_result,
                                                        render_result=render_result,
                                                        output_root=args.output_root,
                                                    )
                                                    print(
                                                        "  Nettoyage stockage: "
                                                        f"{len(removed)} elements supprimes"
                                                    )
                                                except Exception as cleanup_exc:
                                                    record_cleanup_error(state, candidate, str(cleanup_exc))
                                                    print(
                                                        f"  Nettoyage stockage echoue: {cleanup_exc}",
                                                        file=sys.stderr,
                                                    )
                                        except Exception as publish_exc:
                                            record_tiktok_publish_error(state, candidate, str(publish_exc))
                                            print(f"  Echec publication TikTok: {publish_exc}", file=sys.stderr)
                        except Exception as exc:
                            error = f"Echec decoupage/rendu video: {exc}"
                            record_clip_error(state, candidate, error)
                            record_render_error(state, candidate, error)
                            print(f"  {error}", file=sys.stderr)
                    elif not download_result.video_path:
                        error = "Fichier video telecharge introuvable pour le decoupage"
                        record_clip_error(state, candidate, error)
                        print(f"  {error}", file=sys.stderr)
                    total_downloaded += 1
                    save_state(args.state_file, state)
                else:
                    error = f"Echec yt-dlp, code: {download_result.return_code}"
                    record_video_state(state, candidate, "failed", error=error)
                    print(f"  {error}", file=sys.stderr)

            state["channels"].setdefault(row.channel, {})
            state["channels"][row.channel].update(
                {
                    "niche": row.niche,
                    "last_checked_at": datetime.now(tz=UTC).isoformat(),
                }
            )
            save_state(args.state_file, state)

            if args.sleep_seconds:
                time.sleep(args.sleep_seconds)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            state["channels"].setdefault(row.channel, {})
            state["channels"][row.channel].update(
                {
                    "niche": row.niche,
                    "last_checked_at": datetime.now(tz=UTC).isoformat(),
                    "last_result": "error",
                    "last_error": str(exc),
                }
            )
            save_state(args.state_file, state)
            print(f"  Erreur: {exc}", file=sys.stderr)

    print("\nResume")
    print(f"- videos candidates: {total_candidates}")
    print(f"- videos telechargees: {total_downloaded}")
    print(f"- videos deja connues: {total_skipped}")
    print(f"- chaines non resolues: {total_unresolved}")
    if args.dry_run:
        print("- mode dry-run: aucun fichier video n'a ete telecharge")

    save_state(args.state_file, state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verifie les dernieres videos des youtubeurs FR listes, "
            "telecharge les nouvelles videos et les classe par niche."
        )
    )
    parser.add_argument(
        "--channels-file",
        type=Path,
        default=DEFAULT_CHANNELS_FILE,
        help="TSV source avec au minimum les colonnes channel et niche_fr.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=env_path("VIDEO_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT),
        help="Dossier de stockage local. Peut pointer vers un dossier Drive synchronise.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=env_path("YOUTUBE_DOWNLOADER_STATE", DEFAULT_STATE_FILE),
        help="Fichier JSON local pour le cache des chaines et videos deja telechargees.",
    )
    parser.add_argument(
        "--video-url",
        default="",
        help="Traite une video YouTube precise au lieu de scanner les chaines.",
    )
    parser.add_argument(
        "--manual-channel",
        default="Lien manuel",
        help="Nom de chaine fallback pour --video-url.",
    )
    parser.add_argument(
        "--manual-niche",
        default="Manuel",
        help="Niche utilisee pour classer une video fournie par lien.",
    )
    parser.add_argument(
        "--allowed-niches",
        default=os.getenv("YOUTUBE_ALLOWED_NICHES", ""),
        help="Liste de niches separees par des virgules pour limiter le scan automatique.",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=int(os.getenv("YOUTUBE_SINCE_HOURS", "24")),
        help="Telecharge seulement les videos sorties dans cette fenetre.",
    )
    parser.add_argument(
        "--max-videos-per-channel",
        type=int,
        default=int(os.getenv("YOUTUBE_MAX_VIDEOS_PER_CHANNEL", "1")),
        help="Nombre de dernieres videos a verifier par chaine.",
    )
    parser.add_argument(
        "--format",
        default=os.getenv("YOUTUBE_DOWNLOAD_FORMAT", "bv*[height<=1080]+ba/b[height<=1080]/b"),
        help="Format yt-dlp. Par defaut: meilleure video jusqu'a 1080p + audio.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        choices=["brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi"],
        default=os.getenv("YOUTUBE_COOKIES_FROM_BROWSER") or None,
        help="Optionnel: reutilise les cookies d'un navigateur si YouTube bloque l'acces.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite le nombre de chaines pour tester.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=float(os.getenv("YOUTUBE_SLEEP_SECONDS", "0")),
        help="Pause entre deux chaines pour eviter de taper trop vite sur YouTube.",
    )
    parser.add_argument(
        "--include-undated",
        action="store_true",
        help="Inclut une video si yt-dlp ne donne pas sa date d'upload.",
    )
    parser.add_argument(
        "--force-resolve",
        action="store_true",
        help="Force la recherche des URLs de chaines meme si elles sont deja en cache.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche ce qui serait telecharge sans telecharger les videos.",
    )
    parser.add_argument(
        "--clip-segment-seconds",
        type=int,
        default=int(os.getenv("CLIP_SEGMENT_SECONDS", "60")),
        help="Duree cible de chaque partie creee apres telechargement.",
    )
    parser.add_argument(
        "--satisfying-root",
        type=Path,
        default=env_path("SATISFYING_VIDEO_ROOT", DEFAULT_SATISFYING_ROOT),
        help="Dossier contenant les videos satisfying/Trackmania pour le rendu vertical.",
    )
    parser.add_argument(
        "--skip-split",
        action="store_true",
        help="Telecharge la video sans la decouper en parties.",
    )
    parser.add_argument(
        "--skip-vertical-render",
        action="store_true",
        help="Decoupe les clips sans creer les versions verticales avec video satisfying.",
    )
    parser.add_argument(
        "--auto-publish-tiktok",
        action="store_true",
        default=os.getenv("TIKTOK_AUTO_PUBLISH", "0") == "1",
        help="Publie automatiquement les shorts rendus via l'API officielle TikTok.",
    )
    parser.add_argument(
        "--tiktok-access-token",
        default=os.getenv("TIKTOK_ACCESS_TOKEN", ""),
        help="Access token TikTok. Sinon le token OAuth local est utilise.",
    )
    parser.add_argument(
        "--tiktok-publish-state-file",
        type=Path,
        default=env_path("TIKTOK_PUBLISH_STATE", DEFAULT_TIKTOK_PUBLISH_STATE_FILE),
    )
    parser.add_argument(
        "--tiktok-privacy-level",
        default=os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY"),
        help="Privacy level TikTok. Doit etre autorise par creator_info/query.",
    )
    parser.add_argument(
        "--tiktok-caption-template",
        default=os.getenv("TIKTOK_CAPTION_TEMPLATE", "{title} #{niche} #fyp"),
    )
    parser.add_argument(
        "--tiktok-publish-limit",
        type=int,
        default=int(os.getenv("TIKTOK_PUBLISH_LIMIT", "1")),
        help="Nombre max de shorts a publier par video source.",
    )
    parser.add_argument(
        "--tiktok-publish-mode",
        choices=["auto", "direct", "upload"],
        default=os.getenv("TIKTOK_PUBLISH_MODE", "auto"),
        help="auto utilise Direct Post avec video.publish, sinon upload inbox avec video.upload.",
    )
    parser.add_argument(
        "--tiktok-publish-delay-min-seconds",
        type=int,
        default=int(os.getenv("TIKTOK_PUBLISH_DELAY_MIN_SECONDS", "600")),
    )
    parser.add_argument(
        "--tiktok-publish-delay-max-seconds",
        type=int,
        default=int(os.getenv("TIKTOK_PUBLISH_DELAY_MAX_SECONDS", "1200")),
    )
    parser.add_argument(
        "--tiktok-chunk-size",
        type=int,
        default=int(os.getenv("TIKTOK_CHUNK_SIZE", str(16 * 1024 * 1024))),
    )
    parser.add_argument("--tiktok-disable-comment", action="store_true")
    parser.add_argument("--tiktok-disable-duet", action="store_true")
    parser.add_argument("--tiktok-disable-stitch", action="store_true")
    parser.add_argument("--tiktok-is-aigc", action="store_true")
    parser.add_argument("--tiktok-force-publish", action="store_true")
    parser.add_argument(
        "--keep-files-after-publish",
        dest="cleanup_after_publish",
        action="store_false",
        default=os.getenv("TIKTOK_CLEANUP_AFTER_PUBLISH", "1") != "0",
        help="Garde les fichiers source/clips/shorts apres envoi TikTok.",
    )
    parser.add_argument(
        "--keep-published-shorts",
        action="store_true",
        default=os.getenv("TIKTOK_KEEP_PUBLISHED_SHORTS", "0") == "1",
        help="Ne supprime pas chaque clip/short juste apres sa publication.",
    )
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
