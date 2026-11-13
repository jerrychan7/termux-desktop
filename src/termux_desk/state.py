"""Read and write the session state file, and read and append the logs.

The state file is JSON and records only facts that can be checked again:
the PID and PGID of the session, the boot id, the start time of the session
leader, a unique session id, the display, the start time, and the paths of
the generated script and the log.
"""

from __future__ import annotations

import collections
import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .config import DeskError, Paths

STATE_VERSION = 2
LOCK_FILE_NAME = "desk.lock"


class StateError(DeskError):
    """The session state file is unreadable or does not contain valid state."""


class LockError(DeskError):
    """Another desk command is using the session state."""


@dataclass(frozen=True)
class SessionState:
    """The recorded facts about one desktop session."""

    status: str
    display: str
    pid: int
    pgid: int
    started_at: str
    session_id: str
    boot_id: str
    leader_starttime: int
    session_script: str = ""
    log_path: str = ""
    x11_socket: str = ""
    backend: str = "termux-x11"

    def to_record(self) -> dict[str, Any]:
        """Return the JSON-serialisable form of this state."""
        return {"version": STATE_VERSION, **asdict(self)}

    @classmethod
    def from_record(cls, record: Any, source: Path) -> "SessionState":
        """Validate one decoded JSON value and build a state from it."""
        if not isinstance(record, dict):
            raise StateError(f"the state file {source} does not contain a JSON object")

        version = record.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise StateError(f"the state file {source} has no usable 'version' value")
        if version != STATE_VERSION:
            raise StateError(
                f"the state file {source} uses version {version}, but desk expects "
                f"version {STATE_VERSION}. desk has no automatic migration: remove "
                "the state file if no desk session is running, then run desk start again."
            )

        status = record.get("status")
        if not isinstance(status, str) or not status:
            raise StateError(f"the state file {source} has no usable 'status' value")

        display = record.get("display")
        if not isinstance(display, str) or not display:
            raise StateError(f"the state file {source} has no usable 'display' value")

        numbers: dict[str, int] = {}
        for name in ("pid", "pgid"):
            value = record.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 1:
                raise StateError(
                    f"the state file {source} has no usable '{name}' value; "
                    "a session process id and process group id must be greater than 1"
                )
            numbers[name] = value

        started_at = record.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            raise StateError(f"the state file {source} has no usable 'started_at' value")

        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise StateError(f"the state file {source} has no usable 'session_id' value")

        boot_id = record.get("boot_id")
        if not isinstance(boot_id, str) or not boot_id:
            raise StateError(f"the state file {source} has no usable 'boot_id' value")

        leader_starttime = record.get("leader_starttime")
        if (
            isinstance(leader_starttime, bool)
            or not isinstance(leader_starttime, int)
            or leader_starttime <= 0
        ):
            raise StateError(
                f"the state file {source} has no usable 'leader_starttime' value"
            )

        def text(name: str) -> str:
            value = record.get(name, "")
            return value if isinstance(value, str) else ""

        backend = record.get("backend", "termux-x11")
        if backend not in ("termux-x11", "vnc"):
            backend = "termux-x11"

        return cls(
            status=status,
            display=display,
            pid=numbers["pid"],
            pgid=numbers["pgid"],
            started_at=started_at,
            session_id=session_id,
            boot_id=boot_id,
            leader_starttime=leader_starttime,
            session_script=text("session_script"),
            log_path=text("log_path"),
            x11_socket=text("x11_socket"),
            backend=backend,
        )


def read_session_state(path: Path) -> SessionState | None:
    """Return the recorded state, or None when the state file does not exist.

    A state file that exists but cannot be decoded raises StateError. The
    caller then reports the problem instead of guessing a process group.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateError(f"could not read the state file {path}: {error}") from error

    try:
        record = json.loads(text)
    except json.JSONDecodeError as error:
        raise StateError(f"the state file {path} is not valid JSON: {error}") from error

    return SessionState.from_record(record, path)


def write_session_state(path: Path, state: SessionState) -> None:
    """Write the state file atomically so a crash cannot leave half a file.

    The temporary file name contains the PID and a random value, so two desk
    commands can never overwrite each other's temporary file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(state.to_record(), indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def remove_session_state(path: Path, *, session_id: str | None = None) -> bool:
    """Remove the state file and report whether a file was removed.

    When session_id is given, the file is removed only while it still records
    that session. A command that passes its own session id therefore cannot
    delete the state that another session wrote in the meantime.
    """
    if session_id is not None:
        try:
            current = read_session_state(path)
        except StateError:
            return False
        if current is None or current.session_id != session_id:
            return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


@contextmanager
def state_lock(
    paths: Paths,
    *,
    timeout: float = 15.0,
    poll_interval: float = 0.1,
) -> Iterator[None]:
    """Hold the desk state lock for one command.

    desk start and desk stop run under this lock so two commands cannot decide
    that no session exists and then both start one. The lock file stays in the
    state directory; it is never deleted.
    """
    lock_path = paths.state_dir / LOCK_FILE_NAME
    try:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LockError(
            f"could not create the state directory {paths.state_dir}: {error}"
        ) from error
    try:
        handle = lock_path.open("a+")
    except OSError as error:
        raise LockError(f"could not open the desk lock file {lock_path}: {error}") from error

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockError(
                        "another desk command is using the session state in "
                        f"{paths.state_dir}; try again after it finishes."
                    ) from None
                time.sleep(poll_interval)
        try:
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        handle.close()


def append_log(path: Path, message: str, *, timestamp: datetime | None = None) -> None:
    """Append one timestamped line to a log file.

    A log write must never stop a command, so an unwritable log file is
    ignored. The command itself has already been reported on the terminal.
    """
    stamp = (timestamp or datetime.now().astimezone()).isoformat(timespec="seconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log:
            log.write(f"{stamp} {message}\n")
    except OSError:
        return


def read_log_tail(path: Path, line_count: int) -> list[str] | None:
    """Return the last line_count lines of a log, or None when it is missing."""
    if line_count < 0:
        raise ValueError("line_count must not be negative")
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateError(f"could not read the log file {path}: {error}") from error
    with handle:
        return list(collections.deque(handle, maxlen=line_count))
