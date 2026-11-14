"""Inspect and clean the X display socket and lock file of one display."""

from __future__ import annotations

import socket
import stat
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from ..config import Paths
from .errors import StartError, StopError

if TYPE_CHECKING:
    from .runtime import SessionRuntime


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
