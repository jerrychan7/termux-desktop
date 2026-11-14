"""Stop a recorded session by signalling only its verified process group."""

from __future__ import annotations

import os
import signal
import sys
from typing import TextIO

from ..config import Paths
from ..state import read_session_state, remove_session_state, state_lock
from .constants import (
    KILL_TIMEOUT_SECONDS,
    LOCK_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TERMINATE_TIMEOUT_SECONDS,
)
from .display import _clear_stale_display_files_after_stop
from .errors import SessionError, StopError
from .runtime import SessionRuntime
from .verify import (
    VERIFICATION_STALE,
    VERIFICATION_UNVERIFIABLE,
    verify_session,
)


def stop_session(
    paths: Paths,
    *,
    runtime: SessionRuntime | None = None,
    out: TextIO | None = None,
    terminate_timeout: float = TERMINATE_TIMEOUT_SECONDS,
    kill_timeout: float = KILL_TIMEOUT_SECONDS,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> None:
    """Stop the recorded session by signalling only its verified process group.

    When the state records no live process group, the stale state file is
    removed and the call succeeds. When the process group cannot be verified,
    nothing is signalled and the state file is kept.
    """
    output = sys.stdout if out is None else out
    session_runtime = runtime if runtime is not None else SessionRuntime()

    with state_lock(paths, timeout=lock_timeout):
        state = read_session_state(paths.session_file)
        if state is None:
            print("No desk session is running.", file=output)
            return

        verification = verify_session(state, session_runtime)
        if verification.state == VERIFICATION_UNVERIFIABLE:
            raise StopError(
                f"cannot verify the recorded session: {verification.detail}. "
                f"desk refuses to signal process group {state.pgid}. "
                f"The state file {paths.session_file} is kept."
            )
        if verification.state == VERIFICATION_STALE:
            _clear_stale_display_files_after_stop(paths, session_runtime, out=output)
            try:
                remove_session_state(paths.session_file, session_id=state.session_id)
            except OSError as error:
                raise StopError(
                    f"the session is not running, but the state file "
                    f"{paths.session_file} could not be removed: {error}"
                ) from error
            print(
                f"Session is not running; removed the stale state file {paths.session_file}.",
                file=output,
            )
            return

        print(f"Stopping the session (pid {state.pid}, pgid {state.pgid}).", file=output)
        cleaned, residual = _terminate_group(
            state.pgid,
            session_runtime,
            terminate_timeout,
            kill_timeout,
        )
        if not cleaned:
            remaining = ", ".join(str(pid) for pid in residual)
            raise StopError(
                f"the process group {state.pgid} still has live processes: {remaining}. "
                f"The state file {paths.session_file} is kept. "
                "desk does not search for processes by name."
            )

        _clear_stale_display_files_after_stop(paths, session_runtime, out=output)

        try:
            removed = remove_session_state(paths.session_file, session_id=state.session_id)
        except OSError as error:
            raise StopError(
                f"the process group {state.pgid} has no live processes, but the state "
                f"file {paths.session_file} could not be removed: {error}"
            ) from error
        if not removed:
            raise StopError(
                f"the process group {state.pgid} has no live processes, but the state "
                f"file {paths.session_file} was not removed because it is missing or "
                "records another session. Check the state file yourself."
            )
        print("Session stopped.", file=output)


def _terminate_group(
    pgid: int,
    runtime: SessionRuntime,
    terminate_timeout: float,
    kill_timeout: float,
) -> tuple[bool, tuple[int, ...]]:
    """Signal the process group and report whether it became empty."""
    _refuse_unsafe_group(pgid)
    if not runtime.group_members(pgid):
        return True, ()

    _signal_group(pgid, signal.SIGTERM, runtime)
    if _wait_for_group_exit(pgid, runtime, terminate_timeout):
        return True, ()

    _signal_group(pgid, signal.SIGKILL, runtime)
    if _wait_for_group_exit(pgid, runtime, kill_timeout):
        return True, ()

    return False, runtime.group_members(pgid)


def _wait_for_group_exit(pgid: int, runtime: SessionRuntime, timeout_s: float) -> bool:
    """Poll the process group until it is empty or the timeout expires."""
    deadline = runtime.clock() + timeout_s
    while True:
        if not runtime.group_members(pgid):
            return True
        now = runtime.clock()
        if now >= deadline:
            return False
        runtime.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - now)))


def _signal_group(pgid: int, signum: int, runtime: SessionRuntime) -> None:
    _refuse_unsafe_group(pgid)
    try:
        runtime.signal_group(pgid, signum)
    except ProcessLookupError:
        return
    except OSError as error:
        raise SessionError(
            f"could not send signal {signum} to process group {pgid}: {error}"
        ) from error


def _refuse_unsafe_group(pgid: int) -> None:
    """Reject process group numbers that could signal the wrong processes."""
    if pgid <= 1:
        raise SessionError(
            f"refusing to signal process group {pgid}: the value is not a usable process group"
        )
    if pgid == os.getpgrp():
        raise SessionError(
            f"refusing to signal process group {pgid}: it is the process group of desk itself"
        )
