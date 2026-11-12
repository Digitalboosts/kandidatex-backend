from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError, not_found
from ..ids import normalize_interview_id
from ..models import ENDED_STATUSES, Interview


def is_ended(interview: Interview) -> bool:
    return interview.status in ENDED_STATUSES


async def get_interview_or_404(session: AsyncSession, raw_id: str) -> Interview:
    interview = await session.scalar(select(Interview).where(Interview.id == normalize_interview_id(raw_id)))
    if interview is None:
        raise not_found("Interview not found")
    return interview


def assert_not_ended(interview: Interview) -> None:
    if is_ended(interview):
        raise ApiError(410, "interview_ended", "This interview has ended")
