"""Start, stop, and inspect one XFCE desktop session.

Python decides what to do and records the result. The generated session.sh
performs the actual launches inside one new process group: termux-x11, the
Termux:X11 Android app, and XFCE. desk start waits for the result and then
exits; desk does not stay running as a supervisor.

Every recorded session carries a boot id, the start time of its leader, and a
unique session id. desk verifies those facts before it signals or reports a
process group, so a reused process group id is never mistaken for the session.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence, TextIO

from .config import (
    DESKTOP_LOCALE,
    TERMUX_X11_ACTIVITY,
    DeskError,
    Paths,
    resolve_program,
)
from .state import (
    SessionState,
    read_session_state,
    remove_session_state,
    state_lock,
    write_session_state,
)

SOCKET_WAIT_SECONDS = 20
DESKTOP_READY_TIMEOUT_SECONDS = 60.0
TERMINATE_TIMEOUT_SECONDS = 10.0
KILL_TIMEOUT_SECONDS = 5.0
LOCK_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.2

REQUIRED_PROGRAMS = ("termux-x11", "startxfce4", "dbus-launch", "am", "bash")
VNC_REQUIRED_PROGRAMS = ("Xvnc", "startxfce4", "dbus-launch", "bash")
BACKEND_TERMUX_X11 = "termux-x11"
BACKEND_VNC = "vnc"
BACKENDS = (BACKEND_TERMUX_X11, BACKEND_VNC)
SESSION_ID_ENVIRONMENT = "DESK_SESSION_ID"

# Android libc provides only the C and C.UTF-8 locales. The shared constant
# lives in config because container launches use the same locale.
SESSION_LOCALE = DESKTOP_LOCALE

ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_RESET = "\033[0m"

TERMUX_X11_PROCESS_MARKER = "termux-x11"
# Any of these programs counts as the X server of a session: termux-x11 for
# the Android-backed desktop, Xvnc for a VNC session.
X_SERVER_PROCESS_MARKERS = (TERMUX_X11_PROCESS_MARKER, "Xvnc", "Xtigervnc")
# Any of these programs counts as an XFCE process. xfce4-session is the
# session manager that starts the others.
XFCE_PROCESS_MARKERS = (
    "xfce4-session",
    "xfwm4",
    "xfce4-panel",
    "xfdesktop",
    "xfsettingsd",
)

PROC_ROOT = Path("/proc")
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"@[A-Z][A-Z0-9_]*@")

VERIFICATION_RUNNING = "running"
VERIFICATION_STALE = "stale"
VERIFICATION_UNVERIFIABLE = "unverifiable"


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


@dataclass(frozen=True)
class ProcessStat:
    """The parts of /proc/<pid>/stat that desk uses."""

    state: str
    pgrp: int
    starttime: int


@dataclass(frozen=True)
class StartedProcess:
    """The session script process group that desk just created."""

    pid: int
    pgid: int
    popen: "Pollable"


class Pollable(Protocol):
    """The part of subprocess.Popen that desk reads after a start."""

    def poll(self) -> int | None:
        """Return the exit status, or None while the process runs."""


@dataclass(frozen=True)
class StatusReport:
    """What desk status found and what it printed."""

    running: bool
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Verification:
    """Whether a recorded session still belongs to a live process group."""

    state: str
    detail: str


# ---------------------------------------------------------------------------
# Process table helpers
# ---------------------------------------------------------------------------


def parse_process_stat(stat_text: str) -> ProcessStat | None:
    """Read state, process group, and start time from /proc/<pid>/stat text.

    The second field is the executable name in parentheses and can contain
    spaces and closing parentheses, so parsing starts after the last ')'.
    """
    closing = stat_text.rfind(")")
    if closing < 0:
        return None
    fields = stat_text[closing + 1 :].split()
    if len(fields) < 20:
        return None
    try:
        return ProcessStat(
            state=fields[0],
            pgrp=int(fields[2]),
            starttime=int(fields[19]),
        )
    except ValueError:
        return None


def read_process_stat(pid: int, proc_root: Path = PROC_ROOT) -> ProcessStat | None:
    """Read one process stat entry, or None when the process is gone.

    Only a missing process is skipped. Any other read error is a scan error:
    treating it as "no process" could make desk report a stale session and
    skip or misdirect the cleanup. A permission error can also mean that the
    entry belongs to another app; process_group_members checks the owner of
    the entry and skips it in that case.
    """
    path = proc_root / str(pid) / "stat"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, ProcessLookupError):
        # The process disappeared between the directory scan and this read.
        return None
    except OSError as error:
        raise ProcessScanError(f"could not read {path}: {error}") from error
    entry = parse_process_stat(text)
    if entry is None:
        raise ProcessScanError(f"the content of {path} has an unexpected format")
    return entry


def process_group_of(pid: int, proc_root: Path = PROC_ROOT) -> int | None:
    """Return the process group of one process, or None when it is gone."""
    entry = read_process_stat(pid, proc_root)
    return None if entry is None else entry.pgrp


def parse_process_group(stat_text: str) -> int | None:
    """Return the process group from /proc/<pid>/stat text."""
    entry = parse_process_stat(stat_text)
    return None if entry is None else entry.pgrp


def process_start_time(pid: int, proc_root: Path = PROC_ROOT) -> int | None:
    """Return the start time of one process, or None when it is gone."""
    entry = read_process_stat(pid, proc_root)
    return None if entry is None else entry.starttime


def process_group_members(pgid: int, proc_root: Path = PROC_ROOT) -> tuple[int, ...]:
    """Return the live PIDs that belong to one process group.

    Zombie and dead processes do not count: they cannot run anything and the
    process group is empty as soon as no live member remains. An entry that
    belongs to another app is skipped: it cannot be part of a Termux process
    group. Any other failure to read /proc raises ProcessScanError instead of
    returning an empty group.
    """
    if pgid <= 0:
        return ()
    try:
        entries = os.listdir(proc_root)
    except OSError as error:
        raise ProcessScanError(f"could not read the process directory {proc_root}: {error}") from error

    members: list[int] = []
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            process = read_process_stat(pid, proc_root)
        except ProcessScanError:
            if _process_owned_by_another_user(pid, proc_root):
                continue
            raise
        if process is None:
            continue
        if process.state in ("Z", "X"):
            continue
        if process.pgrp == pgid:
            members.append(pid)
    return tuple(sorted(members))


def _process_owned_by_another_user(pid: int, proc_root: Path) -> bool:
    """Return True when the process entry is not a process of this user.

    Android hides the process entries of other apps, so os.stat fails with
    EACCES for them. Termux runs desk, the X server, and XFCE with the same
    UID, so an entry that Termux cannot stat is an entry of another app and
    cannot be part of a Termux process group; the scan can skip it. A stat
    that succeeds and reports another UID is the same case. Any other stat
    error is answered with False, so the scan fails closed.
    """
    try:
        owner = os.stat(proc_root / str(pid)).st_uid
    except PermissionError:
        # Android hides another app's /proc entry from Termux, and every
        # Termux process shares the Termux UID, so this is not our process.
        return True
    except OSError:
        return False
    return owner != os.getuid()


def pid_alive(pid: int, proc_root: Path = PROC_ROOT) -> bool:
    """Return True when /proc still has a process of this user for the PID.

    A process entry that cannot be stat'ed belongs to another app, so it is
    not our process and the answer is False. A stat that fails for any other
    reason raises ProcessScanError, because desk cannot then tell whether the
    process is ours.
    """
    if pid <= 0:
        return False
    path = proc_root / str(pid)
    try:
        os.stat(path)
    except FileNotFoundError:
        return False
    except PermissionError:
        # Android hides another app's /proc entry from Termux, and every
        # Termux process shares the Termux UID, so this is not our process.
        return False
    except OSError as error:
        raise ProcessScanError(f"could not read {path}: {error}") from error
    return True


def read_process_arguments(pid: int, proc_root: Path = PROC_ROOT) -> tuple[str, ...]:
    """Return the argument list of one process, or an empty tuple."""
    try:
        data = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ()
    return tuple(
        part.decode("utf-8", errors="replace") for part in data.split(b"\0") if part
    )


def is_x_server_process(pid: int, proc_root: Path = PROC_ROOT) -> bool:
    """Return True when the process with this PID runs the Termux X server."""
    return any(
        TERMUX_X11_PROCESS_MARKER in argument
        for argument in read_process_arguments(pid, proc_root)
    )


def read_process_environment(pid: int, proc_root: Path = PROC_ROOT) -> dict[str, str]:
    """Return the environment of one process, or an empty mapping."""
    try:
        data = (proc_root / str(pid) / "environ").read_bytes()
    except OSError:
        return {}
    environment: dict[str, str] = {}
    for chunk in data.split(b"\0"):
        if not chunk:
            continue
        text = chunk.decode("utf-8", errors="replace")
        name, separator, value = text.partition("=")
        if separator:
            environment[name] = value
    return environment


def session_in_group(
    pgid: int,
    session_id: str,
    leader_pid: int,
    leader_starttime: int,
    proc_root: Path = PROC_ROOT,
) -> bool:
    """Return True when the process group provably belongs to this session.

    Evidence is the unique session id in a member argument list, the same id
    in a member environment, or the recorded leader still running with the
    recorded start time. Without evidence desk refuses to signal the group.
    """
    for pid in process_group_members(pgid, proc_root):
        if any(session_id in argument for argument in read_process_arguments(pid, proc_root)):
            return True
        if read_process_environment(pid, proc_root).get(SESSION_ID_ENVIRONMENT) == session_id:
            return True

    leader = read_process_stat(leader_pid, proc_root)
    if (
        leader is not None
        and leader.pgrp == pgid
        and leader.starttime == leader_starttime
        and leader.state not in ("Z", "X")
    ):
        return True
    return False


def has_xfce_process(pgid: int, proc_root: Path = PROC_ROOT) -> bool:
    """Return True when a process in the group is an XFCE program."""
    for pid in process_group_members(pgid, proc_root):
        arguments = read_process_arguments(pid, proc_root)
        if any(marker in argument for argument in arguments for marker in XFCE_PROCESS_MARKERS):
            return True
    return False


def has_termux_x11_process(pgid: int, proc_root: Path = PROC_ROOT) -> bool:
    """Return True when a process in the group is termux-x11."""
    for pid in process_group_members(pgid, proc_root):
        arguments = read_process_arguments(pid, proc_root)
        if any(TERMUX_X11_PROCESS_MARKER in argument for argument in arguments):
            return True
    return False


def has_x_server_process(pgid: int, proc_root: Path = PROC_ROOT) -> bool:
    """Return True when a process in the group is an X server (termux-x11 or Xvnc)."""
    for pid in process_group_members(pgid, proc_root):
        arguments = read_process_arguments(pid, proc_root)
        if any(marker in argument for argument in arguments for marker in X_SERVER_PROCESS_MARKERS):
            return True
    return False


def read_boot_id(path: Path = BOOT_ID_PATH) -> str:
    """Read the kernel boot id, which changes on every device boot."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SessionError(f"could not read the boot id from {path}: {error}") from error
    if not text:
        raise SessionError(f"the boot id file {path} is empty")
    return text


