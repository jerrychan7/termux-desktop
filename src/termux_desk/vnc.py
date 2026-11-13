"""Start and control a VNC desktop session.

TigerVNC's Xvnc is the X server of a VNC session: it owns its own display
(:1 by default), so the VNC desktop runs next to the termux-x11 desktop on
:0. The server listens on localhost only; the user connects through an SSH
tunnel with any VNC viewer. Without a password file the server uses
SecurityTypes None; with one it uses VncAuth.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .config import DeskError, Paths
from .session import (
    BACKEND_VNC,
    SessionRuntime,
    SessionState,
    StatusReport,
    session_status,
    start_session,
    stop_session,
)

VNC_DISPLAY = ":1"
VNC_GEOMETRY = "1280x720"
VNC_DEPTH = 24
VNC_BASE_PORT = 5900

GEOMETRY_PATTERN = re.compile(r"^(?P<width>[1-9][0-9]*)x(?P<height>[1-9][0-9]*)$")


class VncError(DeskError):
    """A VNC request contains an unusable value."""


def rfb_port(display: str) -> int:
    """Return the TCP port that clients connect to for a display."""
    if not re.fullmatch(r":[0-9]+", display):
        raise VncError(f"invalid display name {display!r}; expected :<number>")
    return VNC_BASE_PORT + int(display[1:])


def check_geometry(geometry: str) -> str:
    """Return the geometry unchanged, or raise VncError when it is unusable."""
    if not GEOMETRY_PATTERN.fullmatch(geometry):
        raise VncError(
            f"invalid geometry {geometry!r}; expected WIDTHxHEIGHT, for example 1280x720"
        )
    return geometry


def build_xvnc_arguments(
    *,
    geometry: str = VNC_GEOMETRY,
    depth: int = VNC_DEPTH,
    password_file: Path | None = None,
) -> tuple[str, ...]:
    """Return the Xvnc command line options for one session."""
    arguments = [
        "-geometry",
        check_geometry(geometry),
        "-depth",
        str(depth),
        "-localhost",
    ]
    if password_file is None:
        arguments.extend(["-SecurityTypes", "None"])
    else:
        arguments.extend(["-PasswordFile", str(password_file), "-SecurityTypes", "VncAuth"])
    return tuple(arguments)


def run_vnc_start(
    paths: Paths,
    *,
    display: str = VNC_DISPLAY,
    geometry: str = VNC_GEOMETRY,
    password_file: Path | None = None,
    runtime: SessionRuntime | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    color: bool = False,
    ready_timeout: float | None = None,
) -> SessionState:
    """Start an Xvnc session on display and wait until XFCE is ready."""
    arguments = build_xvnc_arguments(
        geometry=geometry, password_file=password_file
    )
    if password_file is not None and not password_file.is_file():
        raise VncError(
            f"the password file {password_file} does not exist; "
            "create it with 'vncpasswd -f'"
        )
    target = paths.with_display(display)
    options: dict[str, object] = {}
    if ready_timeout is not None:
        options["ready_timeout"] = ready_timeout
    return start_session(
        target,
        backend=BACKEND_VNC,
        vnc_arguments=arguments,
        runtime=runtime,
        environ=environ,
        out=out,
        color=color,
        **options,
    )


def run_vnc_stop(
    paths: Paths,
    *,
    display: str = VNC_DISPLAY,
    runtime: SessionRuntime | None = None,
    out: TextIO | None = None,
    **timeouts: object,
) -> None:
    """Stop the VNC session on display."""
    stop_session(paths.with_display(display), runtime=runtime, out=out, **timeouts)


def run_vnc_status(
    paths: Paths,
    *,
    display: str = VNC_DISPLAY,
    runtime: SessionRuntime | None = None,
) -> StatusReport:
    """Report the VNC session on display."""
    return session_status(paths.with_display(display), runtime=runtime)
