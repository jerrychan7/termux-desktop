"""Shared helpers for the desk unit and integration tests.

No helper in this file starts a real desktop process, signals a real process
group, or runs a real package manager. Commands are answered by stand-ins and
process groups are recorded in memory. The integration tests create real
short-lived process groups explicitly.
"""

from __future__ import annotations

import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from termux_desk.config import Paths, load_paths
from termux_desk.runner import CommandResult
from termux_desk.session import SessionRuntime, StartedProcess
from termux_desk.state import SessionState, write_session_state

FIXED_TIME = datetime(2026, 11, 9, 1, 30, tzinfo=timezone(timedelta(hours=8)))
SESSION_ID = "0123456789abcdef0123456789abcdef"
BOOT_ID = "test-boot-id"
LEADER_STARTTIME = 123456


def make_result(argv, returncode=0, stdout="", stderr=""):
    """Build a CommandResult for a stand-in runner."""
    return CommandResult(
        argv=tuple(str(item) for item in argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def command_name(argv) -> str:
    """Return the program name of a recorded command."""
    return Path(str(argv[0])).name


class RecordingRunner:
    """Answer commands from a handler and record every call."""

    def __init__(self, handler=None):
        self.handler = handler
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, **kwargs):
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if self.handler is None:
            return make_result(command)
        return self.handler(command, **kwargs)


class Workspace:
    """A temporary HOME, PREFIX, and PATH directory for one test."""

    def __init__(self, root: Path):
        self.root = root
        self.home = root / "home"
        self.prefix = root / "usr"
        self.path = root / "path"
        self.home.mkdir(parents=True, exist_ok=True)
        (self.prefix / "bin").mkdir(parents=True, exist_ok=True)
        (self.prefix / "tmp").mkdir(parents=True, exist_ok=True)
        self.path.mkdir(parents=True, exist_ok=True)
        self.environ = {
            "HOME": str(self.home),
            "PREFIX": str(self.prefix),
            "PATH": str(self.path),
        }
        self.paths: Paths = load_paths(self.environ)

    def add_program(self, name: str, directory: Path | None = None) -> Path:
        """Create an executable file that resolve_program can find."""
        target_directory = directory if directory is not None else self.prefix / "bin"
        target = target_directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        target.chmod(0o755)
        return target

    def add_required_programs(self) -> None:
        """Create every program that desk start and desk doctor require."""
        for name in (
            "termux-x11",
            "startxfce4",
            "dbus-launch",
            "am",
            "bash",
            "proot-distro",
            "firefox",
        ):
            self.add_program(name)

    def write_state(self, state: SessionState) -> Path:
        """Write a state file into the temporary state directory."""
        write_session_state(self.paths.session_file, state)
        return self.paths.session_file

    def create_socket(self, path: Path, *, listen: bool = False) -> Path:
        """Create a Unix socket file and close it again."""
        path.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            if listen:
                listener.listen(1)
        finally:
            listener.close()
        return path


def make_state(**overrides) -> SessionState:
    """Build a valid SessionState with predictable values."""
    values = {
        "status": "running",
        "display": ":0",
        "pid": 4242,
        "pgid": 4242,
        "started_at": FIXED_TIME.isoformat(timespec="seconds"),
        "session_id": SESSION_ID,
        "boot_id": BOOT_ID,
        "leader_starttime": LEADER_STARTTIME,
        "session_script": "/home/termux/.local/state/termux-desktop/session.sh",
        "log_path": "/home/termux/.local/state/termux-desktop/session.log",
        "x11_socket": "/data/data/com.termux/files/usr/tmp/.X11-unix/X0",
    }
    values.update(overrides)
    return SessionState(**values)


class FakeGroup:
    """An in-memory process group that records the signals it receives."""

    def __init__(self, members=(), clear_on=(), pgid=4242):
        self.pgid = pgid
        self.members = set(members)
        self.clear_on = set(clear_on)
        self.signals: list[int] = []

    def signal(self, pgid, signum):
        self.signals.append(signum)
        if signum in self.clear_on:
            self.members.clear()

    def members_of(self, pgid):
        if pgid != self.pgid:
            return ()
        return tuple(sorted(self.members))


class FakePopen:
    """A stand-in for subprocess.Popen that only reports an exit status."""

    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


class FakeRuntime:
    """Build a SessionRuntime whose operations are observable and harmless."""

    def __init__(
        self,
        *,
        members=(),
        clear_on=(),
        pgid=4242,
        socket_ready=True,
        x11=True,
        xfce=True,
        socket_in_use=False,
        process_alive=False,
        exit_code=None,
        spawn=None,
        now_value=FIXED_TIME,
        now_callable=None,
        start_app_result=(0, ""),
        boot_id=BOOT_ID,
        verified=True,
        start_time=LEADER_STARTTIME,
        read_boot_id_callable=None,
    ):
        self.group = FakeGroup(members, clear_on, pgid)
        self.started = StartedProcess(pid=pgid, pgid=pgid, popen=FakePopen(exit_code))
        self.spawned: list[dict] = []
        self.start_app_calls: list[str] = []
        self.start_app_result = start_app_result
        self.socket_ready = socket_ready
        self.x11 = x11
        self.xfce = xfce
        self.socket_in_use = socket_in_use
        self.process_alive = process_alive
        self.boot_id = boot_id
        self.verified = verified
        self.start_time = start_time
        self.runtime = SessionRuntime(
            spawn=spawn if spawn is not None else self._spawn,
            socket_ready=lambda path: self.socket_ready,
            socket_in_use=lambda path: self.socket_in_use,
            group_members=self.group.members_of,
            xfce_present=lambda pid: self.xfce,
            x11_present=lambda pid: self.x11,
            session_in_group=lambda *args: self.verified,
            read_boot_id=read_boot_id_callable or (lambda: self.boot_id),
            start_time=lambda pid: self.start_time,
            signal_group=self.group.signal,
            start_app=self._start_app,
            process_alive=lambda pid: self.process_alive,
            clock=time.monotonic,
            sleep=lambda seconds: None,
            now=now_callable or (lambda: now_value),
        )

    def _start_app(self, am_path):
        self.start_app_calls.append(am_path)
        return self.start_app_result

    def _spawn(self, script_path, session_id, *, bash_path, log_path, env, record):
        self.spawned.append(
            {
                "script": script_path,
                "session_id": session_id,
                "bash_path": bash_path,
                "log_path": log_path,
                "env": dict(env),
            }
        )
        record(self.started)
        return self.started
