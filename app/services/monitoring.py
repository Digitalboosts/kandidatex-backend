"""Recording events and statuses, and pushing them to the room.

Functions take plain ids rather than ORM rows: a rolled-back dedupe conflict
expires loaded rows, and async sessions can't lazily reload them."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EventSeverity, MonitoringEvent, MonitoringStatus
from ..monitoring_rules import EVENT_CATALOG, derive_status_events
from ..schemas import MonitoringEventDTO, MonitoringStatusDTO, StatusRequest
from ..serializers import event_dto, status_dto
from . import livekit


def _dump(model: MonitoringEventDTO | MonitoringStatusDTO) -> dict:
    return model.model_dump(mode="json", by_alias=True)


async def record_event(
    session: AsyncSession,
    interview_id: str,
    room_name: str,
    event_type: str,
    *,
    metadata: dict | None = None,
    dedupe_key: str | None = None,
    message: str | None = None,
) -> MonitoringEventDTO | None:
    """Stores an event and pushes it to the room. Returns None if ``dedupe_key`` was already recorded."""
    severity, default_message = EVENT_CATALOG[event_type]
    if dedupe_key and await session.scalar(select(MonitoringEvent.id).where(MonitoringEvent.dedupe_key == dedupe_key)):
        return None
    row = MonitoringEvent(
        interview_id=interview_id,
        event_type=event_type,
        message=message or default_message,
        severity=EventSeverity(severity),
        metadata_=metadata or None,
        dedupe_key=dedupe_key,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race with a concurrent redelivery of the same webhook.
        await session.rollback()
        return None
    event = event_dto(row)
    await livekit.broadcast(room_name, {"type": "event", "event": _dump(event)})
    return event


async def apply_status(
    session: AsyncSession, interview_id: str, room_name: str, data: StatusRequest
) -> tuple[MonitoringStatusDTO, list[MonitoringEventDTO]]:
    """Saves the latest status, records any events the change implies and pushes both to the room."""
    fields = data.model_dump(exclude={"interview_id"}, by_alias=False)
    row = await session.scalar(select(MonitoringStatus).where(MonitoringStatus.interview_id == interview_id))
    # Read before overwriting: the row is updated in place.
    previous = (row.screen_detected, row.visibility_percentage) if row else (None, None)
    if row is None:
        row = MonitoringStatus(interview_id=interview_id, **fields)
        session.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    await session.commit()

    status = status_dto(row)
    await livekit.broadcast(room_name, {"type": "status", "status": _dump(status)})

    events: list[MonitoringEventDTO] = []
    derived = derive_status_events(
        previous[0], previous[1], data.screen_detected, data.visibility_percentage, data.source
    )
    for event_type in derived:
        event = await record_event(
            session,
            interview_id,
            room_name,
            event_type,
            metadata={"visibilityPercentage": data.visibility_percentage},
        )
        if event:
            events.append(event)
    return status, events
