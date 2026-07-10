from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def generate_windows(
    video_duration_seconds: float,
    durations_seconds: tuple[float, ...] | list[float],
    step_seconds: float,
) -> list[CandidateWindow]:
    if video_duration_seconds < 0:
        raise ValueError("La duree video ne peut pas etre negative.")
    if step_seconds <= 0:
        raise ValueError("Le pas doit etre strictement positif.")
    if any(duration <= 0 for duration in durations_seconds):
        raise ValueError("Les durees candidates doivent etre strictement positives.")

    windows: list[CandidateWindow] = []
    epsilon = 1e-9
    for duration in durations_seconds:
        index = 0
        while True:
            start = index * step_seconds
            end = start + duration
            if end > video_duration_seconds + epsilon:
                break
            windows.append(CandidateWindow(round(start, 9), round(min(end, video_duration_seconds), 9)))
            index += 1
    return windows
