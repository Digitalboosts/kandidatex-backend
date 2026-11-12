"""Authentication dependencies (the previous backend's requireUser / requireActor)."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .errors import forbidden, unauthorized
from .models import Interview, User, UserRole
from .security import CANDIDATE_AUDIENCE, SESSION_COOKIE, USER_AUDIENCE, USER_SESSION_SECONDS, verify_token

# These two make the "Authorize" button appear in /docs.
_bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth", description="Session token from login/register/join.")
_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False, scheme_name="SessionCookie")

Session = Annotated[AsyncSession, Depends(get_session)]


@dataclass
class Actor:
    kind: str  # "user" | "candidate"
    user: User | None = None
    interview_id: str | None = None
    candidate_name: str | None = None


def _token(
    bearer: HTTPAuthorizationCredentials | None,
    cookie: str | None,
) -> str | None:
    if bearer and bearer.credentials.strip():
        return bearer.credentials.strip()
    return cookie or None


async def _user_from_token(session: AsyncSession, token: str) -> User | None:
    payload = verify_token(token, USER_AUDIENCE)
    if not payload:
        return None
    user = await session.scalar(select(User).where(User.id == payload["sub"]))
    if user is None or user.session_version != payload.get("ver"):
        return None
    return user


async def require_user(
    session: Session,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    cookie: Annotated[str | None, Depends(_cookie)],
) -> User:
    token = _token(bearer, cookie)
    user = await _user_from_token(session, token) if token else None
    if user is None:
        raise unauthorized()
    return user


async def require_actor(
    session: Session,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    cookie: Annotated[str | None, Depends(_cookie)],
) -> Actor:
    """Accepts an interviewer session or a candidate session."""
    token = _token(bearer, cookie)
    if not token:
        raise unauthorized()
    user = await _user_from_token(session, token)
    if user:
        return Actor(kind="user", user=user)
    payload = verify_token(token, CANDIDATE_AUDIENCE)
    if payload and isinstance(payload.get("name"), str):
        return Actor(kind="candidate", interview_id=payload["sub"], candidate_name=payload["name"])
    raise unauthorized()


CurrentUser = Annotated[User, Depends(require_user)]
CurrentActor = Annotated[Actor, Depends(require_actor)]


def assert_can_manage(user: User, interview: Interview) -> None:
    if user.role != UserRole.ADMIN and interview.interviewer_id != user.id:
        raise forbidden()


def assert_can_access(actor: Actor, interview: Interview) -> None:
    """A candidate token only ever opens the interview it was issued for."""
    if actor.kind == "user":
        assert actor.user is not None
        assert_can_manage(actor.user, interview)
    elif actor.interview_id != interview.id:
        raise forbidden()


def set_session_cookie(response, token: str) -> None:  # noqa: ANN001 - starlette Response
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite=settings.cookie_samesite,
        path="/",
        max_age=USER_SESSION_SECONDS,
        domain=settings.cookie_domain,
    )


def clear_session_cookie(response) -> None:  # noqa: ANN001
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        "",
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite=settings.cookie_samesite,
        path="/",
        max_age=0,
        domain=settings.cookie_domain,
    )


__all__ = [
    "Actor",
    "CurrentActor",
    "CurrentUser",
    "Request",
    "Session",
    "assert_can_access",
    "assert_can_manage",
    "clear_session_cookie",
    "require_actor",
    "require_user",
    "set_session_cookie",
]
