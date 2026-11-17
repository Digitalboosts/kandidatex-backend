"""Stored HIGH screen alerts and the per-meeting report (interview + recordings + alerts + timeline)."""

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from ..deps import CurrentUser, Session, assert_can_manage
from ..errors import not_found
from ..ai_summary_agent import SummaryError
from ..errors import ApiError
from ..models import Interview, InterviewSummary, MonitoringEvent, Recording, ScreenAlert, UserRole
from ..schemas import (
    InterviewReportResponse,
    InterviewSummaryDTO,
    InterviewSummaryEnvelope,
    ScreenAlertDTO,
    ScreenAlertListResponse,
)
from ..serializers import event_dto, interview_dto, iso
from ..services import recordings as storage
from ..services.interviews import get_interview_or_404
from ..services.summaries import generate_summary
from ._docs import errors
from .recordings import recording_dto

router = APIRouter(prefix="/api", tags=["Screen alerts"])


def screen_alert_dto(row: ScreenAlert) -> ScreenAlertDTO:
    return ScreenAlertDTO(
        id=row.id,
        interview_id=row.interview_id,
        user_id=row.user_id,
        application=row.application,
        category=row.category,
        activity=row.activity,
        alert_level=row.alert_level,
        message=row.message,
        confidence=row.confidence,
        image_url=f"/api/screen-alerts/{row.id}/image",
        blob_url=row.image_blob_url,
        created_at=iso(row.created_at),  # type: ignore[arg-type]
    )


def summary_dto(row: InterviewSummary | None) -> InterviewSummaryDTO | None:
    if row is None:
        return None
    return InterviewSummaryDTO(
        interview_id=row.interview_id,
        user_id=row.user_id,
        summary=row.summary,
        word_count=row.word_count,
        alert_count=row.alert_count,
        created_at=iso(row.created_at),  # type: ignore[arg-type]
        updated_at=iso(row.updated_at),  # type: ignore[arg-type]
    )


async def _summary(session, interview_id: str) -> InterviewSummary | None:
    return await session.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == interview_id))


_SUMMARY_ERRORS = {"no_alerts": 409, "ai_not_configured": 503, "ai_timeout": 504}


async def _alerts(session, interview_id: str) -> list[ScreenAlert]:
    query = select(ScreenAlert).where(ScreenAlert.interview_id == interview_id)
    return list((await session.scalars(query.order_by(ScreenAlert.created_at.asc(), ScreenAlert.id.asc()))).all())


@router.get(
    "/interviews/{id}/screen-alerts",
    response_model=ScreenAlertListResponse,
    summary="Stored HIGH screen alerts of an interview",
    description="Oldest first. Each item has the six model fields, the interviewer (`userId`) and an `imageUrl` for "
    "the screenshot that triggered it. Only the owner or an admin.",
    responses=errors(401, 403, 404),
)
async def list_interview_screen_alerts(id: str, user: CurrentUser, session: Session) -> ScreenAlertListResponse:
    interview = await get_interview_or_404(session, id)
    assert_can_manage(user, interview)
    return ScreenAlertListResponse(
        screen_alerts=[screen_alert_dto(r) for r in await _alerts(session, interview.id)],
        summary=summary_dto(await _summary(session, interview.id)),
    )


