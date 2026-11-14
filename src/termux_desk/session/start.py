"""Start one session: check the environment, spawn the script, record the result."""

from __future__ import annotations

import os
import signal
import stat
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from ..config import DeskError, Paths, resolve_program
from ..state import (
    SessionState,
    StateError,
    read_session_state,
    remove_session_state,
    state_lock,
    write_session_state,
)
from .constants import (
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_RESET,
    BACKEND_TERMUX_X11,
    BACKEND_VNC,
    BACKENDS,
    DESKTOP_READY_TIMEOUT_SECONDS,
    KILL_TIMEOUT_SECONDS,
    LOCK_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    TERMINATE_TIMEOUT_SECONDS,
)
from .display import clear_stale_display_files, display_in_use
from .errors import SessionError, StartError, StartInterrupted
from .process import StartedProcess
from .runtime import SessionRuntime
from .script import missing_programs, session_environment, write_session_script
from .stop import _terminate_group
from .verify import VERIFICATION_RUNNING, VERIFICATION_UNVERIFIABLE, verify_session


def _ensure_runtime_directory(paths: Paths) -> None:
    """Create and validate the private XDG runtime directory."""
    runtime_dir = paths.runtime_dir
    try:
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise StartError(
            f"could not create the parent of the XDG runtime directory "
            f"{runtime_dir}: {error}"
        ) from error

    try:
        info = os.lstat(runtime_dir)
    except FileNotFoundError:
        try:
            runtime_dir.mkdir(mode=0o700)
        except OSError as error:
            raise StartError(
                f"could not create the XDG runtime directory {runtime_dir}: {error}"
            ) from error
        try:
            info = os.lstat(runtime_dir)
        except OSError as error:
            raise StartError(
                f"could not inspect the XDG runtime directory {runtime_dir}: {error}"
            ) from error
    except OSError as error:
        raise StartError(
            f"could not inspect the XDG runtime directory {runtime_dir}: {error}"
        ) from error

    if stat.S_ISLNK(info.st_mode):
        raise StartError(
            f"the XDG runtime directory {runtime_dir} is a symbolic link; "
            "desk will not use it"
        )
    if not stat.S_ISDIR(info.st_mode):
        raise StartError(
            f"the XDG runtime directory {runtime_dir} is not a directory"
        )

    current_uid = os.getuid()
    if info.st_uid != current_uid:
        raise StartError(
            f"the XDG runtime directory {runtime_dir} is owned by UID {info.st_uid}, "
            f"not the current UID {current_uid}"
        )

    if stat.S_IMODE(info.st_mode) != 0o700:
        try:
            runtime_dir.chmod(0o700)
        except OSError as error:
            raise StartError(
                f"could not set the permissions of the XDG runtime directory "
                f"{runtime_dir} to 0700: {error}"
            ) from error
        try:
            mode = stat.S_IMODE(os.lstat(runtime_dir).st_mode)
        except OSError as error:
            raise StartError(
                f"could not inspect the XDG runtime directory {runtime_dir}: {error}"
            ) from error
        if mode != 0o700:
            raise StartError(
                f"the XDG runtime directory {runtime_dir} still has permissions "
                f"{mode:04o} after desk set them to 0700"
            )


def _show_display_app(
    paths: Paths,
    environment: Mapping[str, str],
    runtime: SessionRuntime,
) -> None:
    """Ask Android to put the Termux:X11 app in front of an existing display.

    Android can remove the app from the foreground without stopping the X
    server or XFCE. Starting the session again in that state means asking for
    the app, not starting a second desktop.
    """
    am_path = resolve_program("am", paths.prefix, environment.get("PATH"))
    if am_path is None:
        raise StartError(
            "missing programs: am. desk start does not install packages; "
            "run 'desk setup' first."
        )
    try:
        returncode, text = runtime.start_app(am_path)
    except OSError as error:
        raise StartError(f"could not run {am_path}: {error}") from error
    if returncode != 0:
        lines = (text or "").strip().splitlines()
        detail = lines[0] if lines else f"{am_path} exited with status {returncode}"
        raise StartError(
            f"the Termux:X11 app did not come to the front: {detail}. "
            "Open the Termux:X11 app yourself; Android can also block starting "
            "it from the background."
        )


