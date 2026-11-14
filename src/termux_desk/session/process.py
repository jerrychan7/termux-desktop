"""Read the /proc process table and decide which processes belong to a session."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .constants import (
    BOOT_ID_PATH,
    PROC_ROOT,
    SESSION_ID_ENVIRONMENT,
    TERMUX_X11_PROCESS_MARKER,
    X_SERVER_PROCESS_MARKERS,
    XFCE_PROCESS_MARKERS,
)
from .errors import ProcessScanError, SessionError

if TYPE_CHECKING:
    from .runtime import Pollable


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
