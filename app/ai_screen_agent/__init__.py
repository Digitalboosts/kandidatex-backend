"""AI screen detection agent: sends a screenshot to a vision model and returns a six-field analysis."""

from .agent import ScreenAnalysisAgent, get_screen_agent
from .schemas import AlertRules, ScreenAnalysis, ScreenAnalysisError

__all__ = ["AlertRules", "ScreenAnalysis", "ScreenAnalysisAgent", "ScreenAnalysisError", "get_screen_agent"]
