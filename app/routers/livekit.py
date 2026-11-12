import logging

from fastapi import APIRouter, Request
from livekit.protocol import models
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentActor, Session, assert_can_access
from ..errors import ApiError, unauthorized
from ..models import Interview, InterviewStatus, utcnow
from ..rate_limit import rate_limit
from ..schemas import LiveKitTokenResponse, OkResponse, TokenRequest
from ..services import livekit as lk
from ..services.interviews import assert_not_ended, get_interview_or_404, is_ended
from ..services.monitoring import record_event
from ._docs import errors

router = APIRouter(prefix="/api/livekit", tags=["LiveKit"])
logger = logging.getLogger("app.livekit")


@router.post(
    "/token",
    response_model=LiveKitTokenResponse,
    summary="Get a LiveKit room token",
    description="Issues a short-lived token for the interview's room (creating the room if needed). "
    "**A candidate's first request here starts the interview** (`SCHEDULED` → `ACTIVE`). Candidates always get the "
    "same identity (`candidate-<id>`), so a second device replaces the first. Candidates may publish camera and "
    "microphone only; interviewers are subscribe-only; nobody may publish data. Limited to 30 per minute per user.",
    responses=errors(400, 401, 403, 404, 410, 415, 429, 503),
)
async def livekit_token(body: TokenRequest, actor: CurrentActor, session: Session) -> LiveKitTokenResponse:
    interview = await get_interview_or_404(session, body.interview_id)
    assert_can_access(actor, interview)
    assert_not_ended(interview)

    identity_key = actor.user.id if actor.kind == "user" and actor.user else interview.id
    rate_limit(f"livekit-token:{identity_key}", 30, 60 * 1000)

    room_name = interview.livekit_room_name
    try:
        await lk.ensure_room(room_name)
    except Exception:  # noqa: BLE001
        logger.exception("Could not reach LiveKit")
        raise ApiError(503, "livekit_unavailable", "The video service is unavailable, please try again") from None

    if actor.kind == "candidate":
        identity = lk.candidate_identity(interview.id)
        token = lk.candidate_token(room_name, identity, interview.candidate_name)
        if interview.status == InterviewStatus.SCHEDULED:
            interview.status = InterviewStatus.ACTIVE
            interview.started_at = utcnow()
            await session.commit()
    else:
        assert actor.user is not None
        identity = lk.interviewer_identity(actor.user.id)
        token = lk.interviewer_token(room_name, identity, actor.user.name)

    settings = get_settings()
    return LiveKitTokenResponse(
        token=token,
        url=settings.livekit_public_url,
        room_name=room_name,
        identity=identity,
        expires_in_seconds=settings.livekit_token_ttl_seconds,
    )


def _event_type_for(webhook) -> str | None:  # noqa: ANN001 - livekit protobuf WebhookEvent
    identity = webhook.participant.identity if webhook.HasField("participant") else ""
    is_candidate = identity.startswith(lk.CANDIDATE_IDENTITY_PREFIX)
    is_interviewer = identity.startswith(lk.INTERVIEWER_IDENTITY_PREFIX)
    is_video = webhook.HasField("track") and webhook.track.type == models.TrackType.VIDEO
    match webhook.event:
        case "participant_joined":
            if is_candidate:
                return "monitoring_started"
            return "interviewer_joined" if is_interviewer else None
        case "participant_left":
            if is_candidate:
                return "candidate_left"
            return "interviewer_left" if is_interviewer else None
        case "track_published":
            return "camera_connected" if is_candidate and is_video else None
        case "track_unpublished":
            return "camera_disconnected" if is_candidate and is_video else None
    return None


@router.post(
    "/webhook",
    response_model=OkResponse,
    summary="LiveKit webhook (called by LiveKit)",
    description="LiveKit's own view of the session, independent of what any client reports. Not for the "
    "frontend. The request must be signed by LiveKit (the `Authorization` header carries a JWT covering the raw "
    "body). Redelivered webhooks don't create duplicate events.",
    responses=errors(401),
)
async def livekit_webhook(request: Request, session: Session) -> OkResponse:
    body = (await request.body()).decode("utf-8", errors="replace")
    try:
        webhook = lk.receive_webhook(body, request.headers.get("authorization"))
    except Exception:  # noqa: BLE001
        raise unauthorized("Invalid webhook signature") from None

    room_name = webhook.room.name if webhook.HasField("room") else ""
    if not room_name:
        return OkResponse()
    interview = await session.scalar(select(Interview).where(Interview.livekit_room_name == room_name))
    if interview is None:
        return OkResponse()

    # Leaving is expected once a session has ended, so don't raise warnings for it.
    if is_ended(interview):
        return OkResponse()

    interview_id = interview.id
    if webhook.event == "room_finished":
        if interview.status == InterviewStatus.ACTIVE:
            interview.status = InterviewStatus.COMPLETED
            interview.ended_at = utcnow()
            await session.commit()
            await record_event(
                session,
                interview_id,
                room_name,
                "session_ended",
                message="Session closed because the room stayed empty.",
                metadata={"endedBy": "system"},
                dedupe_key=webhook.id or None,
            )
        return OkResponse()

    event_type = _event_type_for(webhook)
    if event_type:
        await record_event(session, interview_id, room_name, event_type, dedupe_key=webhook.id or None)
    return OkResponse()
