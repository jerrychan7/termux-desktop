"""Start and control a VNC desktop session.

TigerVNC's Xvnc is the X server of a VNC session: it owns its own display
(:1 by default), so the VNC desktop runs next to the termux-x11 desktop on
:0. The server listens on localhost only; the user connects through an SSH
tunnel with any VNC viewer. Without a password file the server uses
SecurityTypes None; with one it uses VncAuth.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from . import setup
from .config import DeskError, Paths, resolve_program
from .runner import CommandRunner
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
VNC_PACKAGES = ("x11-repo", "tigervnc")
VNC_PROGRAMS = ("Xvnc", "vncpasswd", "x0vncserver")
VNC_VIEWER_NOTE = (
    "No VNC viewer is installed. Connect from another device through an SSH "
    "tunnel to the loopback port of the display, for example "
    "ssh -L 5901:127.0.0.1:5901 <this-device>, then open localhost:5901 "
    "in a VNC viewer."
)

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
        # TigerVNC counts every aborted handshake as a failed authentication
        # attempt and blacklists the host after five of them. The session is
        # loopback only and uses SecurityTypes None, so the blacklist adds no
        # protection but turns a failed probe or viewer retry into a state
        # that refuses every later connection for a while.
        "-UseBlacklist",
        "0",
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


def run_vnc_setup(
    paths: Paths,
    runner: CommandRunner | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Install tigervnc and verify the programs that desk vnc needs."""
    output = sys.stdout if out is None else out
    commands_runner = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if paths.prefix is None or not paths.prefix.is_dir():
        raise VncError("PREFIX is not set to an existing directory. desk must run inside Termux.")

    package_manager = setup.resolve_setup_program("pkg", paths, environment)
    dpkg_query = setup.resolve_setup_program("dpkg-query", paths, environment)

    print(f"VNC packages: {', '.join(VNC_PACKAGES)}", file=output)
    missing = setup.find_missing_packages(
        commands_runner, VNC_PACKAGES, dpkg_query=dpkg_query
    )
    if not missing:
        print("All VNC packages are already installed.", file=output)
    else:
        print(f"Missing packages: {', '.join(missing)}", file=output)
        commands = setup.build_setup_commands(
            missing, package_manager=package_manager, fixed=VNC_PACKAGES
        )
        for command in commands:
            print(f"Running: {' '.join(command)}", file=output, flush=True)
            try:
                result = commands_runner.run(command)
            except FileNotFoundError as error:
                raise VncError(
                    f"the program '{command[0]}' was not found. desk vnc setup must run inside Termux."
                ) from error
            except OSError as error:
                raise VncError(f"could not run {' '.join(command)}: {error}") from error
            if not result.succeeded:
                raise VncError(
                    "the command failed with status "
                    f"{result.returncode}: {' '.join(command)}. Setup stopped at this step."
                )
        still_missing = setup.find_missing_packages(
            commands_runner, VNC_PACKAGES, dpkg_query=dpkg_query
        )
        if still_missing:
            raise VncError(
                "packages are still missing after setup: "
                f"{', '.join(still_missing)}. Check the package manager output above."
            )
        print("All VNC packages are installed.", file=output)

    missing_programs = tuple(
        name
        for name in VNC_PROGRAMS
        if resolve_program(name, paths.prefix, environment.get("PATH")) is None
    )
    if missing_programs:
        raise VncError(
            "VNC setup finished, but these programs were not found: "
            + ", ".join(missing_programs)
            + ". Check the tigervnc package."
        )
    print(
        "Found the Xvnc server, vncpasswd, and x0vncserver.",
        file=output,
    )
    print(VNC_VIEWER_NOTE, file=output)


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
