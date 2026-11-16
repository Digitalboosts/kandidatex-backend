"""Screen analysis agent: screenshot bytes -> vision model -> validated six-field result.

The model is an OpenAI-compatible (Azure OpenAI v1) chat-completions deployment configured by
OPENAI_SIX_LUNA_API_KEY / OPENAI_SIX_LUNA_BASE_URL / OPENAI_SIX_LUNA_MODEL. Per-interview state
(the last application seen) lives in memory and is used only to decide whether to alert."""

import base64
import json
import logging
import re
from functools import lru_cache

import httpx
from pydantic import ValidationError

from ..config import get_settings
from .prompts import SYSTEM_PROMPT, USER_PROMPT
from .schemas import KNOWN_APPLICATIONS, UNKNOWN_APP, AlertRules, ScreenAnalysis, ScreenAnalysisError

logger = logging.getLogger("app.ai_screen_agent")

_CANONICAL = {name.lower(): name for name in KNOWN_APPLICATIONS}
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def detect_mime(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    if image[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    raise ScreenAnalysisError("invalid_image", "Image must be PNG, JPEG, WEBP or GIF")


def parse_model_output(text: str) -> ScreenAnalysis:
    """Validates the model's JSON; raises ScreenAnalysisError if it is not the six-field shape."""
    try:
        analysis = ScreenAnalysis.model_validate(json.loads(_FENCE.sub("", text.strip())))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ScreenAnalysisError("ai_invalid_response", "The vision model returned an invalid response") from exc
    canonical = _CANONICAL.get(analysis.application.strip().lower())
    return analysis.model_copy(update={"application": canonical}) if canonical else analysis


class ScreenAnalysisAgent:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        rules: AlertRules | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.openai_six_luna_api_key
        self._base_url = (base_url if base_url is not None else settings.openai_six_luna_base_url or "").rstrip("/")
        self._model = model if model is not None else settings.openai_six_luna_model
        self._timeout = timeout if timeout is not None else settings.screen_agent_timeout_seconds
        self._client = client
        base_rules = AlertRules()
        self.default_rules = rules or AlertRules(
            app_levels={**base_rules.app_levels, **{app: "HIGH" for app in settings.screen_agent_high_app_list}}
        )
        self._rules: dict[str, AlertRules] = {}
        self._previous_app: dict[str, str] = {}

    # ------------------------------------------------------------------ public API

    async def analyze(self, image: bytes, interview_id: str) -> ScreenAnalysis:
        """Returns the six-field analysis. Raises ScreenAnalysisError if the AI request fails."""
        analysis, _ = await self.analyze_with_alert(image, interview_id)
        return analysis

    async def analyze_with_alert(self, image: bytes, interview_id: str) -> tuple[ScreenAnalysis, bool]:
        """Like ``analyze`` but also says whether a *new* alert should be raised (no repeats while
        the same application stays visible)."""
        raw = await self._call_model(image)
        return self._decide(interview_id, raw)

    def set_rules(self, interview_id: str, rules: AlertRules) -> None:
        """Interviewer-configured rules for one interview."""
        self._rules[interview_id] = rules

    def reset(self, interview_id: str) -> None:
        """Forget state for an interview (call when it ends)."""
        self._previous_app.pop(interview_id, None)
        self._rules.pop(interview_id, None)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ alert logic

    def _decide(self, interview_id: str, raw: ScreenAnalysis) -> tuple[ScreenAnalysis, bool]:
        rules = self._rules.get(interview_id, self.default_rules)
        app = raw.application
        if app.upper() == UNKNOWN_APP:  # unclear frame: no alert, and don't disturb the stored state
            return raw.model_copy(update={"alert_level": "LOW"}), False

        previous = self._previous_app.get(interview_id)
        changed = previous is not None and previous.lower() != app.lower()
        level = rules.level_for(app, raw.category)
        if level == "LOW" and changed:
            level = rules.change_level

        update: dict = {"alert_level": level}
        if level == "HIGH":
            update["message"] = f"{app} detected on screen. Review recommended."
        elif level == "MEDIUM":
            update["message"] = f"Application change detected: {app} is now visible."
        self._previous_app[interview_id] = app

        is_new = previous is None or changed
        return raw.model_copy(update=update), level != "LOW" and is_new

    # ------------------------------------------------------------------ model call

    async def _call_model(self, image: bytes) -> ScreenAnalysis:
        if not (self._api_key and self._base_url and self._model):
            raise ScreenAnalysisError("ai_not_configured", "The screen analysis model is not configured")
        data_url = f"data:{detect_mime(image)};base64,{base64.b64encode(image).decode('ascii')}"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 600,
        }
        headers = {"api-key": self._api_key, "Authorization": f"Bearer {self._api_key}"}
        client = self._client or httpx.AsyncClient()
        self._client = client
        try:
            response = await client.post(
                f"{self._base_url}/chat/completions", json=payload, headers=headers, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise ScreenAnalysisError("ai_timeout", "The vision model timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("Vision request failed: %s", type(exc).__name__)
            raise ScreenAnalysisError("ai_unavailable", "The vision model is unavailable") from exc
        if response.status_code >= 400:
            logger.warning("Vision model returned HTTP %s: %s", response.status_code, response.text[:300])
            raise ScreenAnalysisError("ai_unavailable", f"The vision model returned HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ScreenAnalysisError("ai_invalid_response", "The vision model returned an invalid response") from exc
        return parse_model_output(content or "")


@lru_cache
def get_screen_agent() -> ScreenAnalysisAgent:
    """Process-wide agent so per-interview state survives between requests."""
    return ScreenAnalysisAgent()
