from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request
from sqlalchemy import select

from ..config import get_settings
from ..deps import CurrentActor, CurrentUser, Session, assert_can_access, assert_can_manage
from ..errors import not_found
from ..ids import generate_interview_id, normalize_name
from ..models import Interview, InterviewStatus, MonitoringEvent, UserRole, utcnow
from ..rate_limit import client_ip, rate_limit
from ..schemas import (
    CandidateInterviewEnvelope,
    CreateInterviewRequest,
    EventListResponse,
    InterviewEnvelope,
    InterviewListResponse,
    JoinRequest,
    JoinResponse,
)
from ..security import sign_candidate_token
from ..serializers import candidate_interview_dto, event_dto, interview_dto
from ..services import livekit
from ..services.interviews import assert_not_ended, get_interview_or_404, is_ended
from ..services.monitoring import record_event
from ..services.summaries import summarize_in_background
from ..subscription.service import consume_interview_quota
from ._docs import errors

router = APIRouter(prefix="/api/interviews", tags=["Interviews"])


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@router.post(
    "",
    status_code=201,
    response_model=InterviewEnvelope,
    summary="Create an interview",
    description="Generates an unguessable, typeable interview ID (e.g. `7K2M-9QXT-4HVB`) and a LiveKit room name.",
    responses=errors(400, 401, 402, 415),
)
async def create_interview(body: CreateInterviewRequest, user: CurrentUser, session: Session) -> InterviewEnvelope:
    await consume_interview_quota(session, user)  # needs an active plan with interviews left
    interview_id = generate_interview_id()
    interview = Interview(
        id=interview_id,
        title=body.title,
        candidate_name=body.candidate_name,
        interviewer_id=user.id,
        livekit_room_name=f"interview-{interview_id}",
        status=InterviewStatus.SCHEDULED,
        scheduled_at=_parse_iso(body.scheduled_at) if body.scheduled_at else None,
    )
    session.add(interview)
    await session.commit()
    interview.monitoring_status = None
    return InterviewEnvelope(interview=interview_dto(interview))


@router.get(
    "",
    response_model=InterviewListResponse,
    summary="List interviews",
    description="Interviewers see their own interviews; admins see everyone's. Newest first, at most 200.",
    responses=errors(401),
)
async def list_interviews(user: CurrentUser, session: Session) -> InterviewListResponse:
    query = select(Interview).order_by(Interview.created_at.desc(), Interview.id.desc()).limit(200)
    if user.role != UserRole.ADMIN:
        query = query.where(Interview.interviewer_id == user.id)
    interviews = (await session.scalars(query)).all()
    return InterviewListResponse(interviews=[interview_dto(item) for item in interviews])


@router.get(
    "/{id}",
    response_model=InterviewEnvelope | CandidateInterviewEnvelope,
    summary="Get an interview",
    description="Accepts an interviewer/admin session **or** the candidate token from `join`. "
    "Interviewers get the full `InterviewDTO` (including `monitoringStatus`); candidates get the reduced "
    "`CandidateInterviewDTO`. A candidate token only opens its own interview.",
    responses=errors(401, 403, 404),
)
async def get_interview(id: str, actor: CurrentActor, session: Session) -> InterviewEnvelope | CandidateInterviewEnvelope:
    interview = await get_interview_or_404(session, id)
    assert_can_access(actor, interview)
    if actor.kind == "user":
        return InterviewEnvelope(interview=interview_dto(interview))
    return CandidateInterviewEnvelope(interview=candidate_interview_dto(interview))


