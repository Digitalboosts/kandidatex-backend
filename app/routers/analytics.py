from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from ..deps import CurrentUser, Session
from ..models import EventSeverity, Interview, InterviewStatus, MonitoringEvent, UserRole, utcnow
from ..schemas import AnalyticsOverview, AnalyticsTotals, DayCount, SeverityCount
from ._docs import errors

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

DAYS = 14


@router.get(
    "/overview",
    response_model=AnalyticsOverview,
    summary="Dashboard overview",
    description="Totals by status, interviews created per day for the last 14 days (UTC, oldest first) and event "
    "counts by severity for the same period. Interviewers see their own numbers; admins see everyone's.",
    responses=errors(401),
)
async def overview(user: CurrentUser, session: Session) -> AnalyticsOverview:
    scope = [] if user.role == UserRole.ADMIN else [Interview.interviewer_id == user.id]

    counts = dict(
        (await session.execute(select(Interview.status, func.count()).where(*scope).group_by(Interview.status))).all()
    )

    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAYS - 1)
    created = (await session.scalars(select(Interview.created_at).where(*scope, Interview.created_at >= since))).all()
    by_day = {(since + timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(DAYS)}
    for created_at in created:
        key = created_at.strftime("%Y-%m-%d")
        by_day[key] = by_day.get(key, 0) + 1

    severity_counts = dict(
        (
            await session.execute(
                select(MonitoringEvent.severity, func.count())
                .join(Interview, Interview.id == MonitoringEvent.interview_id)
                .where(*scope, MonitoringEvent.created_at >= since)
                .group_by(MonitoringEvent.severity)
            )
        ).all()
    )

    def count_of(status: InterviewStatus) -> int:
        return int(counts.get(status, 0))

    return AnalyticsOverview(
        totals=AnalyticsTotals(
            total=sum(int(value) for value in counts.values()),
            active=count_of(InterviewStatus.ACTIVE),
            completed=count_of(InterviewStatus.COMPLETED),
            upcoming=count_of(InterviewStatus.SCHEDULED),
        ),
        interviews_by_day=[DayCount(date=day, count=count) for day, count in by_day.items()],
        events_by_severity=[
            SeverityCount(severity=severity.value, count=int(severity_counts.get(severity, 0)))
            for severity in (EventSeverity.INFO, EventSeverity.WARNING, EventSeverity.CRITICAL)
        ],
    )
