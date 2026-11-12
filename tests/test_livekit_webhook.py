import base64
import hashlib
import json

from livekit import api

from .helpers import PASSWORD, bearer, event_types

KEY = "devkey"
SECRET = "devsecret-devsecret-devsecret-dev"


def signed(payload: dict, secret: str = SECRET, body: str | None = None) -> dict:
    """Signs a webhook the way LiveKit does: a JWT carrying the body's SHA-256."""
    raw = json.dumps(payload)
    digest = base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode()
    token = api.AccessToken(KEY, secret).with_sha256(digest).to_jwt()
    return {"content": body if body is not None else raw, "headers": {"authorization": token, "content-type": "application/webhook+json"}}


async def setup(client):
    registered = await client.post("/api/auth/register", json={"name": "Owner", "email": "owner@example.com", "password": PASSWORD})
    token = registered.json()["token"]
    created = await client.post(
        "/api/interviews", headers=bearer(token), json={"title": "Role", "candidateName": "Sam Candidate"}
    )
    return token, created.json()["interview"]


async def send(client, payload: dict, **kwargs):
    return await client.post("/api/livekit/webhook", **signed(payload, **kwargs))


async def test_rejects_unsigned_and_wrongly_signed_requests(client):
    payload = {"event": "participant_joined", "id": "x", "room": {"name": "r"}}
    unsigned = await client.post("/api/livekit/webhook", content=json.dumps(payload))
    assert unsigned.status_code == 401
    forged = await send(client, payload, secret="wrong-secret-wrong-secret-wrong-secret")
    assert forged.status_code == 401


async def test_rejects_a_body_that_does_not_match_its_signature(client):
    response = await send(client, {"event": "room_finished", "id": "x", "room": {"name": "r"}}, body='{"event":"tampered"}')
    assert response.status_code == 401


async def test_records_the_session_as_livekit_reports_it_ignoring_redelivery(client):
    token, interview = await setup(client)
    room = {"name": interview["livekitRoomName"]}
    candidate = {"identity": f"candidate-{interview['id']}"}

    async def ok(payload):
        assert (await send(client, payload)).status_code == 200

    await ok({"event": "participant_joined", "id": "evt1", "room": room, "participant": candidate})
    await ok({"event": "participant_joined", "id": "evt1", "room": room, "participant": candidate})
    await ok({"event": "track_published", "id": "evt2", "room": room, "participant": candidate, "track": {"type": "VIDEO"}})
    await ok({"event": "track_published", "id": "evt3", "room": room, "participant": candidate, "track": {"type": "AUDIO"}})
    await ok({"event": "track_unpublished", "id": "evt4", "room": room, "participant": candidate, "track": {"type": "VIDEO"}})
    await ok({"event": "participant_joined", "id": "evt5", "room": room, "participant": {"identity": "interviewer-abc"}})
    await ok({"event": "participant_left", "id": "evt6", "room": room, "participant": candidate})

    assert await event_types(client, token, interview["id"]) == [
        "monitoring_started",
        "camera_connected",
        "camera_disconnected",
        "interviewer_joined",
        "candidate_left",
    ]


async def test_closes_an_active_interview_when_its_room_finishes_and_ignores_events_after(client):
    from sqlalchemy import update

    from app.db import get_sessionmaker
    from app.models import Interview, InterviewStatus, utcnow

    token, interview = await setup(client)
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Interview).where(Interview.id == interview["id"]).values(status=InterviewStatus.ACTIVE, started_at=utcnow())
        )
        await session.commit()
    room = {"name": interview["livekitRoomName"]}

    await send(client, {"event": "room_finished", "id": "evt10", "room": room})
    status = (await client.get(f"/api/interviews/{interview['id']}", headers=bearer(token))).json()["interview"]["status"]
    assert status == "COMPLETED"

    left = {"event": "participant_left", "id": "evt11", "room": room, "participant": {"identity": f"candidate-{interview['id']}"}}
    await send(client, left)
    assert await event_types(client, token, interview["id"]) == ["session_ended"]


async def test_leaves_a_scheduled_interview_alone_when_its_room_finishes(client):
    token, interview = await setup(client)
    await send(client, {"event": "room_finished", "id": "evt20", "room": {"name": interview["livekitRoomName"]}})
    status = (await client.get(f"/api/interviews/{interview['id']}", headers=bearer(token))).json()["interview"]["status"]
    assert status == "SCHEDULED"


async def test_ignores_rooms_it_does_not_know(client):
    response = await send(client, {"event": "room_finished", "id": "evt30", "room": {"name": "not-ours"}})
    assert response.status_code == 200 and response.json() == {"ok": True}
