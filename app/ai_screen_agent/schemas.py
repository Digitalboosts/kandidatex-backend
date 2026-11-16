"""Response model, alert rules and errors."""

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "DESKTOP", "CODE_EDITOR", "IDE", "WEB_BROWSER", "AI_ASSISTANT", "TERMINAL", "DOCUMENT_EDITOR",
    "SPREADSHEET", "PRESENTATION", "VIDEO_CALL", "FILE_MANAGER", "OTHER", "UNKNOWN",
]  # fmt: skip
AlertLevel = Literal["HIGH", "MEDIUM", "LOW"]

UNKNOWN_APP = "UNKNOWN"

# Canonical names the agent recognises; model output is matched to these case-insensitively.
KNOWN_APPLICATIONS: tuple[str, ...] = (
    "Windows Desktop", "Visual Studio Code", "PyCharm", "IntelliJ IDEA", "Chrome", "Edge", "Firefox",
    "ChatGPT", "Claude", "Gemini", "Microsoft Copilot", "Terminal", "Command Prompt", "PowerShell",
    "Microsoft Word", "Excel", "PowerPoint", "Google Meet", "Zoom", "Other", UNKNOWN_APP,
)  # fmt: skip


class ScreenAnalysis(BaseModel):
    """Exactly six fields; anything else from the model is rejected."""

    model_config = ConfigDict(extra="forbid")

    application: Annotated[str, Field(min_length=1, max_length=100)]
    category: Category
    activity: Annotated[str, Field(min_length=1, max_length=300)]
    alert_level: AlertLevel
    message: Annotated[str, Field(min_length=1, max_length=300)]
    confidence: Annotated[float, Field(ge=0, le=1)]


@dataclass
class AlertRules:
    """Which applications need review. Interviewers can supply their own per interview."""

    app_levels: dict[str, AlertLevel] = field(
        default_factory=lambda: {"ChatGPT": "HIGH", "Claude": "HIGH", "Gemini": "HIGH", "Microsoft Copilot": "HIGH"}
    )
    # Applications not listed above fall back to: AI_ASSISTANT category -> HIGH, otherwise LOW.
    ai_assistant_level: AlertLevel = "HIGH"
    change_level: AlertLevel = "MEDIUM"  # a switch between applications, unless a higher rule applies

    def level_for(self, application: str, category: str) -> AlertLevel:
        for name, level in self.app_levels.items():
            if name.lower() == application.lower():
                return level
        return self.ai_assistant_level if category == "AI_ASSISTANT" else "LOW"


class ScreenAnalysisError(Exception):
    """The vision request failed. Never converted into a fabricated successful analysis."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # "ai_not_configured" | "ai_timeout" | "ai_unavailable" | "ai_invalid_response"
        self.message = message
