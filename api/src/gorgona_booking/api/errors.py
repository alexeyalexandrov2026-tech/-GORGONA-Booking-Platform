"""One error envelope for every failure: {"error": {"code", "message", "request_id", ...}}.

Raw database or provider errors never reach the client.
"""

import logging
from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from gorgona_booking.api.request_id import get_request_id
from gorgona_booking.auth.principal import IdentityNotLinkedError, UserDisabledError
from gorgona_booking.auth.verifier import (
    AuthenticationRequiredError,
    AuthNotConfiguredError,
    InvalidTokenError,
)
from gorgona_booking.booking.models import (
    HoldExpiredError,
    IdempotencyKeyReusedError,
    InvalidTransitionError,
    SlotConflictError,
)
from gorgona_booking.errors import (
    ConflictError,
    DatabaseUnavailableError,
    DomainError,
    InvalidReferenceError,
    NotFoundError,
)
from gorgona_booking.identity import invitations as inv
from gorgona_booking.tenancy.authorization import (
    PermissionDeniedError,
    TenantAccessDeniedError,
    TenantSuspendedError,
)

logger = logging.getLogger("gorgona_booking.api")

# Most specific class wins (looked up along the exception's MRO).
DOMAIN_ERROR_STATUS: dict[type[DomainError], int] = {
    DomainError: 422,
    NotFoundError: 404,
    DatabaseUnavailableError: 503,
    SlotConflictError: 409,
    InvalidTransitionError: 409,
    HoldExpiredError: 409,
    IdempotencyKeyReusedError: 422,
    ConflictError: 409,
    InvalidReferenceError: 422,
    AuthenticationRequiredError: 401,
    InvalidTokenError: 401,
    AuthNotConfiguredError: 503,
    IdentityNotLinkedError: 403,
    UserDisabledError: 403,
    TenantAccessDeniedError: 403,
    TenantSuspendedError: 403,
    PermissionDeniedError: 403,
    inv.EmailNotVerifiedError: 403,
    inv.InvitationEmailMismatchError: 403,
    inv.InvitationNotFoundError: 404,
    inv.InvitationAlreadyUsedError: 409,
    inv.InvitationNotUsableError: 409,
    inv.InvitationPendingError: 409,
    inv.AlreadyMemberError: 409,
    inv.CannotModifySelfError: 409,
    inv.LastOwnerError: 409,
    inv.MembershipRevokedError: 409,
}

_HTTP_STATUS_CODES: Mapping[int, str] = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


def register_domain_error(error_type: type[DomainError], status_code: int) -> None:
    DOMAIN_ERROR_STATUS[error_type] = status_code


def status_for(error: DomainError) -> int:
    for klass in type(error).__mro__:
        if klass in DOMAIN_ERROR_STATUS:
            return DOMAIN_ERROR_STATUS[klass]
    return 422


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": get_request_id(request),
    }
    if details:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, DomainError):
        raise TypeError(exc)
    status = status_for(exc)
    headers = None
    if status == 401:
        # RFC 6750: never echo the token; name the error only when one was presented.
        challenge = 'Bearer realm="gorgona-booking"'
        if isinstance(exc, InvalidTokenError):
            challenge += ', error="invalid_token"'
        headers = {"WWW-Authenticate": challenge}
    return error_response(request, status, exc.code, exc.message, exc.details, headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise TypeError(exc)
    # Deliberately omit the rejected "input" values: they may contain personal data.
    details = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return error_response(request, 422, "INVALID_REQUEST", "Request validation failed", details)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise TypeError(exc)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return error_response(request, exc.status_code, code, message)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error",
        exc_info=exc,
        extra={"request_id": get_request_id(request), "error_type": type(exc).__name__},
    )
    return error_response(request, 500, "INTERNAL_ERROR", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