@router.post(
    "/{id}/join",
    response_model=JoinResponse,
    summary="Candidate joins (public)",
    description="The candidate proves access with the interview ID and their name and must accept the "
    "monitoring notice (`consentAccepted: true`). Name matching ignores case and extra spaces; the ID tolerates "
    "lower case, spaces and look-alike letters. A wrong name gets the **same** 404 as a wrong ID, so IDs can't be "
    "discovered. Returns a candidate token scoped to this interview. Limited to 20 per 10 minutes per IP.",
    responses=errors(400, 404, 410, 415, 429),
)
async def join_interview(id: str, body: JoinRequest, request: Request, session: Session) -> JoinResponse:
    rate_limit(f"join:{client_ip(request)}", 20, 10 * 60 * 1000)

    no_match = not_found("No interview matches this ID and name")
    try:
        interview = await get_interview_or_404(session, id)
    except Exception:  # noqa: BLE001 - answer identically whatever went wrong
        raise no_match from None
    if normalize_name(interview.candidate_name) != normalize_name(body.candidate_name):
        raise no_match
    assert_not_ended(interview)

    now = utcnow()
    interview.candidate_joined_at = interview.candidate_joined_at or now
    interview.consent_accepted_at = now
    await session.commit()

    await record_event(session, interview.id, interview.livekit_room_name, "consent_accepted")
    await record_event(session, interview.id, interview.livekit_room_name, "candidate_joined")

    return JoinResponse(
        token=sign_candidate_token(interview.id, interview.candidate_name),
        interview=candidate_interview_dto(interview),
        readiness_threshold=get_settings().readiness_threshold,
    )


@router.post(
    "/{id}/end",
    response_model=InterviewEnvelope | CandidateInterviewEnvelope,
    summary="End the session for everyone",
    description="Tells the room, then deletes it so media stops immediately. Candidate tokens stop working "
    "because the status is checked on every request. Status becomes `COMPLETED` if it had started, otherwise "
    "`CANCELLED`. Calling it twice is harmless.",
    responses=errors(401, 403, 404),
)
async def end_interview(
    id: str, actor: CurrentActor, session: Session, background: BackgroundTasks
) -> InterviewEnvelope | CandidateInterviewEnvelope:
    interview = await get_interview_or_404(session, id)
    assert_can_access(actor, interview)

    def serialize(item: Interview) -> InterviewEnvelope | CandidateInterviewEnvelope:
        if actor.kind == "user":
            return InterviewEnvelope(interview=interview_dto(item))
        return CandidateInterviewEnvelope(interview=candidate_interview_dto(item))

    if is_ended(interview):
        return serialize(interview)

    interview_id, room = interview.id, interview.livekit_room_name
    ended_by = "interviewer" if actor.kind == "user" else "candidate"
    interview.status = InterviewStatus.COMPLETED if interview.started_at else InterviewStatus.CANCELLED
    interview.ended_at = utcnow()
    await session.commit()

    await record_event(
        session,
        interview_id,
        room,
        "session_ended",
        message=f"Session ended by the {ended_by}.",
        metadata={"endedBy": ended_by},
    )
    await livekit.broadcast(room, {"type": "session_ended", "interviewId": interview_id, "endedBy": ended_by})
    await livekit.close_room(room)
    background.add_task(summarize_in_background, interview_id)  # AI summary of the stored alerts, if any
    return serialize(interview)


@router.get(
    "/{id}/events",
    response_model=EventListResponse,
    summary="Interview timeline",
    description="Events in chronological order (oldest first). Only the owner or an admin. Use `after` to poll "
    "for new events.",
    responses=errors(400, 401, 403, 404),
)
async def list_events(
    id: str,
    user: CurrentUser,
    session: Session,
    after: Annotated[datetime | None, Query(description="Only events created after this ISO time.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> EventListResponse:
    interview = await get_interview_or_404(session, id)
    assert_can_manage(user, interview)
    query = select(MonitoringEvent).where(MonitoringEvent.interview_id == interview.id)
    if after is not None:
        query = query.where(MonitoringEvent.created_at > (after if after.tzinfo else after.replace(tzinfo=timezone.utc)))
    query = query.order_by(MonitoringEvent.created_at.asc(), MonitoringEvent.id.asc()).limit(limit)
    events = (await session.scalars(query)).all()
    return EventListResponse(events=[event_dto(event) for event in events])