def _print_session_summary(output: TextIO, state: SessionState) -> None:
    """Print the recorded facts of one session below the status word."""
    print(f"  display:     {state.display}", file=output)
    print(f"  pid:         {state.pid}", file=output)
    print(f"  pgid:        {state.pgid}", file=output)
    print(f"  session log: {state.log_path}", file=output)


def start_session(
    paths: Paths,
    *,
    backend: str = BACKEND_TERMUX_X11,
    vnc_arguments: Sequence[str] = (),
    runtime: SessionRuntime | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    color: bool = False,
    ready_timeout: float = DESKTOP_READY_TIMEOUT_SECONDS,
    terminate_timeout: float = TERMINATE_TIMEOUT_SECONDS,
    kill_timeout: float = KILL_TIMEOUT_SECONDS,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> SessionState:
    """Start the desktop session and wait until it is ready.

    This function never installs packages. When a program is missing it
    raises StartError and asks the user to run desk setup. The state lock is
    held while desk checks for an existing session, starts the script, and
    writes the starting state. The readiness wait runs without the lock so
    desk stop can still remove a session that hangs while it starts.
    When color is true, only the Starting and running keywords carry ANSI
    color; every other line stays plain.
    """
    if backend not in BACKENDS:
        raise SessionError(f"unknown desktop backend {backend!r}")
    output = sys.stdout if out is None else out
    session_runtime = runtime if runtime is not None else SessionRuntime()
    environment = os.environ if environ is None else environ

    if paths.prefix is None or not paths.prefix.is_dir():
        raise StartError("PREFIX is not set to an existing directory. desk start must run inside Termux.")

    missing = missing_programs(paths, environment, backend=backend)
    if missing:
        setup_hint = "desk vnc setup" if backend == BACKEND_VNC else "desk setup"
        raise StartError(
            "missing programs: "
            + ", ".join(missing)
            + f". desk start does not install packages; run '{setup_hint}' first."
        )

    bash_program = resolve_program("bash", paths.prefix, environment.get("PATH"))
    if bash_program is None:
        raise StartError(
            "missing programs: bash. desk start does not install packages; "
            "run 'desk setup' first."
        )

    _ensure_runtime_directory(paths)

    session_id = uuid.uuid4().hex
    started_box: list[StartedProcess] = []
    started: StartedProcess | None = None
    state: SessionState | None = None

    with _start_signal_guard():
        try:
            with state_lock(paths, timeout=lock_timeout):
                existing = read_session_state(paths.session_file)
                if existing is not None:
                    verification = verify_session(existing, session_runtime)
                    if verification.state == VERIFICATION_RUNNING:
                        if existing.backend == BACKEND_TERMUX_X11:
                            _show_display_app(paths, environment, session_runtime)
                        print(
                            f"Session is already {ANSI_GREEN}running{ANSI_RESET}."
                            if color
                            else "Session is already running.",
                            file=output,
                        )
                        if existing.backend == BACKEND_TERMUX_X11:
                            print("Asked Android to show the Termux:X11 app.", file=output)
                        _print_session_summary(output, existing)
                        print("Stop it with 'desk stop'.", file=output)
                        return existing
                    if verification.state == VERIFICATION_UNVERIFIABLE:
                        raise StartError(
                            f"cannot verify the recorded session: {verification.detail}. "
                            "desk start refuses to continue while the state file "
                            f"{paths.session_file} records process group {existing.pgid}. "
                            "Check that process group yourself before you remove the state file."
                        )
                    try:
                        remove_session_state(
                            paths.session_file, session_id=existing.session_id
                        )
                    except OSError as error:
                        raise StartError(
                            f"could not remove the stale state file "
                            f"{paths.session_file}: {error}"
                        ) from error
                    print(f"Removed the stale state file {paths.session_file}.", file=output)

                in_use = display_in_use(paths, session_runtime)
                if in_use is not None:
                    raise StartError(
                        f"display {paths.display} is already in use: {in_use}. "
                        "Stop the other X server before running desk start."
                    )
                clear_stale_display_files(paths, session_runtime, out=output)

                script_path = write_session_script(
                    paths, backend=backend, vnc_arguments=vnc_arguments
                )
                boot_id = session_runtime.read_boot_id()

                print(
                    f"{ANSI_CYAN}Starting{ANSI_RESET} the desktop session."
                    if color
                    else "Starting the desktop session.",
                    file=output,
                )
                print(f"  display:        {paths.display}", file=output)
                print(f"  session script: {script_path}", file=output)
                print(f"  session log:    {paths.session_log}", file=output)

                try:
                    session_runtime.spawn(
                        script_path,
                        session_id,
                        bash_path=Path(bash_program),
                        log_path=paths.session_log,
                        env=session_environment(paths, session_id, environment),
                        record=started_box.append,
                    )
                except OSError as error:
                    raise StartError(f"could not start {script_path}: {error}") from error

                if not started_box:
                    raise StartError(
                        "the session script did not report its process group."
                    )
                started = started_box[-1]
                if started.pid <= 1 or started.pgid <= 1:
                    raise StartError(
                        f"the session script returned an unusable PID {started.pid} "
                        f"and PGID {started.pgid}."
                    )

                leader_starttime = session_runtime.start_time(started.pid)
                if leader_starttime is None:
                    raise StartError(
                        f"could not read the start time of the session script process "
                        f"{started.pid}. See the session log at {paths.session_log}."
                    )

                state = SessionState(
                    status="starting",
                    display=paths.display,
                    backend=backend,
                    pid=started.pid,
                    pgid=started.pgid,
                    started_at=session_runtime.now().isoformat(timespec="seconds"),
                    session_id=session_id,
                    boot_id=boot_id,
                    leader_starttime=leader_starttime,
                    session_script=str(script_path),
                    log_path=str(paths.session_log),
                    x11_socket=str(paths.x11_socket),
                )
                _write_state(paths, state)

            ready, detail = _wait_for_desktop(
                paths,
                started,
                session_runtime,
                timeout_s=ready_timeout,
            )
            if not ready:
                raise StartError(f"{detail}. See the session log at {paths.session_log}.")

            with state_lock(paths, timeout=lock_timeout):
                current = read_session_state(paths.session_file)
                if current is None or current.session_id != session_id:
                    raise StartError("the session was stopped while it was starting.")
                state = replace(state, status="running")
                _write_state(paths, state)
        except BaseException as error:
            cleaned = True
            residual: tuple[int, ...] = ()
            cleanup_problem: DeskError | None = None
            effective_started = started_box[-1] if started_box else started
            if effective_started is not None and effective_started.pgid > 1:
                cleaned, residual, cleanup_problem = _cleanup_failed_start(
                    paths,
                    session_runtime,
                    effective_started,
                    state,
                    session_id,
                    terminate_timeout,
                    kill_timeout,
                    lock_timeout,
                )
            if cleanup_problem is not None:
                raise StartError(
                    f"{error}. The cleanup also failed: {cleanup_problem}"
                ) from error
            if effective_started is not None and not cleaned:
                remaining = ", ".join(str(pid) for pid in residual)
                if paths.session_file.exists():
                    location = "The state file was kept; run 'desk stop' to retry the cleanup."
                else:
                    location = (
                        "desk could not write the state file "
                        f"{paths.session_file}; check process group "
                        f"{effective_started.pgid} yourself."
                    )
                raise StartError(
                    f"{error}. The process group {effective_started.pgid} still has processes: "
                    f"{remaining}. {location}"
                ) from error
            raise

    print(
        f"Session is {ANSI_GREEN}running{ANSI_RESET}."
        if color
        else "Session is running.",
        file=output,
    )
    _print_session_summary(output, state)
    print("Stop it with 'desk stop'.", file=output)
    return state


class _StartSignalGuard:
    """Install temporary HUP, TERM, and QUIT handlers for one desk start.

    The spawn function blocks these signals around the fork and the group
    record. The handlers turn a pending signal into StartInterrupted, so the
    caller can clean up the process group instead of dying on the default
    action of SIGQUIT.
    """

    def __init__(self) -> None:
        self.previous: dict[int, object] = {}

    def __enter__(self) -> "_StartSignalGuard":
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, _raise_start_interrupted)
            except (ValueError, OSError):
                continue
            self.previous[signum] = previous
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        for signum, handler in self.previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError, TypeError):
                continue