# ---------------------------------------------------------------------------
# X server helpers
# ---------------------------------------------------------------------------


def display_socket_ready(socket_path: Path) -> bool:
    """Return True when the X11 Unix socket exists and is a socket."""
    try:
        mode = socket_path.stat().st_mode
    except OSError:
        return False
    return stat.S_ISSOCK(mode)


def unix_socket_accepts_connection(socket_path: Path) -> bool:
    """Return True when a process accepts connections at the socket."""
    if not display_socket_ready(socket_path):
        return False
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(1.0)
        connection.connect(str(socket_path))
        return True
    except OSError:
        return False
    finally:
        connection.close()


def x11_socket_ready(socket_path: Path) -> bool:
    """Return True when the X socket is present and accepts connections."""
    return display_socket_ready(socket_path) and unix_socket_accepts_connection(socket_path)


def read_lock_pid(lock_path: Path) -> int | None:
    """Return the PID written in an X server lock file, or None."""
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def current_time() -> datetime:
    """Return the current local time with its UTC offset."""
    return datetime.now().astimezone()


# ---------------------------------------------------------------------------
# Runtime hooks
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Session script and environment
# ---------------------------------------------------------------------------


def session_environment(
    paths: Paths,
    session_id: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the environment that the session script and its children get."""
    if paths.prefix is None:
        raise SessionError("PREFIX is not set. desk must run inside Termux.")
    environment = dict(os.environ if environ is None else environ)
    environment["PREFIX"] = str(paths.prefix)
    environment["HOME"] = str(paths.home)
    environment["DISPLAY"] = paths.display
    environment["XDG_RUNTIME_DIR"] = str(paths.runtime_dir)
    environment["XDG_CONFIG_DIRS"] = str(paths.system_config_dir)
    environment["XDG_DATA_DIRS"] = str(paths.prefix / "share")
    environment["LANG"] = SESSION_LOCALE
    environment["LC_ALL"] = SESSION_LOCALE
    environment[SESSION_ID_ENVIRONMENT] = session_id
    return environment


def read_template(paths: Paths) -> str:
    """Read the session script template from the repository."""
    try:
        return paths.template_file.read_text(encoding="utf-8")
    except OSError as error:
        raise SessionError(
            f"could not read the session script template {paths.template_file}: {error}"
        ) from error


def render_session_script(
    paths: Paths,
    template_text: str | None = None,
    *,
    backend: str = "termux-x11",
    vnc_arguments: Sequence[str] = (),
) -> str:
    """Fill the template with the paths of this Termux installation.

    ``backend`` selects the X server that the script starts: termux-x11 or
    Xvnc. ``vnc_arguments`` are the Xvnc command line options; they are only
    used when ``backend`` is ``vnc``.

    The replacement runs in one pass, so an @NAME@ sequence that appears in a
    substituted value is copied literally and is not replaced a second time.
    """
    if paths.prefix is None:
        raise SessionError("PREFIX is not set. desk start must run inside Termux.")
    template = read_template(paths) if template_text is None else template_text
    replacements = {
        "@BASH@": str(paths.prefix / "bin" / "bash"),
        "@PREFIX@": shlex.quote(str(paths.prefix)),
        "@HOME@": shlex.quote(str(paths.home)),
        "@DISPLAY@": shlex.quote(paths.display),
        "@XDG_RUNTIME_DIR@": shlex.quote(str(paths.runtime_dir)),
        "@XDG_CONFIG_DIRS@": shlex.quote(str(paths.system_config_dir)),
        "@XDG_DATA_DIRS@": shlex.quote(str(paths.prefix / "share")),
        "@X11_SOCKET_DIR@": shlex.quote(str(paths.x11_socket_dir)),
        "@X11_SOCKET@": shlex.quote(str(paths.x11_socket)),
        "@SOCKET_WAIT_SECONDS@": str(SOCKET_WAIT_SECONDS),
        "@TERMUX_X11_ACTIVITY@": TERMUX_X11_ACTIVITY,
        "@X_SERVER_MODE@": shlex.quote(backend),
        "@XVNC_ARGS@": " ".join(shlex.quote(argument) for argument in vnc_arguments),
    }

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        if placeholder not in replacements:
            raise SessionError(
                f"the session script template contains a placeholder that desk does not fill: {placeholder}"
            )
        return replacements[placeholder]

    template = TEMPLATE_PLACEHOLDER_PATTERN.sub(replace, template)
    if not template.endswith("\n"):
        template += "\n"
    return template


def write_session_script(
    paths: Paths,
    template_text: str | None = None,
    *,
    backend: str = BACKEND_TERMUX_X11,
    vnc_arguments: Sequence[str] = (),
) -> Path:
    """Write the generated session script into the state directory."""
    text = render_session_script(
        paths, template_text, backend=backend, vnc_arguments=vnc_arguments
    )
    temporary = paths.session_script.with_name(
        f"{paths.session_script.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, 0o755)
        os.replace(temporary, paths.session_script)
    except OSError as error:
        raise SessionError(
            f"could not write the session script {paths.session_script}: {error}"
        ) from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return paths.session_script


def missing_programs(
    paths: Paths, environ: Mapping[str, str], *, backend: str = BACKEND_TERMUX_X11
) -> tuple[str, ...]:
    """Return the programs that the session needs and cannot find."""
    required = VNC_REQUIRED_PROGRAMS if backend == BACKEND_VNC else REQUIRED_PROGRAMS
    search_path = environ.get("PATH")
    return tuple(
        name
        for name in required
        if resolve_program(name, paths.prefix, search_path) is None
    )


def display_in_use(paths: Paths, runtime: SessionRuntime) -> str | None:
    """Return a sentence about who already owns :0, or None when it is free.

    The socket is the evidence that a server is serving :0. The lock file only
    records a PID, and Android can reuse that PID for another app, so a live
    PID counts only while it still runs the Termux X server.
    """
    if paths.prefix is None:
        return None
    if runtime.socket_in_use(paths.x11_socket):
        return f"a process accepts connections at {paths.x11_socket}"
    lock_pid = read_lock_pid(paths.x11_lock)
    if (
        lock_pid is not None
        and runtime.process_alive(lock_pid)
        and runtime.x_server_process(lock_pid)
    ):
        return f"the X server lock file {paths.x11_lock} belongs to live process {lock_pid}"
    return None


def clear_stale_display_files(
    paths: Paths,
    runtime: SessionRuntime,
    *,
    out: TextIO,
) -> None:
    """Remove X server files that no live server owns.

    The caller first proved that :0 is not in use. A socket file that no
    process accepts connections on, and a lock file whose PID is gone or is
    another app's process, are left over from an earlier crash. Removing them
    keeps the readiness wait from accepting a stale socket.
    """
    if paths.prefix is None:
        return

    socket_path = paths.x11_socket
    if socket_path.exists() or socket_path.is_symlink():
        if runtime.socket_in_use(socket_path):
            raise StartError(
                f"the X11 socket {socket_path} accepts connections; desk will not remove it"
            )
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise StartError(f"could not remove the stale X11 socket {socket_path}: {error}") from error
        print(f"Removed the stale X11 socket {socket_path}.", file=out)

    lock_path = paths.x11_lock
    if lock_path.exists() or lock_path.is_symlink():
        lock_pid = read_lock_pid(lock_path)
        if (
            lock_pid is not None
            and runtime.process_alive(lock_pid)
            and runtime.x_server_process(lock_pid)
        ):
            raise StartError(
                f"the X server lock file {lock_path} belongs to live process {lock_pid}"
            )
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise StartError(f"could not remove the stale X lock file {lock_path}: {error}") from error
        print(f"Removed the stale X lock file {lock_path}.", file=out)


def _clear_stale_display_files_after_stop(
    paths: Paths,
    runtime: SessionRuntime,
    *,
    out: TextIO,
) -> None:
    """Remove stale X files only after proving no X server still owns :0."""
    if paths.prefix is None:
        return
    in_use = display_in_use(paths, runtime)
    if in_use is not None:
        print(f"Left the X11 files in place: {in_use}.", file=out)
        return
    try:
        clear_stale_display_files(paths, runtime, out=out)
    except StartError as error:
        raise StopError(str(error)) from error


def verify_session(state: SessionState, runtime: SessionRuntime) -> Verification:
    """Determine whether the recorded session still owns a live process group."""
    current_boot_id = runtime.read_boot_id()
    if current_boot_id != state.boot_id:
        return Verification(
            VERIFICATION_STALE,
            f"the state file was written during an earlier boot ({state.boot_id})",
        )

    members = runtime.group_members(state.pgid)
    if not members:
        return Verification(
            VERIFICATION_STALE,
            f"process group {state.pgid} has no live processes",
        )

    if runtime.session_in_group(
        state.pgid,
        state.session_id,
        state.pid,
        state.leader_starttime,
    ):
        return Verification(
            VERIFICATION_RUNNING,
            f"process group {state.pgid} belongs to session {state.session_id}",
        )

    return Verification(
        VERIFICATION_UNVERIFIABLE,
        f"process group {state.pgid} has processes, but none of them carry "
        f"session id {state.session_id}",
    )


# ---------------------------------------------------------------------------
# Start, stop, and status
# ---------------------------------------------------------------------------


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


SESSION_STATE_PATTERN = re.compile(r"^session(?:-(?P<number>\d+))?\.json$")


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


# ---------------------------------------------------------------------------
# Internal steps
# ---------------------------------------------------------------------------


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
