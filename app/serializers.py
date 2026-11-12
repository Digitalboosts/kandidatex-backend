"""ORM rows -> response models (the previous backend's *DTO functions)."""

from datetime import datetime, timezone

from .models import Interview, MonitoringEvent, MonitoringStatus, User
from .schemas import (
    CandidateInterviewDTO,
    InterviewDTO,
    MonitoringEventDTO,
    MonitoringStatusDTO,
    UserDTO,
)


def iso(value: datetime | None) -> str | None:
    """Same format as JavaScript's Date#toISOString: 2030-01-02T10:00:00.000Z."""
    if value is None:
        return None
    value = value.astimezone(timezone.utc)
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _iso(value: datetime) -> str:
    return iso(value)  # type: ignore[return-value]


def user_dto(user: User) -> UserDTO:
    return UserDTO(id=user.id, name=user.name, email=user.email, role=user.role.value, created_at=_iso(user.created_at))


def status_dto(status: MonitoringStatus) -> MonitoringStatusDTO:
    return MonitoringStatusDTO(
        id=status.id,
        interview_id=status.interview_id,
        source=status.source,  # type: ignore[arg-type]
        screen_detected=status.screen_detected,
        visibility_percentage=status.visibility_percentage,
        clarity=status.clarity,  # type: ignore[arg-type]
        lighting=status.lighting,  # type: ignore[arg-type]
        stability=status.stability,  # type: ignore[arg-type]
        alignment=status.alignment,  # type: ignore[arg-type]
        readiness_score=status.readiness_score,
        updated_at=_iso(status.updated_at),
    )


def interview_dto(interview: Interview) -> InterviewDTO:
    return InterviewDTO(
        id=interview.id,
        title=interview.title,
        interviewer_id=interview.interviewer_id,
        candidate_name=interview.candidate_name,
        status=interview.status.value,
        livekit_room_name=interview.livekit_room_name,
        scheduled_at=iso(interview.scheduled_at),
        started_at=iso(interview.started_at),
        ended_at=iso(interview.ended_at),
        created_at=_iso(interview.created_at),
        candidate_joined_at=iso(interview.candidate_joined_at),
        monitoring_status=status_dto(interview.monitoring_status) if interview.monitoring_status else None,
    )


def candidate_interview_dto(interview: Interview) -> CandidateInterviewDTO:
    return CandidateInterviewDTO(
        id=interview.id,
        title=interview.title,
        candidate_name=interview.candidate_name,
        status=interview.status.value,
        started_at=iso(interview.started_at),
        ended_at=iso(interview.ended_at),
    )


def event_dto(event: MonitoringEvent) -> MonitoringEventDTO:
    return MonitoringEventDTO(
        id=event.id,
        interview_id=event.interview_id,
        event_type=event.event_type,
        message=event.message,
        severity=event.severity.value,
        metadata=event.metadata_,
        created_at=_iso(event.created_at),
    )
