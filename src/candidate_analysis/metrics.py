from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .schemas import Interval, WordTimestamp
from .windows import CandidateWindow


def _clipped_intervals(intervals: Iterable[Interval], window: CandidateWindow) -> list[Interval]:
    clipped = [
        Interval(max(item.start, window.start), min(item.end, window.end))
        for item in intervals
        if item.start < window.end and item.end > window.start
    ]
    return sorted(clipped, key=lambda item: (item.start, item.end))


def _merged_intervals(intervals: Iterable[Interval], window: CandidateWindow) -> list[Interval]:
    clipped = _clipped_intervals(intervals, window)
    if not clipped:
        return []
    merged: list[Interval] = []
    current_start, current_end = clipped[0].start, clipped[0].end
    for item in clipped[1:]:
        if item.start <= current_end:
            current_end = max(current_end, item.end)
        else:
            merged.append(Interval(current_start, current_end))
            current_start, current_end = item.start, item.end
    merged.append(Interval(current_start, current_end))
    return merged


def _merged_duration(intervals: Iterable[Interval], window: CandidateWindow) -> float:
    return sum(item.end - item.start for item in _merged_intervals(intervals, window))


def _normalize_words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", normalized, flags=re.UNICODE)


def words_in_window(words: Iterable[WordTimestamp], window: CandidateWindow) -> list[WordTimestamp]:
    return [word for word in words if window.start <= word.start < window.end]


def count_hesitations(tokens: list[str], expressions: Iterable[str]) -> int:
    normalized_expressions = sorted(
        {tuple(normalized) for expression in expressions if (normalized := _normalize_words(expression))},
        key=len,
        reverse=True,
    )
    count = 0
    index = 0
    while index < len(tokens):
        matched_width = next(
            (len(expression) for expression in normalized_expressions if tuple(tokens[index:index + len(expression)]) == expression),
            0,
        )
        if matched_width:
            count += 1
            index += matched_width
        else:
            index += 1
    return count


def calculate_metrics(
    window: CandidateWindow,
    silence_intervals: Iterable[Interval],
    speech_intervals: Iterable[Interval],
    words: Iterable[WordTimestamp],
    hesitation_expressions: Iterable[str],
) -> dict[str, float]:
    duration = window.duration
    if duration <= 0:
        raise ValueError("La duree du candidat doit etre positive.")

    merged_silences = _merged_intervals(silence_intervals, window)
    silence_seconds = sum(item.end - item.start for item in merged_silences)
    longest_silence = max((item.end - item.start for item in merged_silences), default=0.0)
    active_speech_seconds = _merged_duration(speech_intervals, window)
    selected_words = words_in_window(words, window)
    tokens = [token for item in selected_words for token in _normalize_words(item.word)]
    hesitation_count = count_hesitations(tokens, hesitation_expressions)

    speech_starts = [max(item.start, window.start) for item in speech_intervals if item.start < window.end and item.end > window.start]
    startup_latency = min(speech_starts) - window.start if speech_starts else duration

    return {
        "silence_ratio": silence_seconds / duration,
        "longest_silence_seconds": longest_silence,
        "speech_density": active_speech_seconds / duration,
        "words_per_minute": (len(tokens) / active_speech_seconds * 60.0) if active_speech_seconds > 0 else 0.0,
        "hesitation_ratio": (hesitation_count / len(tokens)) if tokens else 0.0,
        "startup_latency_seconds": startup_latency,
    }
