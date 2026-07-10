from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .schemas import Interval


@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_audio: bool


def ensure_ffmpeg_available() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError("FFmpeg/FFprobe absent du PATH: " + ", ".join(missing))


def probe_media(video_path: Path) -> MediaInfo:
    ensure_ffmpeg_available()
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
            "-of", "json", str(video_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    payload = json.loads(result.stdout or "{}")
    duration = float(payload.get("format", {}).get("duration", 0.0))
    if duration <= 0:
        raise ValueError(f"Duree video invalide ou introuvable: {video_path}")
    has_audio = any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))
    return MediaInfo(duration_seconds=duration, has_audio=has_audio)


SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.+-]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.+-]+)")


def parse_silencedetect_output(output: str, duration_seconds: float) -> tuple[Interval, ...]:
    intervals: list[Interval] = []
    open_start: float | None = None
    for line in output.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            open_start = max(0.0, float(start_match.group(1)))
        end_match = SILENCE_END_RE.search(line)
        if end_match and open_start is not None:
            end = min(duration_seconds, float(end_match.group(1)))
            if end > open_start:
                intervals.append(Interval(open_start, end))
            open_start = None
    if open_start is not None and duration_seconds > open_start:
        intervals.append(Interval(open_start, duration_seconds))
    return tuple(intervals)


def detect_silences(
    video_path: Path,
    duration_seconds: float,
    *,
    threshold_db: float,
    minimum_duration_seconds: float,
    has_audio: bool,
) -> tuple[Interval, ...]:
    if not has_audio:
        return (Interval(0.0, duration_seconds),)
    filter_value = f"silencedetect=noise={threshold_db:g}dB:d={minimum_duration_seconds:g}"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path), "-af", filter_value, "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Echec FFmpeg silencedetect: {(result.stderr or result.stdout or '').strip()}")
    return parse_silencedetect_output((result.stderr or "") + "\n" + (result.stdout or ""), duration_seconds)