def _start_signal_guard() -> _StartSignalGuard:
    return _StartSignalGuard()


def _raise_start_interrupted(signum, frame) -> None:
    raise StartInterrupted(signum)


def _cleanup_failed_start(
    paths: Paths,
    runtime: SessionRuntime,
    started: StartedProcess,
    state: SessionState | None,
    session_id: str,
    terminate_timeout: float,
    kill_timeout: float,
    lock_timeout: float,
) -> tuple[bool, tuple[int, ...], DeskError | None]:
    """Stop the process group of a start that did not finish.

    The state file is removed only while it still records this session. When
    the state lock cannot be taken, the process group is signalled anyway and
    the lock problem is reported.
    """
    problem: DeskError | None = None
    try:
        with state_lock(paths, timeout=lock_timeout):
            cleaned, residual = _terminate_group(
                started.pgid, runtime, terminate_timeout, kill_timeout
            )
            if cleaned:
                try:
                    remove_session_state(paths.session_file, session_id=session_id)
                except OSError as error:
                    problem = SessionError(
                        f"could not remove the state file {paths.session_file}: {error}"
                    )
            else:
                _keep_failed_state(paths, state)
        return cleaned, residual, problem
    except DeskError as error:
        problem = error

    try:
        cleaned, residual = _terminate_group(
            started.pgid, runtime, terminate_timeout, kill_timeout
        )
    except DeskError as error:
        return False, (), error

    if cleaned:
        try:
            remove_session_state(paths.session_file, session_id=session_id)
        except OSError:
            pass
        return True, (), None
    return False, residual, problem


