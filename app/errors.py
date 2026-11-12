"""One error shape for every failure: {"error": {"code", "message", "details?"}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def unauthorized(message: str = "Authentication required") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "You do not have access to this resource") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(404, "not_found", message)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def _issue_message(error: dict[str, Any]) -> str:
    message = str(error.get("msg", "Invalid value"))
    # Pydantic prefixes messages raised from validators.
    return message.removeprefix("Value error, ")


def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    if any(error.get("type") == "json_invalid" for error in errors):
        return JSONResponse(error_body("invalid_json", "Request body is not valid JSON"), status_code=400)
    issues = [
        {
            "code": error.get("type"),
            "path": [part for part in error.get("loc", ()) if part not in ("body",)],
            "message": _issue_message(error),
        }
        for error in errors
    ]
    return JSONResponse(error_body("validation_error", "Invalid request", issues), status_code=400)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status)

    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed"}
        code = codes.get(exc.status_code, "request_failed")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(error_body("internal_error", "Something went wrong"), status_code=500)
