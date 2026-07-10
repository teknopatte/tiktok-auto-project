from __future__ import annotations

from pathlib import Path
from typing import Callable

from .audio_analysis import detect_silences, probe_media
from .cache import cache_key, load_cached_analysis, save_cached_analysis
from .metrics import calculate_metrics
from .schemas import ANALYSIS_VERSION, AnalysisConfig, GlobalAnalysis, Interval, WordTimestamp
from .transcription import transcribe_video
from .windows import generate_windows


GlobalAnalyzer = Callable[[Path, AnalysisConfig], GlobalAnalysis]


def run_global_analysis(video_path: Path, config: AnalysisConfig) -> GlobalAnalysis:
    media = probe_media(video_path)
    silences = detect_silences(
        video_path,
        media.duration_seconds,
        threshold_db=config.silence_threshold_db,
        minimum_duration_seconds=config.minimum_silence_duration_seconds,
        has_audio=media.has_audio,
    )
    speech, words = transcribe_video(
        video_path,
        model_name=config.transcription_model,
        language=config.transcription_language,
        device=config.transcription_device,
        compute_type=config.transcription_compute_type,
        has_audio=media.has_audio,
    )
    return GlobalAnalysis(
        duration_seconds=media.duration_seconds,
        silence_intervals=tuple(silences),
        speech_intervals=tuple(speech),
        words=tuple(words),
        transcription_engine="faster-whisper" if media.has_audio else "none-no-audio-stream",
        transcription_model=config.transcription_model,
    )


def get_global_analysis(
    video_path: Path,
    config: AnalysisConfig,
    *,
    global_analyzer: GlobalAnalyzer = run_global_analysis,
) -> tuple[GlobalAnalysis, bool]:
    key = cache_key(video_path, config.global_cache_config())
    if config.use_cache:
        cached = load_cached_analysis(config.cache_dir, key)
        if cached is not None:
            return cached, True
    analysis = global_analyzer(video_path, config)
    if config.use_cache:
        save_cached_analysis(config.cache_dir, key, analysis)
    return analysis, False


def analyze_video(
    video_path: str | Path,
    config: AnalysisConfig | None = None,
    *,
    global_analyzer: GlobalAnalyzer = run_global_analysis,
) -> dict[str, object]:
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video locale introuvable: {path}")
    selected_config = config or AnalysisConfig()
    global_analysis, cache_hit = get_global_analysis(path, selected_config, global_analyzer=global_analyzer)
    windows = generate_windows(
        global_analysis.duration_seconds,
        selected_config.window_durations_seconds,
        selected_config.step_seconds,
    )
    candidates = []
    for index, window in enumerate(windows, start=1):
        candidates.append(
            {
                "candidate_id": f"clip_{index:04d}",
                "start_seconds": window.start,
                "end_seconds": window.end,
                "duration_seconds": window.duration,
                "metrics": calculate_metrics(
                    window,
                    global_analysis.silence_intervals,
                    global_analysis.speech_intervals,
                    global_analysis.words,
                    selected_config.hesitation_expressions,
                ),
            }
        )
    return {
        "source_video": str(path),
        "analysis_version": ANALYSIS_VERSION,
        "config": selected_config.output_config(),
        "global_analysis": {
            "duration_seconds": global_analysis.duration_seconds,
            "transcription_engine": global_analysis.transcription_engine,
            "transcription_model": global_analysis.transcription_model,
            "cache_hit": cache_hit,
        },
        "candidates": candidates,
    }
