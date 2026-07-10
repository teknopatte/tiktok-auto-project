"""Local, objective candidate-window analysis for long videos."""

from .analyzer import analyze_video
from .schemas import AnalysisConfig

__all__ = ["AnalysisConfig", "analyze_video"]
