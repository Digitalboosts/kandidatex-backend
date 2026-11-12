"""Fixed-window rate limiter held in process memory.

It protects a single instance; running several instances needs a shared store
such as Redis (swap the implementation behind ``rate_limit``)."""

import time

from fastapi import Request

from .errors import ApiError

_buckets: dict[str, list[float]] = {}  # key -> [count, reset_at_ms]


def rate_limit(key: str, limit: int, window_ms: int) -> None:
    now = time.time() * 1000
    if len(_buckets) > 10_000:
        for existing in [k for k, (_, reset_at) in _buckets.items() if reset_at <= now]:
            del _buckets[existing]
    bucket = _buckets.get(key)
    if bucket is None or bucket[1] <= now:
        _buckets[key] = [1, now + window_ms]
        return
    bucket[0] += 1
    if bucket[0] > limit:
        raise ApiError(429, "rate_limited", "Too many requests, please try again shortly")


def reset_rate_limits() -> None:
    _buckets.clear()


def client_ip(request: Request) -> str:
    """The rightmost X-Forwarded-For entry is the one our own proxy added, so clients can't forge it."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return "unknown"
