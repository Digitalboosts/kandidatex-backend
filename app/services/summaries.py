"""Builds and stores the AI summary of an interview's screen alerts."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_summary_agent import AlertText, SummaryError, get_summary_agent
from ..db import get_sessionmaker
from ..models import Interview, InterviewSummary, ScreenAlert

logger = logging.getLogger("app.summaries")


async def generate_summary(session: AsyncSession, interview: Interview) -> InterviewSummary:
    """Summarises the stored alerts (text fields only, never the screenshots) and saves it for the interview.
    Raises SummaryError."""
    interview_id, owner_id = interview.id, interview.interviewer_id
    alerts = list(
        (
            await session.scalars(
                select(ScreenAlert)
                .where(ScreenAlert.interview_id == interview_id)
                .order_by(ScreenAlert.created_at.asc(), ScreenAlert.id.asc())
            )
        ).all()
    )
    if not alerts:
        raise SummaryError("no_alerts", "There are no screen alerts to summarize")
    first = alerts[0].created_at
    texts = [
        AlertText(
            seconds=int((a.created_at - first).total_seconds()),
            application=a.application,
            category=a.category,
            activity=a.activity,
            message=a.message,
            confidence=a.confidence,
        )
        for a in alerts
    ]
    text = await get_summary_agent().summarize(texts)

    row = await session.scalar(select(InterviewSummary).where(InterviewSummary.interview_id == interview_id))
    if row is None:
        row = InterviewSummary(interview_id=interview_id, user_id=owner_id, summary=text, word_count=0, alert_count=0)
        session.add(row)
    row.summary, row.word_count, row.alert_count = text, len(text.split()), len(alerts)
    await session.commit()
    return row


async def summarize_in_background(interview_id: str) -> None:
    """Runs after an interview ends; never raises (a failed summary can be regenerated on demand)."""
    try:
        async with get_sessionmaker()() as session:
            interview = await session.get(Interview, interview_id)
            if interview is not None:
                await generate_summary(session, interview)
    except SummaryError as exc:
        if exc.code != "no_alerts":
            logger.warning("Summary for interview %s not created: %s", interview_id, exc.message)
    except Exception:  # noqa: BLE001
        logger.exception("Summary for interview %s failed", interview_id)
