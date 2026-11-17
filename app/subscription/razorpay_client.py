"""Thin async client for the Razorpay REST API (Orders / one-time payments) plus signature checks."""

import hashlib
import hmac
import logging
from typing import Any

import httpx

from ..config import get_settings
from ..errors import ApiError
from .plans import Plan

logger = logging.getLogger("app.razorpay")
BASE_URL = "https://api.razorpay.com/v1"

_client: httpx.AsyncClient | None = None


def _credentials() -> tuple[str, str]:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise ApiError(503, "payments_not_configured", "Payments are not configured")
    return settings.razorpay_key_id, settings.razorpay_key_secret


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=BASE_URL, timeout=20)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _request(method: str, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
    key_id, key_secret = _credentials()
    try:
        response = await _http().request(method, path, json=json, auth=(key_id, key_secret))
    except httpx.HTTPError:
        logger.exception("Razorpay request failed: %s %s", method, path)
        raise ApiError(502, "payment_provider_unavailable", "The payment provider is unreachable") from None
    if response.status_code >= 400:
        logger.error("Razorpay %s %s -> %s %s", method, path, response.status_code, response.text[:500])
        raise ApiError(502, "payment_provider_error", "The payment provider rejected the request")
    return response.json()


async def create_order(plan: Plan, user_id: str) -> dict[str, Any]:
    """One-time payment: a Razorpay order for the plan price. Checkout is opened with the returned order ID."""
    return await _request(
        "POST",
        "/orders",
        {
            "amount": plan.amount,
            "currency": plan.currency,
            "notes": {"user_id": user_id, "plan_code": plan.code},
        },
    )


async def fetch_payment(payment_id: str) -> dict[str, Any]:
    return await _request("GET", f"/payments/{payment_id}")


def key_id() -> str:
    return _credentials()[0]


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Razorpay signs `<order_id>|<payment_id>` with the key secret after a successful checkout."""
    secret = _credentials()[1]
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    secret = get_settings().razorpay_webhook_secret
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
