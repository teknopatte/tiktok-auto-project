from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ANALYSIS_VERSION = "1.0"


@dataclass(frozen=True)
class Interval:
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Intervalle invalide: {self.start} -> {self.end}")


@dataclass(frozen=True)
class WordTimestamp:
    word: str
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Timestamp de mot invalide: {self.start} -> {self.end}")


@dataclass(frozen=True)
class AnalysisConfig:
    window_durations_seconds: tuple[float, ...] = (60.0, 75.0, 90.0, 105.0, 120.0)
    step_seconds: float = 3.0
    silence_threshold_db: float = -35.0
    minimum_silence_duration_seconds: float = 0.25
    transcription_model: str = "small"
    transcription_language: str = "fr"
    transcription_device: str = "auto"
    transcription_compute_type: str = "default"
    hesitation_expressions: tuple[str, ...] = ("euh", "heu", "hum", "hmm", "bah", "ben")
    cache_dir: Path = field(default_factory=lambda: Path(".cache") / "candidate_analysis")
    use_cache: bool = True

    def __post_init__(self) -> None:
        if not self.window_durations_seconds or any(value <= 0 for value in self.window_durations_seconds):
            raise ValueError("Les durees candidates doivent etre strictement positives.")
        if self.step_seconds <= 0:
            raise ValueError("Le pas doit etre strictement positif.")
        if self.minimum_silence_duration_seconds < 0:
            raise ValueError("La duree minimale de silence ne peut pas etre negative.")
        if not self.hesitation_expressions:
            raise ValueError("La liste des hesitations ne peut pas etre vide.")

    def global_cache_config(self) -> dict[str, Any]:
        """Only settings that change the expensive global timelines."""
        return {
            "silence_threshold_db": self.silence_threshold_db,
            "minimum_silence_duration_seconds": self.minimum_silence_duration_seconds,
            "transcription_model": self.transcription_model,
            "transcription_language": self.transcription_language,
            "transcription_device": self.transcription_device,
            "transcription_compute_type": self.transcription_compute_type,
        }

    def output_config(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["window_durations_seconds"] = list(self.window_durations_seconds)
        payload["hesitation_expressions"] = list(self.hesitation_expressions)
        payload["cache_dir"] = str(self.cache_dir)
        return payload


@dataclass(frozen=True)
class GlobalAnalysis:
    duration_seconds: float
    silence_intervals: tuple[Interval, ...]
    speech_intervals: tuple[Interval, ...]
    words: tuple[WordTimestamp, ...]
    transcription_engine: str
    transcription_model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "silence_intervals": [asdict(item) for item in self.silence_intervals],
            "speech_intervals": [asdict(item) for item in self.speech_intervals],
            "words": [asdict(item) for item in self.words],
            "transcription_engine": self.transcription_engine,
            "transcription_model": self.transcription_model,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GlobalAnalysis:
        return cls(
            duration_seconds=float(payload["duration_seconds"]),
            silence_intervals=tuple(Interval(**item) for item in payload["silence_intervals"]),
            speech_intervals=tuple(Interval(**item) for item in payload["speech_intervals"]),
            words=tuple(WordTimestamp(**item) for item in payload["words"]),
            transcription_engine=str(payload["transcription_engine"]),
            transcription_model=str(payload["transcription_model"]),
        )
