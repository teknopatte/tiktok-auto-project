from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from tiktok_oauth import (
        build_authorization_url,
        disconnect as disconnect_tiktok,
        exchange_code_for_token,
        public_status as tiktok_public_status,
        verify_state,
    )
    from youtube_recent_downloader import (
        DEFAULT_CHANNELS_FILE,
        DEFAULT_OUTPUT_ROOT,
        DEFAULT_SATISFYING_ROOT,
        DEFAULT_STATE_FILE,
        PROJECT_ROOT,
        load_creator_rows,
        load_dotenv,
        load_state,
        save_state,
    )
except ModuleNotFoundError:  # pragma: no cover - used when imported as src.control_app
    from .tiktok_oauth import (
        build_authorization_url,
        disconnect as disconnect_tiktok,
        exchange_code_for_token,
        public_status as tiktok_public_status,
        verify_state,
    )
    from .youtube_recent_downloader import (
        DEFAULT_CHANNELS_FILE,
        DEFAULT_OUTPUT_ROOT,
        DEFAULT_SATISFYING_ROOT,
        DEFAULT_STATE_FILE,
        PROJECT_ROOT,
        load_creator_rows,
        load_dotenv,
        load_state,
        save_state,
    )


WEB_ROOT = PROJECT_ROOT / "web"
FEATURES_FILE = PROJECT_ROOT / "data" / "features.json"
JOB_STATE_FILE = PROJECT_ROOT / ".state" / "control_app_job.json"
LOOP_STATE_FILE = PROJECT_ROOT / ".state" / "control_app_loop.json"
ANALYSIS_OUTPUT_FILE = PROJECT_ROOT / ".state" / "candidate_analysis" / "latest.json"
ANALYSIS_CACHE_DIR = PROJECT_ROOT / ".cache" / "candidate_analysis"
SCRIPT_FILE = PROJECT_ROOT / "src" / "youtube_recent_downloader.py"
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

JOB_LOCK = threading.Lock()
CURRENT_JOB: dict[str, Any] = {
    "id": None,
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "command": [],
    "returncode": None,
    "logs": [],
}
CURRENT_PROCESS: subprocess.Popen[str] | None = None
LOOP_LOCK = threading.Lock()
DEFAULT_LOOP_PAYLOAD: dict[str, Any] = {
    "dryRun": True,
    "sinceHours": 24,
    "maxVideosPerChannel": 1,
    "limit": "",
    "cookiesFromBrowser": "",
    "outputRoot": "",
    "includeUndated": False,
    "forceResolve": False,
    "clipSegmentSeconds": 60,
    "skipSplit": False,
    "satisfyingRoot": "videos_satisfaisantes",
    "skipVerticalRender": False,
    "autoPublishTikTok": False,
    "tiktokPrivacyLevel": "SELF_ONLY",
    "tiktokCaptionTemplate": "{title} #{niche} #fyp",
    "tiktokPublishLimit": 1,
    "tiktokPublishDelayMinSeconds": 600,
    "tiktokPublishDelayMaxSeconds": 1200,
    "allowedNiches": ["Divertissement pur", "Gaming"],
}
LOOP_STATE: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 60,
    "next_run_at": None,
    "last_run_at": None,
    "last_message": "Boucle inactive",
    "payload": DEFAULT_LOOP_PAYLOAD.copy(),
}


def now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def read_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def get_output_root() -> Path:
    raw = os.getenv("VIDEO_OUTPUT_ROOT")
    if not raw:
        return DEFAULT_OUTPUT_ROOT
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            try:
                total += file.stat().st_size
            except OSError:
                continue
    return total


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{size} B"


def load_features() -> list[dict[str, Any]]:
    return read_json_file(FEATURES_FILE, [])


def count_video_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for file in path.rglob("*") if file.is_file() and file.suffix.lower() in VIDEO_SUFFIXES)


