"""Request and response models. JSON field names are camelCase, exactly as the
dashboard and mobile app already expect."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
)
from pydantic.alias_generators import to_camel

UserRoleLiteral = Literal["ADMIN", "INTERVIEWER"]
InterviewStatusLiteral = Literal["SCHEDULED", "ACTIVE", "COMPLETED", "CANCELLED"]
SeverityLiteral = Literal["INFO", "WARNING", "CRITICAL"]
StatusSource = Literal["calibration", "stream_analysis"]
ClarityLevel = Literal["good", "fair", "poor"]
LightingLevel = Literal["good", "low", "high"]
StabilityLevel = Literal["stable", "moderate", "unstable"]
AlignmentLevel = Literal["correct", "off_center", "skewed", "too_close", "too_far", "cut_off", "unknown"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


Trimmed = lambda max_length: Annotated[  # noqa: E731
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)
]

_ISO_WITH_OFFSET = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


# ---------------------------------------------------------------- requests
class RegisterRequest(CamelModel):
    name: Trimmed(120)  # type: ignore[valid-type]
    email: Annotated[EmailStr, Field(max_length=254)]
    password: str = Field(description="10 to 72 bytes (bcrypt only reads the first 72).")
    admin_code: str | None = Field(default=None, max_length=200, description="Creates an ADMIN when it matches the server's code.")

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def _password_rules(cls, value: str) -> str:
        if len(value) < 10:
            raise ValueError("Password must be at least 10 characters")
        if len(value.encode()) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return value


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _lower_email(cls, value: str) -> str:
        return value.lower()


class CreateInterviewRequest(CamelModel):
    title: Trimmed(200)  # type: ignore[valid-type]
    candidate_name: Trimmed(120)  # type: ignore[valid-type]
    scheduled_at: str | None = Field(
        default=None, description="ISO 8601 date-time with a UTC offset, e.g. 2030-01-02T10:00:00Z."
    )

    @field_validator("scheduled_at")
    @classmethod
    def _iso(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ISO_WITH_OFFSET.match(value):
            raise ValueError("Invalid ISO datetime (a UTC offset is required)")
        try:
            datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Invalid ISO datetime") from error
        return value


class JoinRequest(CamelModel):
    candidate_name: Trimmed(120)  # type: ignore[valid-type]
    consent_accepted: StrictBool

    @field_validator("consent_accepted")
    @classmethod
    def _consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Consent to live monitoring is required to join")
        return value


class TokenRequest(CamelModel):
    interview_id: str = Field(min_length=1, max_length=40)


MetadataValue = str | int | float | bool | None


class EventRequest(CamelModel):
    interview_id: str = Field(min_length=1, max_length=40)
    event_type: str = Field(min_length=1, max_length=60)
    metadata: dict[str, MetadataValue] | None = Field(
        default=None, description="Up to 16 keys (names ≤ 60 chars); string values ≤ 300 chars."
    )

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, value: dict[str, MetadataValue] | None) -> dict[str, MetadataValue] | None:
        if value is None:
            return None
        if len(value) > 16:
            raise ValueError("Too many metadata keys")
        for key, item in value.items():
            if len(key) > 60:
                raise ValueError("Metadata key is too long")
            if isinstance(item, str) and len(item) > 300:
                raise ValueError("Metadata value is too long")
        return value


class ScreenAnalysisRequest(CamelModel):
    interview_id: str = Field(min_length=1, max_length=40)
    image: str = Field(min_length=1, description="Screenshot as base64 (a `data:image/...;base64,` prefix is accepted).")


class StatusRequest(CamelModel):
    interview_id: str = Field(min_length=1, max_length=40)
    source: StatusSource
    screen_detected: StrictBool
    visibility_percentage: Annotated[StrictInt, Field(ge=0, le=100)]
    clarity: ClarityLevel
    lighting: LightingLevel
    stability: StabilityLevel
    alignment: AlignmentLevel
    readiness_score: Annotated[StrictInt, Field(ge=0, le=100)]


class CreateRecordingRequest(CamelModel):
    interview_id: str = Field(min_length=1, max_length=40)
    content_type: Literal["video/webm", "video/mp4"] = "video/webm"


class CompleteRecordingRequest(CamelModel):
    chunk_count: Annotated[StrictInt, Field(ge=1, le=50000, description="Chunks uploaded, numbered 0..n-1.")]
    duration_seconds: Annotated[StrictInt, Field(ge=0, le=86400)] | None = None


# --------------------------------------------------------------- responses
class UserDTO(CamelModel):
    id: str
    name: str
    email: str
    role: UserRoleLiteral
    created_at: str


class MonitoringStatusDTO(CamelModel):
    id: str
    interview_id: str
    source: StatusSource
    screen_detected: bool
    visibility_percentage: int
    clarity: ClarityLevel
    lighting: LightingLevel
    stability: StabilityLevel
    alignment: AlignmentLevel
    readiness_score: int
    updated_at: str


class MonitoringEventDTO(CamelModel):
    id: str
    interview_id: str
    event_type: str
    message: str
    severity: SeverityLiteral
    metadata: dict | None = None
    created_at: str


class InterviewDTO(CamelModel):
    id: str = Field(examples=["7K2M-9QXT-4HVB"])
    title: str
    interviewer_id: str
    candidate_name: str
    status: InterviewStatusLiteral
    livekit_room_name: str
    scheduled_at: str | None
    started_at: str | None
    ended_at: str | None
    created_at: str
    candidate_joined_at: str | None
    monitoring_status: MonitoringStatusDTO | None = None


class CandidateInterviewDTO(CamelModel):
    """What a candidate may see about their own interview."""

    id: str
    title: str
    candidate_name: str
    status: InterviewStatusLiteral
    started_at: str | None
    ended_at: str | None


class AuthResponse(CamelModel):
    token: str = Field(description="Session token. Also set as the httpOnly `im_session` cookie.")
    user: UserDTO


class UserEnvelope(CamelModel):
    user: UserDTO


class OkResponse(CamelModel):
    ok: bool = True


class InterviewEnvelope(CamelModel):
    interview: InterviewDTO


class CandidateInterviewEnvelope(CamelModel):
    interview: CandidateInterviewDTO


class InterviewListResponse(CamelModel):
    interviews: list[InterviewDTO]


class JoinResponse(CamelModel):
    token: str = Field(description="Candidate session token, scoped to this interview only.")
    interview: CandidateInterviewDTO
    readiness_threshold: int


class EventListResponse(CamelModel):
    events: list[MonitoringEventDTO]


class EventEnvelope(CamelModel):
    event: MonitoringEventDTO


class StatusResponse(CamelModel):
    status: MonitoringStatusDTO
    events: list[MonitoringEventDTO]


class LiveKitTokenResponse(CamelModel):
    token: str
    url: str
    room_name: str
    identity: str
    expires_in_seconds: int


class AnalyticsTotals(CamelModel):
    total: int
    active: int
    completed: int
    upcoming: int


class DayCount(CamelModel):
    date: str
    count: int


class SeverityCount(CamelModel):
    severity: SeverityLiteral
    count: int


class AnalyticsOverview(CamelModel):
    totals: AnalyticsTotals
    interviews_by_day: list[DayCount]
    events_by_severity: list[SeverityCount]


class HealthResponse(CamelModel):
    status: Literal["ok"] = "ok"


class ErrorDetail(CamelModel):
    code: str = Field(examples=["unauthorized"])
    message: str
    details: list | dict | None = None


class ErrorResponse(CamelModel):
    error: ErrorDetail


class RecordingDTO(CamelModel):
    id: str
    interview_id: str
    interview_title: str
    candidate_name: str
    status: Literal["RECORDING", "READY", "FAILED"]
    content_type: str
    size_bytes: int | None
    duration_seconds: int | None
    started_at: str
    ended_at: str | None
    created_at: str
    video_url: str | None = Field(description="Authenticated URL that streams the video (Range supported); READY only.")


class RecordingEnvelope(CamelModel):
    recording: RecordingDTO


class RecordingListResponse(CamelModel):
    recordings: list[RecordingDTO]
