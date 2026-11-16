import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import dispose_engine
from .services import recordings as recordings_storage
from .errors import install_error_handlers
from .middleware import RequestLimitsMiddleware, SecurityHeadersMiddleware
from .routers import analytics, auth, health, interviews, livekit, monitoring, recordings

DESCRIPTION = """
REST API for the interview-monitoring platform: a candidate's phone streams its camera through
LiveKit, and the interviewer's dashboard watches and analyzes it live.

### Conventions
* Bodies are **JSON** (`Content-Type: application/json`, max 64 KB). JSON fields are **camelCase**.
* Errors always look like `{"error": {"code", "message", "details?"}}`.
* Timestamps are ISO 8601 UTC with milliseconds (`2030-01-02T10:00:00.000Z`).

### Authentication
* **Interviewer / admin** — `POST /api/auth/login` (or `register`) returns a `token` and sets the httpOnly
  `im_session` cookie. Send either `Authorization: Bearer <token>` or the cookie. Sessions last 8 hours.
* **Candidate** — `POST /api/interviews/{id}/join` returns a token scoped to **one** interview (4 hours),
  sent as `Authorization: Bearer <token>`.

### Common status codes
`400` invalid input · `401` not signed in · `403` no access · `404` no match (also a wrong candidate name) ·
`410` interview ended · `413`/`415` bad body · `429` rate limited · `503` LiveKit/database unreachable.
"""

TAGS = [
    {"name": "Auth", "description": "Interviewer accounts and sessions."},
    {"name": "Interviews", "description": "Create and manage interviews; candidate join and end."},
    {"name": "LiveKit", "description": "Room tokens for the video session, and LiveKit's signed webhook."},
    {"name": "Monitoring", "description": "Events and screen-detection status reported by the clients."},
    {"name": "Analytics", "description": "Dashboard overview numbers."},
    {"name": "Health", "description": "Liveness / database check."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await dispose_engine()
    await recordings_storage.close()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(
        title="Interview Monitoring API",
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    install_error_handlers(app)

    # Added last = outermost. Order: security headers > CORS > request limits > app.
    app.add_middleware(RequestLimitsMiddleware)
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )
    app.add_middleware(SecurityHeadersMiddleware)

    for router in (auth.router, interviews.router, livekit.router, monitoring.router, recordings.router, analytics.router, health.router):
        app.include_router(router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"name": "Interview Monitoring API", "docs": "/docs", "health": "/api/health"}

    return app


app = create_app()
