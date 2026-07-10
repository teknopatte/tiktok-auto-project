from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schemas import ANALYSIS_VERSION, GlobalAnalysis


def source_signature(video_path: Path) -> dict[str, Any]:
    resolved = video_path.resolve()
    stat = resolved.stat()
    return {"path": str(resolved), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def cache_key(video_path: Path, global_config: dict[str, Any]) -> str:
    identity = {
        "analysis_version": ANALYSIS_VERSION,
        "source": source_signature(video_path),
        "global_config": global_config,
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_cached_analysis(cache_dir: Path, key: str) -> GlobalAnalysis | None:
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("analysis_version") != ANALYSIS_VERSION:
            return None
        return GlobalAnalysis.from_dict(payload["global_analysis"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_cached_analysis(cache_dir: Path, key: str, analysis: GlobalAnalysis) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{key}.json"
    temporary = path.with_suffix(".tmp")
    payload = {"analysis_version": ANALYSIS_VERSION, "global_analysis": analysis.to_dict()}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path
