import os

# Must be set before the app imports its settings.
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": "sqlite+aiosqlite://",
        "JWT_SECRET": "test-secret-test-secret-test-secret-test-secret",
        "LIVEKIT_API_KEY": "devkey",
        "LIVEKIT_API_SECRET": "devsecret-devsecret-devsecret-dev",
        "LIVEKIT_HOST": "http://localhost:7880",
        "LIVEKIT_PUBLIC_URL": "ws://localhost:7880",
        "READINESS_THRESHOLD": "80",
        "ADMIN_REGISTRATION_CODE": "test-admin-code",
        "CORS_ORIGINS": "",
        "SUBSCRIPTION_ENFORCED": "false",  # test_subscription.py switches it on
        "RAZORPAY_KEY_ID": "rzp_test_key",
        "RAZORPAY_KEY_SECRET": "rzp_test_secret",
        "RAZORPAY_WEBHOOK_SECRET": "whsec_test",
        "EMAIL_SENDER_C": "",
        "EMAIL_PASSWORD_C": "",
    }
)

from unittest.mock import AsyncMock  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.db import dispose_engine, get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.rate_limit import reset_rate_limits  # noqa: E402
from app.services import livekit  # noqa: E402


@pytest_asyncio.fixture
async def client():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    reset_rate_limits()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await dispose_engine()  # drops the in-memory database with its connection


@pytest.fixture
def lk(monkeypatch):
    """Replaces the calls that need a LiveKit server."""
    mocks = types_namespace(
        ensure_room=AsyncMock(return_value=None),
        broadcast=AsyncMock(return_value=None),
        close_room=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(livekit, "ensure_room", mocks.ensure_room)
    monkeypatch.setattr(livekit, "broadcast", mocks.broadcast)
    monkeypatch.setattr(livekit, "close_room", mocks.close_room)
    return mocks


def types_namespace(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)
