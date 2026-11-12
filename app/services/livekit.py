"""LiveKit: rooms, realtime messages, access tokens, webhook verification.

Routers call these through the module (``livekit.ensure_room(...)``) so tests can
replace them without a LiveKit server."""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from livekit import api
from livekit.protocol import models

from ..config import get_settings

logger = logging.getLogger("app.livekit")

REALTIME_TOPIC = "monitoring"
CANDIDATE_IDENTITY_PREFIX = "candidate-"
INTERVIEWER_IDENTITY_PREFIX = "interviewer-"

# How long LiveKit keeps an empty room before closing it and sending room_finished.
EMPTY_ROOM_TIMEOUT_SECONDS = 15 * 60
MAX_ROOM_PARTICIPANTS = 6
_CALL_TIMEOUT_SECONDS = 10
# Best-effort calls (broadcast / close) must not stall a request when LiveKit is down.
_BEST_EFFORT_TIMEOUT_SECONDS = 3


def _client() -> api.LiveKitAPI:
    settings = get_settings()
    return api.LiveKitAPI(
        settings.livekit_host, settings.livekit_api_key, settings.livekit_api_secret, failover=False
    )


async def ensure_room(room_name: str) -> None:
    """Creates the room if needed. LiveKit treats this as idempotent for an existing room."""
    client = _client()
    try:
        await asyncio.wait_for(
            client.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=EMPTY_ROOM_TIMEOUT_SECONDS,
                    max_participants=MAX_ROOM_PARTICIPANTS,
                )
            ),
            _CALL_TIMEOUT_SECONDS,
        )
    finally:
        await client.aclose()


async def broadcast(room_name: str, message: dict[str, Any]) -> None:
    """Sends to everyone in the room. Delivery is best effort: clients also poll, and the database is the record."""
    client = _client()
    try:
        payload = json.dumps(message, separators=(",", ":")).encode()
        await asyncio.wait_for(
            client.room.send_data(
                api.SendDataRequest(
                    room=room_name, data=payload, kind=models.DataPacket.Kind.RELIABLE, topic=REALTIME_TOPIC
                )
            ),
            _BEST_EFFORT_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - expected before anyone has joined, when the room doesn't exist yet
        logger.debug("broadcast skipped for %s", room_name, exc_info=True)
    finally:
        await client.aclose()


async def close_room(room_name: str) -> None:
    """Disconnects everyone. Clients see the ROOM_DELETED reason."""
    client = _client()
    try:
        await asyncio.wait_for(client.room.delete_room(api.DeleteRoomRequest(room=room_name)), _BEST_EFFORT_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - the room may never have been created, or may already be gone
        logger.debug("close_room skipped for %s", room_name, exc_info=True)
    finally:
        await client.aclose()


def _sign(identity: str, name: str, grants: api.VideoGrants) -> str:
    settings = get_settings()
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_ttl(timedelta(seconds=settings.livekit_token_ttl_seconds))
        .with_grants(grants)
        .to_jwt()
    )


def candidate_identity(interview_id: str) -> str:
    return f"{CANDIDATE_IDENTITY_PREFIX}{interview_id}"


def interviewer_identity(user_id: str) -> str:
    return f"{INTERVIEWER_IDENTITY_PREFIX}{user_id}"


def candidate_token(room_name: str, identity: str, name: str) -> str:
    """Candidates may publish their camera and microphone only. Neither side may publish
    data, so every realtime message in the room comes from this backend and can't be forged."""
    return _sign(
        identity,
        name,
        api.VideoGrants(
            room=room_name,
            room_join=True,
            can_publish=True,
            can_publish_sources=["camera", "microphone"],
            can_publish_data=False,
            can_subscribe=True,
            can_update_own_metadata=False,
        ),
    )


def interviewer_token(room_name: str, identity: str, name: str) -> str:
    return _sign(
        identity,
        name,
        api.VideoGrants(
            room=room_name,
            room_join=True,
            can_publish=False,
            can_publish_data=False,
            can_subscribe=True,
            can_update_own_metadata=False,
        ),
    )


def receive_webhook(body: str, authorization: str | None) -> Any:
    """Verifies the signature (which covers the body) and returns the parsed event. Raises on failure."""
    settings = get_settings()
    receiver = api.WebhookReceiver(api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret))
    return receiver.receive(body, authorization or "")
