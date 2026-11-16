"""Pure-ASGI middleware.

* ``RequestLimitsMiddleware`` — bodies must be JSON and at most 64 KB. A cross-site
  HTML form can't send ``application/json``, which backs up SameSite cookies against CSRF.
* ``SecurityHeadersMiddleware`` — no-store / nosniff / no-referrer on every /api response."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_body

MAX_BODY_BYTES = 64 * 1024
WEBHOOK_PATH = "/api/livekit/webhook"
WEBHOOK_MAX_BODY_BYTES = 1024 * 1024
CHUNK_MAX_BODY_BYTES = 16 * 1024 * 1024  # one video chunk
SCREEN_ANALYSIS_PATH = "/api/monitoring/screen-analysis"
SCREEN_ANALYSIS_MAX_BODY_BYTES = 8 * 1024 * 1024  # base64 screenshot
_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RequestLimitsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        is_webhook = scope["path"] == WEBHOOK_PATH
        method, path = scope["method"], scope["path"]
        is_chunk = method == "PUT" and path.startswith("/api/recordings/") and "/chunks/" in path
        limit = MAX_BODY_BYTES
        if is_webhook:
            limit = WEBHOOK_MAX_BODY_BYTES
        elif is_chunk:
            limit = CHUNK_MAX_BODY_BYTES
        elif scope["path"] == SCREEN_ANALYSIS_PATH:
            limit = SCREEN_ANALYSIS_MAX_BODY_BYTES
        has_body = headers.get("content-length", "0") not in ("", "0") or "transfer-encoding" in headers

        async def reject(status: int, code: str, message: str) -> None:
            await JSONResponse(error_body(code, message), status_code=status)(scope, receive, send)

        if has_body and not is_webhook and not is_chunk and not headers.get("content-type", "").lower().startswith("application/json"):
            await reject(415, "unsupported_media_type", "Content-Type must be application/json")
            return

        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await reject(413, "payload_too_large", "Request body is too large")
            return

        # Buffer the (small) body so chunked uploads can't slip past the limit, then replay it.
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > limit:
                await reject(413, "payload_too_large", "Request body is too large")
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                for name, value in (
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ):
                    if name not in present:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