def _keep_failed_state(paths: Paths, state: SessionState | None) -> None:
    """Record the failed start so desk stop can clean it later."""
    if state is None:
        return
    try:
        current = read_session_state(paths.session_file)
    except StateError:
        return
    if current is not None and current.session_id != state.session_id:
        return
    try:
        write_session_state(paths.session_file, replace(state, status="failed"))
    except OSError:
        return


def _write_state(paths: Paths, state: SessionState) -> None:
    try:
        write_session_state(paths.session_file, state)
    except OSError as error:
        raise StartError(f"could not write the state file {paths.session_file}: {error}") from error


def _wait_for_desktop(
    paths: Paths,
    started: StartedProcess,
    runtime: SessionRuntime,
    *,
    timeout_s: float,
) -> tuple[bool, str]:
    """Wait until the X socket accepts connections and XFCE is running.

    termux-x11 must already be a member of the group, so a socket that a dead
    server left behind cannot make the wait succeed.
    """
    deadline = runtime.clock() + timeout_s
    while True:
        socket_ready = runtime.socket_ready(paths.x11_socket)
        x11_ready = runtime.x11_present(started.pgid)
        xfce_ready = runtime.xfce_present(started.pgid)
        if socket_ready and x11_ready and xfce_ready:
            return True, "the desktop is ready"

        exit_code = started.popen.poll()
        if exit_code is not None and not runtime.group_members(started.pgid):
            return False, (
                f"the session script exited with status {exit_code} before the desktop was ready. "
                "If the Termux:X11 app did not open, start the app yourself; "
                "Android can block background app starts"
            )

        now = runtime.clock()
        if now >= deadline:
            return False, (
                f"the desktop was not ready after {timeout_s:.0f} seconds "
                f"(X11 socket accepts connections: {socket_ready}, "
                f"X server process found: {x11_ready}, XFCE process found: {xfce_ready})"
            )
        runtime.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - now)))
