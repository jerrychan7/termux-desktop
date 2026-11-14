"""The errors that session code raises.

SessionError is the base class for a session action that failed. StartError,
StopError, and ProcessScanError name the three failures that callers handle
separately. StartInterrupted is not a SessionError: a HUP, TERM, or QUIT
signal interrupts desk start and the caller cleans up before it exits.
"""

from __future__ import annotations

from ..config import DeskError


class SessionError(DeskError):
    """A session action could not be carried out."""


class StartError(SessionError):
    """The desktop session could not be started."""


class StopError(SessionError):
    """The desktop session could not be stopped cleanly."""


class ProcessScanError(SessionError):
    """The process table could not be read, so session membership is unknown."""


class StartInterrupted(BaseException):
    """A HUP, TERM, or QUIT signal interrupted desk start after the script started."""

    def __init__(self, signum: int):
        super().__init__(f"signal {signum}")
        self.signum = signum
