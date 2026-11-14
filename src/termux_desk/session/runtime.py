"""The process, signal, and clock operations that the session code calls.

SessionRuntime records those operations as fields, so the tests can replace
each one with a stand-in and no real process is signalled.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol

from ..config import TERMUX_X11_ACTIVITY
from .display import unix_socket_accepts_connection, x11_socket_ready
from .errors import SessionError
from .process import (
    StartedProcess,
    has_x_server_process,
    has_xfce_process,
    is_x_server_process,
    pid_alive,
    process_group_members,
    process_start_time,
    read_boot_id,
    session_in_group,
)


class Pollable(Protocol):
    """The part of subprocess.Popen that desk reads after a start."""

    def poll(self) -> int | None:
        """Return the exit status, or None while the process runs."""


START_INTERRUPT_SIGNALS = frozenset(
    {signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGQUIT}
)


def _block_start_signals() -> set[signal.Signals] | None:
    """Block the signals that can interrupt desk start, return the old mask."""
    try:
        return signal.pthread_sigmask(signal.SIG_BLOCK, START_INTERRUPT_SIGNALS)
    except (AttributeError, OSError, ValueError):
        return None


def _restore_signal_mask(previous_mask: set[signal.Signals] | None) -> None:
    if previous_mask is None:
        return
    try:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    except (AttributeError, OSError, ValueError):
        return


def _child_signal_mask_restorer(
    previous_mask: set[signal.Signals] | None,
) -> Callable[[], None] | None:
    """Build the child-side call that restores the mask inherited on fork."""
    if previous_mask is None:
        return None

    def restore() -> None:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    return restore


def spawn_session_script(
    script_path: Path,
    session_id: str,
    *,
    bash_path: Path,
    log_path: Path,
    env: Mapping[str, str],
    record: Callable[[StartedProcess], None],
) -> StartedProcess:
    """Start session.sh in a new session and process group.

    The script is started with the prefix bash explicitly, so a prefix path
    that contains spaces works even though a shebang cannot hold such a path.
    HUP, TERM, INT, and QUIT are blocked from before the fork until the child
    pid and process group are recorded. A pending signal then reaches the
    caller, which can clean up the group that record has made visible.
    """
    try:
        log_handle = log_path.open("a", encoding="utf-8")
    except OSError as error:
        raise SessionError(f"could not open the session log {log_path}: {error}") from error

    started: StartedProcess | None = None
    previous_mask: set[signal.Signals] | None = None
    try:
        log_handle.write(
            f"\n=== desk start {current_time().isoformat(timespec='seconds')} "
            f"session {session_id} ===\n"
        )
        log_handle.flush()

        previous_mask = _block_start_signals()
        try:
            popen = subprocess.Popen(
                [str(bash_path), str(script_path), session_id],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=dict(env),
                start_new_session=True,
                close_fds=True,
                preexec_fn=_child_signal_mask_restorer(previous_mask),
            )
        except OSError as error:
            raise SessionError(
                f"could not start {bash_path} {script_path}: {error}"
            ) from error

        # Record the group as soon as the pid exists. The process group id of
        # a new session is its pid, so the first record is already usable.
        started = StartedProcess(pid=popen.pid, pgid=popen.pid, popen=popen)
        record(started)
        try:
            pgid = os.getpgid(popen.pid)
        except OSError:
            pgid = popen.pid
        started = StartedProcess(pid=popen.pid, pgid=pgid, popen=popen)
        record(started)
        return started
    except BaseException:
        if started is not None:
            _kill_group_quietly(started.pgid)
        raise
    finally:
        log_handle.close()
        _restore_signal_mask(previous_mask)


def _kill_group_quietly(pgid: int) -> None:
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        return


def start_termux_x11_app(am_path: str) -> tuple[int, str]:
    """Ask Android to show the Termux:X11 app and return its status and output."""
    completed = subprocess.run(
        [am_path, "start", "--user", "0", "-n", TERMUX_X11_ACTIVITY],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def current_time() -> datetime:
    """Return the current local time with its UTC offset."""
    return datetime.now().astimezone()


@dataclass(frozen=True)
class SessionRuntime:
    """The process and file system operations that session code depends on.

    Unit tests replace individual fields with stand-ins so no real process is
    signalled and no real socket is opened.
    """

    spawn: Callable[..., StartedProcess] = spawn_session_script
    socket_ready: Callable[[Path], bool] = x11_socket_ready
    socket_in_use: Callable[[Path], bool] = unix_socket_accepts_connection
    group_members: Callable[[int], tuple[int, ...]] = process_group_members
    xfce_present: Callable[[int], bool] = has_xfce_process
    x11_present: Callable[[int], bool] = has_x_server_process
    x_server_process: Callable[[int], bool] = is_x_server_process
    session_in_group: Callable[..., bool] = session_in_group
    read_boot_id: Callable[[], str] = read_boot_id
    start_time: Callable[[int], int | None] = process_start_time
    signal_group: Callable[[int, int], None] = os.killpg
    start_app: Callable[[str], tuple[int, str]] = start_termux_x11_app
    process_alive: Callable[[int], bool] = pid_alive
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = current_time