def publish_record_views(record: dict[str, Any]) -> int:
    for key in ("view_count", "views", "play_count", "video_views"):
        try:
            return int(record.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return 0


def get_loop_state() -> dict[str, Any]:
    with LOOP_LOCK:
        return json.loads(json.dumps(LOOP_STATE))


def summarize_dashboard() -> dict[str, Any]:
    rows = load_creator_rows(DEFAULT_CHANNELS_FILE)
    state = load_state(DEFAULT_STATE_FILE)
    channels_state = state.get("channels", {})
    videos_state = state.get("videos", {})
    output_root = get_output_root()

    niches = sorted({row.niche for row in rows})
    checked_channels = sum(1 for row in rows if channels_state.get(row.channel, {}).get("last_checked_at"))
    downloaded_ids = set(state.get("downloaded_video_ids", []))
    recent_videos = sorted(
        videos_state.values(),
        key=lambda item: item.get("last_seen_at") or item.get("uploaded_at") or "",
        reverse=True,
    )
    detected_videos = [
        video
        for video in recent_videos
        if video.get("status") in {"detected", "downloaded", "already_downloaded", "failed"}
    ]
    clips_generated = sum(int(video.get("clips_count") or 0) for video in videos_state.values())
    shorts_generated = sum(int(video.get("shorts_count") or 0) for video in videos_state.values())
    tiktok_published = sum(int(video.get("tiktok_publish_count") or 0) for video in videos_state.values())
    tiktok_views = sum(
        publish_record_views(record)
        for video in videos_state.values()
        for record in video.get("tiktok_publish_records", [])
        if isinstance(record, dict)
    )

    channel_rows = []
    for row in rows:
        channel_state = channels_state.get(row.channel, {})
        video_id = channel_state.get("last_video_id")
        video_state = videos_state.get(video_id, {}) if video_id else {}
        channel_rows.append(
            {
                "rank": row.rank,
                "channel": row.channel,
                "niche": row.niche,
                "last_checked_at": channel_state.get("last_checked_at"),
                "last_result": channel_state.get("last_result", "not_checked"),
                "last_error": channel_state.get("last_error"),
                "last_video_id": video_id,
                "last_video_title": channel_state.get("last_video_title"),
                "last_video_url": channel_state.get("last_video_url"),
                "last_video_uploaded_at": channel_state.get("last_video_uploaded_at"),
                "video_status": video_state.get("status"),
            }
        )

    stats_by_niche: dict[str, dict[str, Any]] = {
        niche: {"niche": niche, "channels": 0, "checked": 0, "videos": 0, "downloaded": 0}
        for niche in niches
    }
    for channel in channel_rows:
        stats_by_niche[channel["niche"]]["channels"] += 1
        if channel["last_checked_at"]:
            stats_by_niche[channel["niche"]]["checked"] += 1
    for video in videos_state.values():
        niche = video.get("niche") or "unknown"
        stats_by_niche.setdefault(
            niche, {"niche": niche, "channels": 0, "checked": 0, "videos": 0, "downloaded": 0}
        )
        stats_by_niche[niche]["videos"] += 1
        if video.get("status") in {"downloaded", "already_downloaded"}:
            stats_by_niche[niche]["downloaded"] += 1

    with JOB_LOCK:
        job = dict(CURRENT_JOB)
        job["logs"] = CURRENT_JOB.get("logs", [])[-250:]

    tiktok_status = tiktok_public_status()

    return {
        "generated_at": now_iso(),
        "stats": {
            "channels_total": len(rows),
            "niches_total": len(niches),
            "channels_checked": checked_channels,
            "videos_detected": len(detected_videos),
            "videos_downloaded": len(downloaded_ids),
            "clips_generated": clips_generated,
            "shorts_generated": shorts_generated,
            "tiktok_published": tiktok_published,
            "tiktok_views": tiktok_views,
            "tiktok_configured": bool(
                os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
                or tiktok_status.get("connected")
                or tiktok_status.get("configured")
            ),
            "tiktok_connected": bool(tiktok_status.get("connected")),
            "satisfying_videos": count_video_files(DEFAULT_SATISFYING_ROOT),
            "storage_path": str(output_root),
            "storage_size": format_bytes(directory_size(output_root)),
        },
        "tiktok": tiktok_status,
        "channels": channel_rows,
        "videos": recent_videos[:200],
        "niches": sorted(stats_by_niche.values(), key=lambda item: item["niche"]),
        "features": load_features(),
        "job": job,
        "candidate_analysis": analysis_summary(),
        "automation": get_loop_state(),
    }


def safe_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def safe_float(
    value: Any,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def parse_analysis_durations(value: Any) -> tuple[float, ...]:
    raw_items = value if isinstance(value, list) else str(value or "").split(",")
    try:
        durations = tuple(float(str(item).strip()) for item in raw_items if str(item).strip())
    except ValueError as exc:
        raise ValueError("Les durees doivent etre des nombres separes par des virgules.") from exc
    if not durations:
        raise ValueError("Indique au moins une duree candidate.")
    if len(durations) > 10 or any(duration < 1 or duration > 600 for duration in durations):
        raise ValueError("Chaque duree doit etre comprise entre 1 et 600 secondes (10 maximum).")
    return durations


def build_analysis_command(payload: dict[str, Any]) -> list[str]:
    raw_path = str(payload.get("videoPath") or "").strip()
    if not raw_path:
        raise ValueError("Choisis une video locale a analyser.")
    video_path = Path(raw_path).expanduser()
    if not video_path.is_absolute():
        video_path = PROJECT_ROOT / video_path
    video_path = video_path.resolve()
    if not video_path.is_file():
        raise ValueError(f"Video introuvable: {video_path}")
    if video_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError("Format video non pris en charge.")

    durations = parse_analysis_durations(payload.get("durations") or "60,75,90,105,120")
    step = safe_float(payload.get("step"), 3.0, minimum=0.1, maximum=600.0)
    silence_threshold = safe_float(
        payload.get("silenceThresholdDb"),
        -35.0,
        minimum=-100.0,
        maximum=0.0,
    )
    model = str(payload.get("model") or "tiny").strip()
    if not model or len(model) > 200:
        raise ValueError("Modele de transcription invalide.")
    device = str(payload.get("device") or "cpu").strip().lower()
    if device not in {"auto", "cpu", "cuda"}:
        raise ValueError("Device invalide: auto, cpu ou cuda attendu.")
    compute_type = str(payload.get("computeType") or "int8").strip().lower()
    allowed_compute_types = {"default", "auto", "int8", "int8_float16", "int8_float32", "float16", "float32"}
    if compute_type not in allowed_compute_types:
        raise ValueError("Type de calcul invalide.")

    return [
        sys.executable,
        "-m",
        "src.candidate_analysis",
        "analyze",
        str(video_path),
        "--step",
        f"{step:g}",
        "--durations",
        ",".join(f"{duration:g}" for duration in durations),
        "--silence-threshold-db",
        f"{silence_threshold:g}",
        "--model",
        model,
        "--language",
        str(payload.get("language") or "fr").strip() or "fr",
        "--device",
        device,
        "--compute-type",
        compute_type,
        "--cache-dir",
        str(ANALYSIS_CACHE_DIR),
        "--output",
        str(ANALYSIS_OUTPUT_FILE),
    ]


def analysis_summary() -> dict[str, Any]:
    payload = read_json_file(ANALYSIS_OUTPUT_FILE, None)
    if not isinstance(payload, dict):
        return {"available": False, "candidate_count": 0}
    candidates = payload.get("candidates")
    return {
        "available": isinstance(candidates, list),
        "source_video": payload.get("source_video"),
        "analysis_version": payload.get("analysis_version"),
        "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
        "config": payload.get("config", {}),
        "global_analysis": payload.get("global_analysis", {}),
        "updated_at": datetime.fromtimestamp(ANALYSIS_OUTPUT_FILE.stat().st_mtime, tz=UTC).isoformat(),
    }


def load_analysis_page(offset: int = 0, limit: int = 100) -> dict[str, Any]:
    payload = read_json_file(ANALYSIS_OUTPUT_FILE, None)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return {"ok": False, "error": "Aucune analyse disponible."}
    candidates = payload["candidates"]
    safe_offset = max(0, offset)
    safe_limit = max(1, min(200, limit))
    return {
        "ok": True,
        "source_video": payload.get("source_video"),
        "analysis_version": payload.get("analysis_version"),
        "config": payload.get("config", {}),
        "global_analysis": payload.get("global_analysis", {}),
        "candidates": candidates[safe_offset:safe_offset + safe_limit],
        "pagination": {
            "offset": safe_offset,
            "limit": safe_limit,
            "total": len(candidates),
            "has_more": safe_offset + safe_limit < len(candidates),
        },
    }


def build_download_command(payload: dict[str, Any]) -> list[str]:
    command = [sys.executable, str(SCRIPT_FILE)]
    if payload.get("dryRun", True):
        command.append("--dry-run")

    video_url = str(payload.get("videoUrl") or "").strip()
    if video_url:
        command.extend(["--video-url", video_url])
        manual_channel = str(payload.get("manualChannel") or "Lien manuel").strip()
        manual_niche = str(payload.get("manualNiche") or "Manuel").strip()
        command.extend(["--manual-channel", manual_channel])
        command.extend(["--manual-niche", manual_niche])

    since_hours = safe_int(payload.get("sinceHours"), 24, minimum=1, maximum=24 * 365)
    max_videos = safe_int(payload.get("maxVideosPerChannel"), 1, minimum=1, maximum=10)
    command.extend(["--since-hours", str(since_hours)])
    command.extend(["--max-videos-per-channel", str(max_videos)])

    limit = payload.get("limit")
    if str(limit).strip():
        command.extend(["--limit", str(safe_int(limit, 0, minimum=1, maximum=100))])

    allowed_niches = payload.get("allowedNiches")
    if isinstance(allowed_niches, list):
        allowed_niches_value = ",".join(str(niche).strip() for niche in allowed_niches if str(niche).strip())
    else:
        allowed_niches_value = str(allowed_niches or "").strip()
    if allowed_niches_value:
        command.extend(["--allowed-niches", allowed_niches_value])

    output_root = str(payload.get("outputRoot") or "").strip()
    if output_root:
        command.extend(["--output-root", output_root])

    cookies = str(payload.get("cookiesFromBrowser") or "").strip()
    if cookies:
        command.extend(["--cookies-from-browser", cookies])

    if payload.get("includeUndated"):
        command.append("--include-undated")
    if payload.get("forceResolve"):
        command.append("--force-resolve")
    if payload.get("skipSplit"):
        command.append("--skip-split")
    if payload.get("skipVerticalRender"):
        command.append("--skip-vertical-render")

    clip_segment_seconds = safe_int(payload.get("clipSegmentSeconds"), 60, minimum=10, maximum=600)
    command.extend(["--clip-segment-seconds", str(clip_segment_seconds)])
    satisfying_root = str(payload.get("satisfyingRoot") or "").strip()
    if satisfying_root:
        command.extend(["--satisfying-root", satisfying_root])
    if payload.get("autoPublishTikTok"):
        command.append("--auto-publish-tiktok")
    tiktok_privacy = str(payload.get("tiktokPrivacyLevel") or "SELF_ONLY").strip()
    command.extend(["--tiktok-privacy-level", tiktok_privacy])
    tiktok_caption_template = str(payload.get("tiktokCaptionTemplate") or "{title} #{niche} #fyp").strip()
    command.extend(["--tiktok-caption-template", tiktok_caption_template])
    tiktok_publish_limit = safe_int(payload.get("tiktokPublishLimit"), 1, minimum=1, maximum=100)
    command.extend(["--tiktok-publish-limit", str(tiktok_publish_limit)])
    delay_min = safe_int(payload.get("tiktokPublishDelayMinSeconds"), 600, minimum=0, maximum=24 * 60 * 60)
    delay_max = safe_int(payload.get("tiktokPublishDelayMaxSeconds"), 1200, minimum=0, maximum=24 * 60 * 60)
    command.extend(["--tiktok-publish-delay-min-seconds", str(delay_min)])
    command.extend(["--tiktok-publish-delay-max-seconds", str(delay_max)])

    return command


def build_satisfying_command(payload: dict[str, Any]) -> list[str]:
    video_url = str(payload.get("videoUrl") or "").strip()
    if not video_url:
        raise ValueError("Lien YouTube manquant.")

    output_root = DEFAULT_SATISFYING_ROOT
    output_root.mkdir(parents=True, exist_ok=True)
    output_template = str(output_root / "%(title).180s [%(id)s].%(ext)s")
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
        video_url,
    ]
    cookies = str(payload.get("cookiesFromBrowser") or "").strip()
    if cookies:
        command.extend(["--cookies-from-browser", cookies])
    return command


def folder_target(key: str) -> Path:
    targets = {
        "downloads": get_output_root(),
        "satisfying": DEFAULT_SATISFYING_ROOT,
        "project": PROJECT_ROOT,
        "state": PROJECT_ROOT / ".state",
    }
    if key not in targets:
        raise ValueError("Dossier inconnu.")
    return targets[key]


def open_folder(payload: dict[str, Any]) -> dict[str, Any]:
    key = str(payload.get("folder") or "").strip()
    path = folder_target(key).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"ok": True, "path": str(path)}


