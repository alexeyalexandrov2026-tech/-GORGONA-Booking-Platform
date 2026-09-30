"""Typed domain errors. The API layer maps them to HTTP; nothing here knows about HTTP."""

from typing import ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "DOMAIN_ERROR"

    def __init__(self, message: str, **details: str | int | None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    code = "NOT_FOUND"


class DatabaseUnavailableError(DomainError):
    code = "DATABASE_UNAVAILABLE"
