from __future__ import annotations

from pathlib import Path

from .schemas import Interval, WordTimestamp


def transcribe_video(
    video_path: Path,
    *,
    model_name: str,
    language: str,
    device: str,
    compute_type: str,
    has_audio: bool,
) -> tuple[tuple[Interval, ...], tuple[WordTimestamp, ...]]:
    if not has_audio:
        return (), ()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper est requis pour la transcription locale. "
            "Installe les dependances avec: python -m pip install -r requirements-analysis.txt"
        ) from exc

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _ = model.transcribe(
        str(video_path),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
    )
    speech: list[Interval] = []
    words: list[WordTimestamp] = []
    for segment in segments:
        start = max(0.0, float(segment.start))
        end = max(start, float(segment.end))
        if end > start:
            speech.append(Interval(start, end))
        for item in segment.words or ():
            word_start = max(0.0, float(item.start))
            word_end = max(word_start, float(item.end))
            text = str(item.word).strip()
            if text:
                words.append(WordTimestamp(text, word_start, word_end))
    return tuple(speech), tuple(words)