def delete_path_if_inside_workspace(path_value: str) -> str | None:
    if not path_value:
        return None
    path = Path(path_value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if PROJECT_ROOT.resolve() not in (resolved, *resolved.parents):
        raise ValueError("Chemin hors projet refuse.")
    if resolved.is_dir():
        import shutil

        shutil.rmtree(resolved)
        return str(resolved)
    if resolved.is_file():
        resolved.unlink()
        return str(resolved)
    return None


def cleanup_failed_video(payload: dict[str, Any]) -> dict[str, Any]:
    state = load_state(DEFAULT_STATE_FILE)
    videos = state.get("videos", {})
    requested_video_id = str(payload.get("videoId") or "").strip()
    if requested_video_id and requested_video_id in videos:
        video_id = requested_video_id
        record = videos[video_id]
    else:
        failed = [
            (video_id, record)
            for video_id, record in videos.items()
            if record.get("tiktok_publish_status") == "failed"
            or record.get("cleanup_status") == "failed"
            or record.get("render_status") == "failed"
        ]
        if not failed:
            return {"ok": False, "error": "Aucune video echouee a nettoyer."}
        video_id, record = max(failed, key=lambda item: item[1].get("pipeline_updated_at") or item[1].get("last_seen_at") or "")

    removed: list[str] = []
    for key in ("clips_dir", "shorts_dir", "downloaded_path"):
        removed_path = delete_path_if_inside_workspace(str(record.get(key) or ""))
        if removed_path:
            removed.append(removed_path)
    downloaded_path = str(record.get("downloaded_path") or "")
    if downloaded_path:
        source = Path(downloaded_path)
        if not source.is_absolute():
            source = PROJECT_ROOT / source
        for suffix in (".info.json", ".webp", ".jpg", ".png"):
            removed_path = delete_path_if_inside_workspace(str(source.with_suffix(suffix)))
            if removed_path:
                removed.append(removed_path)

    record["cleanup_status"] = "manual_cleaned"
    record["cleanup_removed_paths"] = removed
    record["cleanup_at"] = now_iso()
    save_state(DEFAULT_STATE_FILE, state)
    return {"ok": True, "videoId": video_id, "removed": removed}


def append_job_log(line: str) -> None:
    with JOB_LOCK:
        logs = CURRENT_JOB.setdefault("logs", [])
        logs.append(line.rstrip())
        if len(logs) > 500:
            CURRENT_JOB["logs"] = logs[-500:]
        write_json_file(JOB_STATE_FILE, CURRENT_JOB)


def run_job(command: list[str], job_id: str, source: str = "manual") -> None:
    global CURRENT_PROCESS
    with JOB_LOCK:
        CURRENT_JOB.update(
            {
                "id": job_id,
                "status": "running",
                "started_at": now_iso(),
                "finished_at": None,
                "command": command,
                "returncode": None,
                "logs": [],
                "source": source,
            }
        )
        write_json_file(JOB_STATE_FILE, CURRENT_JOB)

    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        CURRENT_PROCESS = process
        assert process.stdout is not None
        for line in process.stdout:
            append_job_log(line)
        returncode = process.wait()
        with JOB_LOCK:
            CURRENT_JOB["status"] = "completed" if returncode == 0 else "failed"
            CURRENT_JOB["returncode"] = returncode
            CURRENT_JOB["finished_at"] = now_iso()
            write_json_file(JOB_STATE_FILE, CURRENT_JOB)
    except Exception as exc:
        append_job_log(f"Erreur app: {exc}")
        with JOB_LOCK:
            CURRENT_JOB["status"] = "failed"
            CURRENT_JOB["returncode"] = -1
            CURRENT_JOB["finished_at"] = now_iso()
            write_json_file(JOB_STATE_FILE, CURRENT_JOB)
    finally:
        CURRENT_PROCESS = None


def start_job(payload: dict[str, Any], source: str = "manual") -> dict[str, Any]:
    with JOB_LOCK:
        if CURRENT_JOB.get("status") in {"running", "stopping"}:
            return {"ok": False, "error": "Un job est deja en cours."}

    command = build_download_command(payload)
    job_id = str(int(time.time()))
    thread = threading.Thread(target=run_job, args=(command, job_id, source), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def start_satisfying_job(payload: dict[str, Any]) -> dict[str, Any]:
    with JOB_LOCK:
        if CURRENT_JOB.get("status") in {"running", "stopping"}:
            return {"ok": False, "error": "Un job est deja en cours."}

    try:
        command = build_satisfying_command(payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    job_id = str(int(time.time()))
    thread = threading.Thread(target=run_job, args=(command, job_id, "satisfying"), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def start_analysis_job(payload: dict[str, Any]) -> dict[str, Any]:
    with JOB_LOCK:
        if CURRENT_JOB.get("status") in {"running", "stopping"}:
            return {"ok": False, "error": "Un job est deja en cours."}

    try:
        command = build_analysis_command(payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    job_id = str(time.time_ns())
    thread = threading.Thread(target=run_job, args=(command, job_id, "analysis"), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def stop_job() -> dict[str, Any]:
    global CURRENT_PROCESS
    with JOB_LOCK:
        process = CURRENT_PROCESS
        if not process or CURRENT_JOB.get("status") != "running":
            return {"ok": False, "error": "Aucun job en cours."}
        CURRENT_JOB["status"] = "stopping"
        write_json_file(JOB_STATE_FILE, CURRENT_JOB)
    process.terminate()
    return {"ok": True}


def save_loop_state() -> None:
    with LOOP_LOCK:
        write_json_file(LOOP_STATE_FILE, LOOP_STATE)


def update_automation(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled"))
    interval_minutes = safe_int(payload.get("intervalMinutes"), 60, minimum=5, maximum=24 * 60)
    run_payload = {
        "dryRun": bool(payload.get("dryRun", True)),
        "sinceHours": safe_int(payload.get("sinceHours"), 24, minimum=1, maximum=24 * 365),
        "maxVideosPerChannel": safe_int(payload.get("maxVideosPerChannel"), 1, minimum=1, maximum=10),
        "limit": str(payload.get("limit") or "").strip(),
        "cookiesFromBrowser": str(payload.get("cookiesFromBrowser") or "").strip(),
        "outputRoot": str(payload.get("outputRoot") or "").strip(),
        "includeUndated": bool(payload.get("includeUndated")),
        "forceResolve": bool(payload.get("forceResolve")),
        "clipSegmentSeconds": safe_int(payload.get("clipSegmentSeconds"), 60, minimum=10, maximum=600),
        "skipSplit": bool(payload.get("skipSplit")),
        "satisfyingRoot": str(payload.get("satisfyingRoot") or "").strip(),
        "skipVerticalRender": bool(payload.get("skipVerticalRender")),
        "autoPublishTikTok": bool(payload.get("autoPublishTikTok")),
        "tiktokPrivacyLevel": str(payload.get("tiktokPrivacyLevel") or "SELF_ONLY").strip(),
        "tiktokCaptionTemplate": str(payload.get("tiktokCaptionTemplate") or "{title} #{niche} #fyp").strip(),
        "tiktokPublishLimit": safe_int(payload.get("tiktokPublishLimit"), 1, minimum=1, maximum=100),
        "tiktokPublishDelayMinSeconds": safe_int(
            payload.get("tiktokPublishDelayMinSeconds"),
            600,
            minimum=0,
            maximum=24 * 60 * 60,
        ),
        "tiktokPublishDelayMaxSeconds": safe_int(
            payload.get("tiktokPublishDelayMaxSeconds"),
            1200,
            minimum=0,
            maximum=24 * 60 * 60,
        ),
        "allowedNiches": payload.get("allowedNiches") or ["Divertissement pur", "Gaming"],
    }

    with LOOP_LOCK:
        was_enabled = bool(LOOP_STATE.get("enabled"))
        LOOP_STATE.update(
            {
                "enabled": enabled,
                "interval_minutes": interval_minutes,
                "payload": run_payload,
                "last_message": "Boucle active" if enabled else "Boucle inactive",
            }
        )
        if enabled and not was_enabled:
            LOOP_STATE["next_run_at"] = datetime.now(tz=UTC).isoformat()
        elif enabled and not LOOP_STATE.get("next_run_at"):
            LOOP_STATE["next_run_at"] = (
                datetime.now(tz=UTC) + timedelta(minutes=interval_minutes)
            ).isoformat()
        elif not enabled:
            LOOP_STATE["next_run_at"] = None
        write_json_file(LOOP_STATE_FILE, LOOP_STATE)

    return {"ok": True, "automation": get_loop_state()}


def load_loop_state() -> None:
    previous = read_json_file(LOOP_STATE_FILE, None)
    if isinstance(previous, dict):
        with LOOP_LOCK:
            LOOP_STATE.update(previous)
            LOOP_STATE["payload"] = {**DEFAULT_LOOP_PAYLOAD, **previous.get("payload", {})}
            if LOOP_STATE.get("enabled") and not LOOP_STATE.get("next_run_at"):
                LOOP_STATE["next_run_at"] = datetime.now(tz=UTC).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def automation_loop() -> None:
    while True:
        time.sleep(2)
        with LOOP_LOCK:
            enabled = bool(LOOP_STATE.get("enabled"))
            next_run_at = parse_iso(LOOP_STATE.get("next_run_at"))
            payload = dict(LOOP_STATE.get("payload", {}))
            interval_minutes = safe_int(LOOP_STATE.get("interval_minutes"), 60, minimum=5)

        if not enabled:
            continue
        if next_run_at and datetime.now(tz=UTC) < next_run_at:
            continue

        with JOB_LOCK:
            busy = CURRENT_JOB.get("status") in {"running", "stopping"}
        if busy:
            with LOOP_LOCK:
                LOOP_STATE["last_message"] = "Job en cours, prochain passage decale"
                LOOP_STATE["next_run_at"] = (
                    datetime.now(tz=UTC) + timedelta(minutes=5)
                ).isoformat()
                write_json_file(LOOP_STATE_FILE, LOOP_STATE)
            continue

        result = start_job(payload, source="loop")
        with LOOP_LOCK:
            LOOP_STATE["last_run_at"] = datetime.now(tz=UTC).isoformat()
            LOOP_STATE["next_run_at"] = (
                datetime.now(tz=UTC) + timedelta(minutes=interval_minutes)
            ).isoformat()
            LOOP_STATE["last_message"] = (
                "Scan automatique lance" if result.get("ok") else str(result.get("error"))
            )
            write_json_file(LOOP_STATE_FILE, LOOP_STATE)


class ControlAppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect_to(self, url: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self.send_json(summarize_dashboard())
            return
        if parsed.path == "/api/job":
            with JOB_LOCK:
                self.send_json(CURRENT_JOB)
            return
        if parsed.path == "/api/automation":
            self.send_json(get_loop_state())
            return
        if parsed.path == "/api/tiktok/status":
            self.send_json(tiktok_public_status())
            return
        if parsed.path == "/api/analysis/latest":
            query = urllib.parse.parse_qs(parsed.query)
            offset = safe_int(query.get("offset", [0])[0], 0, minimum=0)
            limit = safe_int(query.get("limit", [100])[0], 100, minimum=1, maximum=200)
            result = load_analysis_page(offset, limit)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND
            self.send_json(result, status)
            return
        if parsed.path == "/api/tiktok/connect":
            try:
                self.redirect_to(build_authorization_url())
            except ValueError as exc:
                self.send_html(
                    f"<h1>Configuration TikTok incomplete</h1><p>{exc}</p><p>Ajoute TIKTOK_CLIENT_KEY et TIKTOK_CLIENT_SECRET dans .env.</p>",
                    HTTPStatus.BAD_REQUEST,
                )
            return
        if parsed.path == "/tiktok/callback":
            query = urllib.parse.parse_qs(parsed.query)
            error = query.get("error", [""])[0]
            if error:
                description = query.get("error_description", [""])[0]
                self.send_html(
                    f"<h1>Connexion TikTok refusee</h1><p>{error}</p><p>{description}</p><p><a href='/'>Retour dashboard</a></p>",
                    HTTPStatus.BAD_REQUEST,
                )
                return
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if not code or not verify_state(state):
                self.send_html(
                    "<h1>Connexion TikTok invalide</h1><p>State OAuth invalide ou expire.</p><p><a href='/'>Retour dashboard</a></p>",
                    HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                token = exchange_code_for_token(code)
            except Exception as exc:
                self.send_html(
                    f"<h1>Erreur OAuth TikTok</h1><p>{exc}</p><p><a href='/'>Retour dashboard</a></p>",
                    HTTPStatus.BAD_REQUEST,
                )
                return
            scopes = token.get("scope", "")
            self.send_html(
                f"<h1>TikTok connecte</h1><p>Scopes: {scopes}</p><p><a href='/'>Retour dashboard</a></p>"
            )
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/jobs":
                self.send_json(start_job(self.read_payload()))
                return
            if parsed.path == "/api/satisfying-jobs":
                self.send_json(start_satisfying_job(self.read_payload()))
                return
            if parsed.path == "/api/analysis-jobs":
                self.send_json(start_analysis_job(self.read_payload()))
                return
            if parsed.path == "/api/jobs/stop":
                self.send_json(stop_job())
                return
            if parsed.path == "/api/automation":
                self.send_json(update_automation(self.read_payload()))
                return
            if parsed.path == "/api/tiktok/disconnect":
                disconnect_tiktok()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/folders/open":
                self.send_json(open_folder(self.read_payload()))
                return
            if parsed.path == "/api/cleanup-failed":
                self.send_json(cleanup_failed_video(self.read_payload()))
                return
            self.send_json({"ok": False, "error": "Route inconnue."}, HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "JSON invalide."}, HTTPStatus.BAD_REQUEST)


def load_previous_job() -> None:
    previous = read_json_file(JOB_STATE_FILE, None)
    if isinstance(previous, dict):
        with JOB_LOCK:
            CURRENT_JOB.update(previous)
            if CURRENT_JOB.get("status") in {"running", "stopping"}:
                CURRENT_JOB["status"] = "interrupted"
                CURRENT_JOB["finished_at"] = now_iso()


def main() -> int:
    load_dotenv()
    load_previous_job()
    load_loop_state()
    threading.Thread(target=automation_loop, daemon=True).start()
    host = os.getenv("CONTROL_APP_HOST", "127.0.0.1")
    port = int(os.getenv("CONTROL_APP_PORT", "8787"))
    server = ThreadingHTTPServer((host, port), ControlAppHandler)
    print(f"TikTok Auto Project app: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
