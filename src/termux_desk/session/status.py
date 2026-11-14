"""Report the recorded sessions and whether each one still runs."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..config import Paths
from ..state import read_session_state
from .runtime import SessionRuntime
from .verify import (
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    verify_session,
)

SESSION_STATE_PATTERN = re.compile(r"^session(?:-(?P<number>\d+))?\.json$")


@dataclass(frozen=True)
class StatusReport:
    """What desk status found and what it printed."""

    running: bool
    lines: tuple[str, ...]


def session_displays(paths: Paths) -> tuple[str, ...]:
    """Return the displays whose state files exist, from :0 to :N."""
    try:
        names = os.listdir(paths.state_dir)
    except OSError:
        return ()
    numbers: set[int] = set()
    for name in names:
        match = SESSION_STATE_PATTERN.fullmatch(name)
        if match is None:
            continue
        number = match.group("number")
        numbers.add(0 if number is None else int(number))
    return tuple(f":{number}" for number in sorted(numbers))


def session_status(
    paths: Paths,
    *,
    runtime: SessionRuntime | None = None,
) -> StatusReport:
    """Report whether the recorded session still owns a live process group."""
    session_runtime = runtime if runtime is not None else SessionRuntime()
    state = read_session_state(paths.session_file)
    if state is None:
        return StatusReport(
            running=False,
            lines=(
                "Session is not running.",
                f"  state file: {paths.session_file} (not created)",
            ),
        )

    verification = verify_session(state, session_runtime)
    details = (
        f"  display: {state.display}",
        f"  pid: {state.pid}",
        f"  pgid: {state.pgid}",
        f"  started at: {state.started_at}",
        f"  session log: {state.log_path}",
        f"  session script: {state.session_script}",
    )
    if verification.state == VERIFICATION_RUNNING:
        if state.status == "starting":
            return StatusReport(
                running=True,
                lines=("Session is starting.",) + details,
            )
        if state.status == "failed":
            return StatusReport(
                running=True,
                lines=("Session is still running after a failed start.",) + details,
            )
        return StatusReport(
            running=True,
            lines=("Session is running.",) + details,
        )
    if verification.state == VERIFICATION_STALE:
        return StatusReport(
            running=False,
            lines=(
                "Session is not running.",
                f"  {verification.detail}.",
                "  run 'desk stop' to remove the stale state file.",
            ),
        )
    return StatusReport(
        running=False,
        lines=(
            "Session is not running.",
            f"  desk cannot verify the recorded session: {verification.detail}.",
            f"  the state file {paths.session_file} is kept; check process group "
            f"{state.pgid} yourself.",
        ),
    )
