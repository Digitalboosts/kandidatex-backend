import hmac

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, update

from ..config import get_settings
from ..deps import CurrentUser, Session, clear_session_cookie, set_session_cookie
from ..errors import ApiError
from ..models import User, UserRole
from ..rate_limit import client_ip, rate_limit
from ..schemas import AuthResponse, LoginRequest, OkResponse, RegisterRequest, UserEnvelope
from ..security import hash_password, sign_user_token, verify_password
from ..serializers import user_dto
from ._docs import errors

router = APIRouter(prefix="/api/auth", tags=["Auth"])

TEN_MINUTES_MS = 10 * 60 * 1000


def _is_valid_admin_code(code: str | None) -> bool:
    expected = get_settings().admin_registration_code
    if not code or not expected:
        return False
    return hmac.compare_digest(code.encode(), expected.encode())


@router.post(
    "/register",
    status_code=201,
    response_model=AuthResponse,
    summary="Create an interviewer account",
    description="Creates an `INTERVIEWER` (or `ADMIN` when `adminCode` matches the server's code), "
    "signs the user in and sets the `im_session` cookie. Limited to 10 per hour per IP.",
    responses=errors(400, 403, 409, 415, 429),
)
async def register(body: RegisterRequest, request: Request, response: Response, session: Session) -> AuthResponse:
    rate_limit(f"register:{client_ip(request)}", 10, 60 * 60 * 1000)
    if body.admin_code is not None and not _is_valid_admin_code(body.admin_code):
        raise ApiError(403, "invalid_admin_code", "Invalid admin registration code")
    if await session.scalar(select(User.id).where(User.email == body.email)):
        raise ApiError(409, "email_taken", "An account with this email already exists")
    user = User(
        name=body.name,
        email=body.email,
        password_hash=await hash_password(body.password),
        role=UserRole.ADMIN if body.admin_code else UserRole.INTERVIEWER,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    token = sign_user_token(user.id, user.session_version)
    set_session_cookie(response, token)
    return AuthResponse(token=token, user=user_dto(user))


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Sign in",
    description="Returns a session token and sets the `im_session` cookie. A wrong password and an unknown "
    "email return the same error. Limited to 30 per 10 minutes per IP and 10 per 10 minutes per email.",
    responses=errors(400, 401, 415, 429),
)
async def login(body: LoginRequest, request: Request, response: Response, session: Session) -> AuthResponse:
    rate_limit(f"login-ip:{client_ip(request)}", 30, TEN_MINUTES_MS)
    rate_limit(f"login-email:{body.email}", 10, TEN_MINUTES_MS)
    user = await session.scalar(select(User).where(User.email == body.email))
    valid = await verify_password(body.password, user.password_hash if user else None)
    if user is None or not valid:
        raise ApiError(401, "invalid_credentials", "Incorrect email or password")
    token = sign_user_token(user.id, user.session_version)
    set_session_cookie(response, token)
    return AuthResponse(token=token, user=user_dto(user))


@router.post(
    "/logout",
    response_model=OkResponse,
    summary="Sign out everywhere",
    description="Invalidates **every** session this user holds (not just the current one) and clears the cookie.",
    responses=errors(401),
)
async def logout(user: CurrentUser, response: Response, session: Session) -> OkResponse:
    await session.execute(update(User).where(User.id == user.id).values(session_version=User.session_version + 1))
    await session.commit()
    clear_session_cookie(response)
    return OkResponse()


@router.get(
    "/me",
    response_model=UserEnvelope,
    summary="Current user",
    responses=errors(401),
)
async def me(user: CurrentUser) -> UserEnvelope:
    return UserEnvelope(user=user_dto(user))
