"""Shared OpenAPI response descriptions."""

from ..schemas import ErrorResponse

_DESCRIPTIONS = {
    400: "Validation error (`validation_error`) or malformed JSON (`invalid_json`).",
    401: "Not signed in, or the token is invalid/expired (`unauthorized`).",
    403: "Signed in but not allowed (`forbidden`).",
    404: "Not found (`not_found`).",
    409: "Conflict (`email_taken`).",
    410: "The interview has ended (`interview_ended`).",
    413: "Request body larger than 64 KB (`payload_too_large`).",
    415: "Content-Type must be `application/json` (`unsupported_media_type`).",
    429: "Rate limited (`rate_limited`).",
    503: "A dependency is unavailable (`livekit_unavailable`, `database_unavailable`).",
}


def errors(*codes: int) -> dict[int | str, dict]:
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}
