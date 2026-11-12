"""Passwords and session tokens.

Token format is unchanged from the previous backend (HS256 JWT, same issuer,
audiences and claims), so tokens signed by either implementation with the same
JWT_SECRET are accepted by both."""

import asyncio
import time
from functools import lru_cache
from typing import Any

import bcrypt
import jwt

from .config import get_settings

ISSUER = "interview-monitoring"
USER_AUDIENCE = "interviewer"
CANDIDATE_AUDIENCE = "candidate"
USER_SESSION_SECONDS = 8 * 60 * 60
CANDIDATE_SESSION_SECONDS = 4 * 60 * 60
BCRYPT_COST = 12
SESSION_COOKIE = "im_session"


def _pw(password: str) -> bytes:
    # bcrypt only reads the first 72 bytes; newer libraries raise instead of truncating.
    return password.encode()[:72]


@lru_cache
def _dummy_hash() -> bytes:
    """Compared against when the email is unknown, so response time doesn't reveal which emails exist."""
    return bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(BCRYPT_COST))


async def hash_password(password: str) -> str:
    hashed = await asyncio.to_thread(bcrypt.hashpw, _pw(password), bcrypt.gensalt(BCRYPT_COST))
    return hashed.decode()


async def verify_password(password: str, password_hash: str | None) -> bool:
    target = password_hash.encode() if password_hash else _dummy_hash()
    try:
        return await asyncio.to_thread(bcrypt.checkpw, _pw(password), target)
    except ValueError:  # malformed stored hash
        return False


def _sign(claims: dict[str, Any], subject: str, audience: str, ttl: int) -> str:
    now = int(time.time())
    payload = {**claims, "sub": subject, "iss": ISSUER, "aud": audience, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")


def sign_user_token(user_id: str, session_version: int) -> str:
    return _sign({"ver": session_version}, user_id, USER_AUDIENCE, USER_SESSION_SECONDS)


def sign_candidate_token(interview_id: str, candidate_name: str) -> str:
    return _sign({"name": candidate_name}, interview_id, CANDIDATE_AUDIENCE, CANDIDATE_SESSION_SECONDS)


def verify_token(token: str, audience: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=ISSUER,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
