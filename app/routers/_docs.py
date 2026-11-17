"""Shared OpenAPI response descriptions."""

from ..schemas import ErrorResponse

_DESCRIPTIONS = {
    400: "Validation error (`validation_error`) or malformed JSON (`invalid_json`).",
    401: "Not signed in, or the token is invalid/expired (`unauthorized`).",
    402: "Subscription needed or the monthly interview allowance is used up (`subscription_required`, `interview_limit_reached`).",
    403: "Signed in but not allowed (`forbidden`).",
    404: "Not found (`not_found`).",
    409: "Conflict (`email_taken`, `recording_closed`).",
    416: "Invalid or unsatisfiable `Range` (`range_not_satisfiable`).",
    410: "The interview has ended (`interview_ended`).",
    413: "Request body too large (64 KB; 8 MB for screen analysis) (`payload_too_large`).",
    415: "Content-Type must be `application/json` (`unsupported_media_type`).",
    429: "Rate limited (`rate_limited`).",
    502: "The AI model failed (`ai_unavailable`, `ai_invalid_response`).",
    504: "The AI model timed out (`ai_timeout`).",
    503: "A dependency is unavailable (`livekit_unavailable`, `database_unavailable`, `ai_not_configured`).",
}


def errors(*codes: int) -> dict[int | str, dict]:
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}
