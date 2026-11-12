"""ORM models. Table and column names match the existing Prisma schema, so the
same PostgreSQL database works with this backend without data migration."""

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import new_id


def utcnow() -> datetime:
    """Current UTC time at millisecond precision (the database column precision)."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC (TIMESTAMP without time zone) and always returns aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value is not None else None


JsonColumn = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    INTERVIEWER = "INTERVIEWER"


class InterviewStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class EventSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


ENDED_STATUSES = (InterviewStatus.COMPLETED, InterviewStatus.CANCELLED)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column("passwordHash", String)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, name="UserRole"), default=UserRole.INTERVIEWER)
    # Bumped on logout, which invalidates every session token issued before it.
    session_version: Mapped[int] = mapped_column("sessionVersion", Integer, default=0)
    created_at: Mapped[datetime] = mapped_column("createdAt", UTCDateTime, default=utcnow)


class Interview(Base):
    __tablename__ = "Interview"
    __table_args__ = (
        Index("Interview_interviewerId_status_idx", "interviewerId", "status"),
        Index("Interview_status_scheduledAt_idx", "status", "scheduledAt"),
        Index("Interview_createdAt_idx", "createdAt"),
    )

    # Random, unguessable ID the candidate types in or receives in a link.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    interviewer_id: Mapped[str] = mapped_column("interviewerId", ForeignKey("User.id", ondelete="CASCADE"))
    candidate_name: Mapped[str] = mapped_column("candidateName", String)
    status: Mapped[InterviewStatus] = mapped_column(
        Enum(InterviewStatus, name="InterviewStatus"), default=InterviewStatus.SCHEDULED
    )
    livekit_room_name: Mapped[str] = mapped_column("livekitRoomName", String, unique=True)
    scheduled_at: Mapped[datetime | None] = mapped_column("scheduledAt", UTCDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column("startedAt", UTCDateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column("endedAt", UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", UTCDateTime, default=utcnow)
    candidate_joined_at: Mapped[datetime | None] = mapped_column("candidateJoinedAt", UTCDateTime, nullable=True)
    consent_accepted_at: Mapped[datetime | None] = mapped_column("consentAcceptedAt", UTCDateTime, nullable=True)

    monitoring_status: Mapped["MonitoringStatus | None"] = relationship(
        lazy="selectin", uselist=False, back_populates="interview"
    )


class MonitoringEvent(Base):
    __tablename__ = "MonitoringEvent"
    __table_args__ = (
        Index("MonitoringEvent_interviewId_createdAt_idx", "interviewId", "createdAt"),
        Index("MonitoringEvent_severity_createdAt_idx", "severity", "createdAt"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    interview_id: Mapped[str] = mapped_column("interviewId", ForeignKey("Interview.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column("eventType", String)
    message: Mapped[str] = mapped_column(String)
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity, name="EventSeverity"), default=EventSeverity.INFO
    )
    metadata_: Mapped[dict | None] = mapped_column("metadata", JsonColumn, nullable=True)
    # LiveKit webhook ID, so a redelivered webhook doesn't add a second event.
    dedupe_key: Mapped[str | None] = mapped_column("dedupeKey", String, unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column("createdAt", UTCDateTime, default=utcnow)


class MonitoringStatus(Base):
    __tablename__ = "MonitoringStatus"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    interview_id: Mapped[str] = mapped_column(
        "interviewId", ForeignKey("Interview.id", ondelete="CASCADE"), unique=True
    )
    source: Mapped[str] = mapped_column(String)
    screen_detected: Mapped[bool] = mapped_column("screenDetected", Boolean)
    visibility_percentage: Mapped[int] = mapped_column("visibilityPercentage", Integer)
    clarity: Mapped[str] = mapped_column(String)
    lighting: Mapped[str] = mapped_column(String)
    stability: Mapped[str] = mapped_column(String)
    alignment: Mapped[str] = mapped_column(String)
    readiness_score: Mapped[int] = mapped_column("readinessScore", Integer)
    updated_at: Mapped[datetime] = mapped_column("updatedAt", UTCDateTime, default=utcnow, onupdate=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="monitoring_status")
