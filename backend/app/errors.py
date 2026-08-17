"""Domain and application level exceptions.

Every error carries a stable machine-readable ``code`` so the SPA can react to
specific failures instead of pattern-matching on prose.
"""

from __future__ import annotations

from typing import Any


class TradeCognitionError(Exception):
    """Base class for all application errors."""

    status_code: int = 400
    code: str = "error"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class ValidationError(TradeCognitionError):
    status_code = 422
    code = "validation_error"


class AuthError(TradeCognitionError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(TradeCognitionError):
    status_code = 403
    code = "forbidden"


class NotFoundError(TradeCognitionError):
    status_code = 404
    code = "not_found"


class ConflictError(TradeCognitionError):
    status_code = 409
    code = "conflict"


class ServiceUnavailableError(TradeCognitionError):
    status_code = 503
    code = "service_unavailable"


class RuleViolationError(TradeCognitionError):
    """Raised when the rules engine blocks an action."""

    status_code = 422
    code = "rules_rejected"


class Mt5Error(TradeCognitionError):
    """Raised when the MT5 terminal / gateway fails."""

    status_code = 502
    code = "mt5_error"


class Mt5NotConnectedError(Mt5Error):
    code = "mt5_not_connected"
    status_code = 409
