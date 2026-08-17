"""MT5 login credentials as they travel through the application."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Mt5Credentials:
    """A terminal login.

    ``password`` is only ever held in memory for the duration of a call; it is
    stored encrypted at rest (see :mod:`app.core.crypto`).  ``__repr__`` is
    overridden so the secret cannot leak into logs or tracebacks.
    """

    login: int
    password: str
    server: str
    terminal_path: str = ""

    def __repr__(self) -> str:
        return f"Mt5Credentials(login={self.login}, server={self.server!r}, password=***)"

    __str__ = __repr__

    @property
    def key(self) -> tuple[int, str]:
        """Identity of the terminal session these credentials establish."""
        return (self.login, self.server)
