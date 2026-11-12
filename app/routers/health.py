import logging

from fastapi import APIRouter
from sqlalchemy import text

from ..deps import Session
from ..errors import ApiError
from ..schemas import HealthResponse
from ._docs import errors

router = APIRouter(prefix="/api", tags=["Health"])
logger = logging.getLogger("app.health")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Public. Checks the database connection; use it for load-balancer / platform health checks.",
    responses=errors(503),
)
async def health(session: Session) -> HealthResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        logger.exception("Health check failed")
        raise ApiError(503, "database_unavailable", "The database is unavailable") from None
    return HealthResponse()
