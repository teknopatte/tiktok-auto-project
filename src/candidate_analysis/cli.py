from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import analyze_video
from .schemas import AnalysisConfig


def parse_durations(value: str) -> tuple[float, ...]:
    try:
        durations = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Les durees doivent etre des nombres separes par des virgules.") from exc
    if not durations or any(item <= 0 for item in durations):
        raise argparse.ArgumentTypeError("Les durees doivent etre strictement positives.")
    return durations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyse locale objective de passages candidats video.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyse une video locale sans creer de clips.")
    analyze.add_argument("video", type=Path)
    analyze.add_argument("--step", type=float, default=3.0)
    analyze.add_argument("--durations", type=parse_durations, default=(60.0, 75.0, 90.0, 105.0, 120.0))
    analyze.add_argument("--output", type=Path, default=Path("analysis.json"))
    analyze.add_argument("--silence-threshold-db", type=float, default=-35.0)
    analyze.add_argument("--minimum-silence-duration", type=float, default=0.25)
    analyze.add_argument("--model", default="small")
    analyze.add_argument("--language", default="fr")
    analyze.add_argument("--device", default="auto")
    analyze.add_argument("--compute-type", default="default")
    analyze.add_argument("--hesitations", default="euh,heu,hum,hmm,bah,ben")
    analyze.add_argument("--cache-dir", type=Path, default=Path(".cache") / "candidate_analysis")
    analyze.add_argument("--no-cache", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AnalysisConfig(
        window_durations_seconds=args.durations,
        step_seconds=args.step,
        silence_threshold_db=args.silence_threshold_db,
        minimum_silence_duration_seconds=args.minimum_silence_duration,
        transcription_model=args.model,
        transcription_language=args.language,
        transcription_device=args.device,
        transcription_compute_type=args.compute_type,
        hesitation_expressions=tuple(item.strip() for item in args.hesitations.split(",") if item.strip()),
        cache_dir=args.cache_dir,
        use_cache=not args.no_cache,
    )
    result = analyze_video(args.video, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Analyse ecrite dans {args.output} ({len(result['candidates'])} candidats)")
    return 0
