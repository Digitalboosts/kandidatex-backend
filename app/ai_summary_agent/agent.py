"""Summary agent: stored screen-alert responses (text only) -> one 100-120 word summary.

The screenshots and their blob URLs are never sent to the model; it only sees the text fields of each
alert. Uses the same OpenAI-compatible deployment as the screen agent (OPENAI_SIX_LUNA_*)."""

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

import httpx

from ..config import get_settings
from .prompts import MAX_WORDS, MIN_WORDS, RETRY_PROMPT, SYSTEM_PROMPT, USER_PROMPT

logger = logging.getLogger("app.ai_summary_agent")

MAX_ALERTS_SENT = 60  # keeps the prompt small on very long interviews


class SummaryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # "no_alerts" | "ai_not_configured" | "ai_timeout" | "ai_unavailable" | "ai_invalid_response"
        self.message = message


@dataclass(frozen=True)
class AlertText:
    """The text-only view of a stored alert. Deliberately has no image or blob URL field."""

    seconds: int
    application: str
    category: str
    activity: str
    message: str
    confidence: float


def word_count(text: str) -> int:
    return len(text.split())


def fit_to_limit(text: str) -> str:
    """Trims an over-long answer back to a whole sentence within MAX_WORDS."""
    text = " ".join(text.split())
    if word_count(text) <= MAX_WORDS:
        return text
    clipped = " ".join(text.split()[:MAX_WORDS])
    if clipped.endswith((".", "?", "!")):
        return clipped
    cut = max(clipped.rfind(". "), clipped.rfind("? "), clipped.rfind("! "))
    if cut > 0 and word_count(clipped[: cut + 1]) >= MIN_WORDS:
        return clipped[: cut + 1]
    return clipped.rstrip(",;:") + "."


def build_payload(alerts: list[AlertText]) -> str:
    shown = alerts if len(alerts) <= MAX_ALERTS_SENT else alerts[: MAX_ALERTS_SENT - 1] + alerts[-1:]
    body = {
        "total_alerts": len(alerts),
        "alerts": [
            {
                "time": a.seconds,
                "application": a.application,
                "category": a.category,
                "activity": a.activity,
                "message": a.message,
                "confidence": round(a.confidence, 2),
            }
            for a in shown
        ],
    }
    return json.dumps(body, ensure_ascii=False)


class SummaryAgent:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.openai_six_luna_api_key
        self._base_url = (settings.openai_six_luna_base_url or "").rstrip("/")
        self._model = settings.openai_six_luna_model
        self._timeout = settings.screen_agent_timeout_seconds
        self._client = client

    async def summarize(self, alerts: list[AlertText]) -> str:
        if not alerts:
            raise SummaryError("no_alerts", "There are no screen alerts to summarize")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT.format(alerts=build_payload(alerts))},
        ]
        text = await self._chat(messages)
        for _ in range(2):  # at most two rewrites to land inside the word range
            if MIN_WORDS <= word_count(text) <= MAX_WORDS:
                return " ".join(text.split())
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": RETRY_PROMPT.format(count=word_count(text))},
            ]
            text = await self._chat(messages)
        return fit_to_limit(text)

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _chat(self, messages: list[dict]) -> str:
        if not (self._api_key and self._base_url and self._model):
            raise SummaryError("ai_not_configured", "The summary model is not configured")
        headers = {"api-key": self._api_key, "Authorization": f"Bearer {self._api_key}"}
        payload = {"model": self._model, "messages": messages, "max_completion_tokens": 600}
        client = self._client or httpx.AsyncClient()
        self._client = client
        try:
            response = await client.post(
                f"{self._base_url}/chat/completions", json=payload, headers=headers, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise SummaryError("ai_timeout", "The summary model timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("Summary request failed: %s", type(exc).__name__)
            raise SummaryError("ai_unavailable", "The summary model is unavailable") from exc
        if response.status_code >= 400:
            logger.warning("Summary model returned HTTP %s: %s", response.status_code, response.text[:300])
            raise SummaryError("ai_unavailable", f"The summary model returned HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise SummaryError("ai_invalid_response", "The summary model returned an invalid response") from exc
        content = re.sub(r"^```\w*\s*|\s*```$", "", (content or "").strip()).strip()
        if not content:
            raise SummaryError("ai_invalid_response", "The summary model returned an empty response")
        return content


@lru_cache
def get_summary_agent() -> SummaryAgent:
    return SummaryAgent()
