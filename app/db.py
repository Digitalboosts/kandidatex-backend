"""Async SQLAlchemy engine and session dependency."""

from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import get_settings

# Query parameters that belong to other tools (Prisma, PgBouncer) and that asyncpg rejects.
_STRIP_PARAMS = {"schema", "pgbouncer", "connection_limit", "pool_timeout", "connect_timeout", "sslmode"}


def build_engine_args(database_url: str) -> tuple[str, dict]:
    """Turns a Prisma/Supabase style URL into an async SQLAlchemy URL plus connect args."""
    parts = urlsplit(database_url)
    scheme = parts.scheme
    if scheme.startswith("sqlite"):
        return database_url, {}
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = parse_qsl(parts.query, keep_blank_values=True)
    sslmode = next((value for key, value in query if key == "sslmode"), None)
    kept = [(key, value) for key, value in query if key not in _STRIP_PARAMS]
    connect_args: dict = {}
    if sslmode in ("require", "verify-ca", "verify-full", "prefer"):
        connect_args["ssl"] = True
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)), connect_args


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url, connect_args = build_engine_args(get_settings().database_url)
        kwargs: dict = {"connect_args": connect_args}
        if url.startswith("sqlite"):
            kwargs["poolclass"] = StaticPool  # one shared connection, needed for in-memory test databases
        else:
            kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=10)
        _engine = create_async_engine(url, **kwargs)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