@router.get(
    "/screen-alerts",
    response_model=ScreenAlertListResponse,
    summary="All stored screen alerts",
    description="Newest first. Interviewers see their own; admins see all. Filter with `interviewId`.",
    responses=errors(401),
)
async def list_screen_alerts(
    user: CurrentUser,
    session: Session,
    interview_id: str | None = Query(default=None, alias="interviewId", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
) -> ScreenAlertListResponse:
    query = select(ScreenAlert)
    if user.role != UserRole.ADMIN:
        query = query.where(ScreenAlert.user_id == user.id)
    if interview_id:
        query = query.where(ScreenAlert.interview_id == interview_id)
    rows = (await session.scalars(query.order_by(ScreenAlert.created_at.desc()).limit(limit))).all()
    return ScreenAlertListResponse(screen_alerts=[screen_alert_dto(r) for r in rows])


@router.get(
    "/screen-alerts/{alert_id}/image",
    summary="The stored screenshot",
    description="Raw image bytes (PNG/JPEG/WEBP/GIF). Needs the session cookie or Bearer token.",
    responses={**errors(401, 403, 404), 200: {"content": {"image/png": {}, "image/jpeg": {}}}},
)
async def screen_alert_image(alert_id: str, user: CurrentUser, session: Session) -> Response:
    row = await session.scalar(select(ScreenAlert).where(ScreenAlert.id == alert_id))
    if row is None:
        raise not_found("Screen alert not found")
    assert_can_manage(user, await session.get(Interview, row.interview_id))  # type: ignore[arg-type]
    return Response(
        content=await storage.download_image(row.image_blob_path), media_type=row.image_content_type, headers={"Cache-Control": "private, no-store"}
    )


@router.get(
    "/interviews/{id}/report",
    response_model=InterviewReportResponse,
    summary="Everything stored for a meeting",
    description="The interview, its recordings (with `videoUrl`), the HIGH screen alerts (with `imageUrl`) and the "
    "event timeline, in one response. Only the owner or an admin.",
    responses=errors(401, 403, 404),
)
async def interview_report(id: str, user: CurrentUser, session: Session) -> InterviewReportResponse:
    interview = await get_interview_or_404(session, id)
    assert_can_manage(user, interview)
    recordings = (
        await session.scalars(
            select(Recording).where(Recording.interview_id == interview.id).order_by(Recording.created_at.asc())
        )
    ).unique().all()
    events = (
        await session.scalars(
            select(MonitoringEvent)
            .where(MonitoringEvent.interview_id == interview.id)
            .order_by(MonitoringEvent.created_at.asc(), MonitoringEvent.id.asc())
        )
    ).all()
    return InterviewReportResponse(
        interview=interview_dto(interview),
        recordings=[recording_dto(r) for r in recordings],
        screen_alerts=[screen_alert_dto(r) for r in await _alerts(session, interview.id)],
        summary=summary_dto(await _summary(session, interview.id)),
        events=[event_dto(e) for e in events],
    )


@router.get(
    "/interviews/{id}/summary",
    response_model=InterviewSummaryEnvelope,
    summary="AI summary of the stored screen alerts",
    description="`summary` is `null` until one exists. It is created automatically when the interview ends "
    "(if there were HIGH alerts) or on demand with `POST`. Only the owner or an admin.",
    responses=errors(401, 403, 404),
)
async def get_summary(id: str, user: CurrentUser, session: Session) -> InterviewSummaryEnvelope:
    interview = await get_interview_or_404(session, id)
    assert_can_manage(user, interview)
    return InterviewSummaryEnvelope(summary=summary_dto(await _summary(session, interview.id)))


@router.post(
    "/interviews/{id}/summary",
    response_model=InterviewSummaryEnvelope,
    summary="Create or refresh the AI summary",
    description="Sends only the text of the stored alerts (never screenshots or blob URLs) to the summary agent and "
    "saves a 100-120 word summary for the interview, replacing any earlier one. 409 `no_alerts` when nothing was "
    "stored.",
    responses=errors(401, 403, 404, 409, 502, 503, 504),
)
async def create_summary(id: str, user: CurrentUser, session: Session) -> InterviewSummaryEnvelope:
    interview = await get_interview_or_404(session, id)
    assert_can_manage(user, interview)
    try:
        row = await generate_summary(session, interview)
    except SummaryError as exc:
        raise ApiError(_SUMMARY_ERRORS.get(exc.code, 502), exc.code, exc.message) from exc
    return InterviewSummaryEnvelope(summary=summary_dto(row))
