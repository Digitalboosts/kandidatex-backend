import base64
import binascii

from fastapi import APIRouter

from ..deps import CurrentActor, Session, assert_can_access
from ..ai_screen_agent import ScreenAnalysis, ScreenAnalysisError, get_screen_agent
from ..errors import ApiError, forbidden
from ..monitoring_rules import CANDIDATE_REPORTABLE_EVENTS, EVENT_CATALOG, INTERVIEWER_REPORTABLE_EVENTS
from ..rate_limit import rate_limit
from ..schemas import EventEnvelope, EventRequest, ScreenAnalysisRequest, StatusRequest, StatusResponse
from ..services.interviews import assert_not_ended, get_interview_or_404
from ..services.monitoring import apply_status, record_event
from ._docs import errors

router = APIRouter(prefix="/api/monitoring", tags=["Monitoring"])


@router.post(
    "/events",
    status_code=201,
    response_model=EventEnvelope,
    summary="Report an event",
    description="Clients pick an `eventType` from an allowlist; the wording and severity come from the server's "
    "catalog, so clients can't author event text.\n\n"
    "- Candidate may report: `camera_permission_error`, `calibration_passed`, `candidate_reconnecting`, "
    "`candidate_reconnected`, `network_unstable`, `network_recovered`, `app_backgrounded`, `app_foregrounded`.\n"
    "- Interviewer may report: `video_stopped`, `video_resumed`, `network_unstable`, `network_recovered`.\n\n"
    "Limited to 60 per minute per interview and side.",
    responses=errors(400, 401, 403, 404, 410, 415, 429),
)
async def report_event(body: EventRequest, actor: CurrentActor, session: Session) -> EventEnvelope:
    interview = await get_interview_or_404(session, body.interview_id)
    assert_can_access(actor, interview)
    assert_not_ended(interview)

    rate_limit(f"events:{interview.id}:{actor.kind}", 60, 60 * 1000)
    allowed = CANDIDATE_REPORTABLE_EVENTS if actor.kind == "candidate" else INTERVIEWER_REPORTABLE_EVENTS
    if body.event_type not in allowed or body.event_type not in EVENT_CATALOG:
        raise forbidden("This event type cannot be reported by this client")

    event = await record_event(
        session, interview.id, interview.livekit_room_name, body.event_type, metadata=body.metadata
    )
    assert event is not None
    return EventEnvelope(event=event)


@router.post(
    "/status",
    response_model=StatusResponse,
    summary="Report monitoring status",
    description="Phones report `calibration`; once streaming starts, the interviewer's dashboard analyzes the "
    "received video and reports `stream_analysis`. Each side is held to its own `source` (403 otherwise). "
    "Stream-analysis changes can add timeline events (`screen_detected`, `screen_lost`, `visibility_dropped`, "
    "`positioning_corrected`), returned in `events`. Limited to 120 per minute per interview and side.",
    responses=errors(400, 401, 403, 404, 410, 415, 429),
)
async def report_status(body: StatusRequest, actor: CurrentActor, session: Session) -> StatusResponse:
    interview = await get_interview_or_404(session, body.interview_id)
    assert_can_access(actor, interview)
    assert_not_ended(interview)

    expected_source = "calibration" if actor.kind == "candidate" else "stream_analysis"
    if body.source != expected_source:
        raise forbidden(f"This client may only report {expected_source} status")

    rate_limit(f"status:{interview.id}:{actor.kind}", 120, 60 * 1000)
    status, events = await apply_status(session, interview.id, interview.livekit_room_name, body)
    return StatusResponse(status=status, events=events)


_SCREEN_ERRORS = {
    "invalid_image": 400,
    "ai_not_configured": 503,
    "ai_timeout": 504,
    "ai_unavailable": 502,
    "ai_invalid_response": 502,
}


@router.post(
    "/screen-analysis",
    response_model=ScreenAnalysis,
    summary="Analyse a screenshot of the candidate's screen",
    description="The interviewer's dashboard sends a screenshot (base64, up to 8 MB). A vision model identifies the "
    "visible application and returns exactly six fields. When a new application needing review appears, a "
    "`screen_app_review` (HIGH) or `screen_app_changed` (MEDIUM) event is added to the timeline; the same "
    "application staying visible does not repeat it. A HIGH alert means review is recommended, not misuse. "
    "AI failures return an error (never a made-up analysis). Limited to 30 per minute per interview.",
    responses=errors(400, 401, 403, 404, 410, 429, 502, 503, 504),
)
async def analyze_screen(body: ScreenAnalysisRequest, actor: CurrentActor, session: Session) -> ScreenAnalysis:
    interview = await get_interview_or_404(session, body.interview_id)
    assert_can_access(actor, interview)
    assert_not_ended(interview)
    if actor.kind == "candidate":
        raise forbidden("Only the interviewer may request screen analysis")
    rate_limit(f"screen:{interview.id}", 30, 60 * 1000)

    encoded = body.image.split(",", 1)[1] if body.image.startswith("data:") and "," in body.image else body.image
    try:
        image = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "invalid_image", "Image must be valid base64") from None

    interview_id, room_name = interview.id, interview.livekit_room_name  # plain values survive commits
    try:
        analysis, should_alert = await get_screen_agent().analyze_with_alert(image, interview_id)
    except ScreenAnalysisError as exc:
        raise ApiError(_SCREEN_ERRORS.get(exc.code, 502), exc.code, exc.message) from exc

    if should_alert:
        await record_event(
            session,
            interview_id,
            room_name,
            "screen_app_review" if analysis.alert_level == "HIGH" else "screen_app_changed",
            message=analysis.message,
            metadata={"application": analysis.application, "category": analysis.category,
                      "confidence": analysis.confidence},
        )
    return analysis
