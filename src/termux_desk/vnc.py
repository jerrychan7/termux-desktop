"""Start and control a VNC desktop session.

TigerVNC's Xvnc is the X server of a VNC session: it owns its own display
(:1 by default), so the VNC desktop runs next to the termux-x11 desktop on
:0. The server listens on localhost only; the user connects through an SSH
tunnel with any VNC viewer. Without a password file the server uses
SecurityTypes None; with one it uses VncAuth.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from . import setup
from .config import DeskError, Paths, resolve_program
from .runner import CommandRunner
from .session import (
    BACKEND_VNC,
    SessionRuntime,
    SessionState,
    StatusReport,
    display_socket_ready,
    pid_alive,
    session_status,
    start_session,
    stop_session,
)

VNC_DISPLAY = ":1"
MIRROR_DISPLAY = ":0"
MIRROR_STATE_VERSION = 1
VNC_GEOMETRY = "1280x720"
VNC_DEPTH = 24
VNC_BASE_PORT = 5900
VNC_PACKAGES = ("x11-repo", "tigervnc", "darkhttpd")
VNC_PROGRAMS = ("Xvnc", "vncpasswd", "x0vncserver", "darkhttpd")
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


# ---------------------------------------------------------------------------
# Mirror the running termux-x11 desktop with x0vncserver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MirrorState:
    """The recorded state of a running x0vncserver that mirrors display :0."""

    display: str
    pid: int
    port: int
    started_at: float = 0.0
    view_only: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "version": MIRROR_STATE_VERSION,
            "display": self.display,
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "view_only": self.view_only,
        }

    @classmethod
    def from_json(cls, record: Mapping[str, object]) -> "MirrorState":
        if record.get("version") != MIRROR_STATE_VERSION:
            raise VncError("the VNC mirror state has an unknown version")
        try:
            return cls(
                display=str(record["display"]),
                pid=int(record["pid"]),
                port=int(record["port"]),
                started_at=float(record.get("started_at", 0.0)),
                view_only=bool(record.get("view_only", False)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise VncError(f"the VNC mirror state is incomplete: {error}") from error


MIRROR_STATE_PATTERN = re.compile(r"mirror-(?P<number>[0-9]+)\.json")


def mirror_displays(paths: Paths) -> tuple[str, ...]:
    """Return the displays that have a recorded mirror, from :0 to :N."""
    try:
        names = os.listdir(paths.state_dir)
    except OSError:
        return ()
    numbers: set[int] = set()
    for name in names:
        match = MIRROR_STATE_PATTERN.fullmatch(name)
        if match is None:
            continue
        numbers.add(int(match.group("number")))
    return tuple(f":{number}" for number in sorted(numbers))


def mirror_state_path(paths: Paths, display: str) -> Path:
    return paths.state_dir / f"mirror-{display[1:]}.json"


def mirror_log_path(paths: Paths, display: str) -> Path:
    return paths.state_dir / f"mirror-{display[1:]}.log"


def read_mirror_state(paths: Paths, display: str) -> MirrorState | None:
    path = mirror_state_path(paths, display)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise VncError(f"could not read {path}: {error}") from error
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as error:
        raise VncError(f"the VNC mirror state is not valid JSON: {error}") from error
    if not isinstance(record, dict):
        raise VncError("the VNC mirror state is not an object")
    return MirrorState.from_json(record)


def write_mirror_state(paths: Paths, state: MirrorState) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    mirror_state_path(paths, state.display).write_text(
        json.dumps(state.to_json(), indent=2) + "\n"
    )


def remove_mirror_state(paths: Paths, display: str) -> None:
    try:
        mirror_state_path(paths, display).unlink()
    except FileNotFoundError:
        pass


def build_x0vncserver_arguments(
    *, display: str = MIRROR_DISPLAY, view_only: bool = False
) -> tuple[str, ...]:
    """Return the x0vncserver options that mirror a running X server."""
    if display != MIRROR_DISPLAY:
        raise VncError(
            f"a VNC mirror needs {MIRROR_DISPLAY}; use a separate display for a new session"
        )
    arguments = [
        "-display",
        display,
        "-rfbport",
        str(rfb_port(display)),
        "-localhost",
        "-AlwaysShared",
        # The same PID-reuse and retry lessons as the Xvnc arguments: loopback
        # only, no password, so the blacklist can only lock us out.
        "-UseBlacklist",
        "0",
        "-SecurityTypes",
        "None",
        "-desktop",
        f"host {display}",
    ]
    if view_only:
        # View-only still accepts the connection and clipboard updates from the
        # client unless all three inputs are disabled; keep the desktop untouched.
        arguments.extend(["-AcceptKeyEvents", "0", "-AcceptPointerEvents", "0", "-AcceptCutText", "0"])
    return tuple(arguments)


def _port_accepts(port: int, timeout: float = 0.3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        try:
            return probe.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            return False


def _wait_for_accept(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_accepts(port):
            return True
        time.sleep(0.1)
    return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def run_mirror_start(
    paths: Paths,
    *,
    display: str = MIRROR_DISPLAY,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    spawn: Callable[..., object] | None = None,
    ready_timeout: float = 5.0,
    view_only: bool = False,
) -> MirrorState:
    """Mirror the running desktop on display with x0vncserver."""
    output = sys.stdout if out is None else out
    if display != MIRROR_DISPLAY:
        raise VncError(
            f"--mirror works only with {MIRROR_DISPLAY}; start a new VNC session "
            "on another display instead"
        )
    existing = read_mirror_state(paths, display)
    if existing is not None and pid_alive(existing.pid):
        raise VncError(
            f"the VNC mirror on {display} is already running (pid {existing.pid})"
        )
    if existing is not None:
        remove_mirror_state(paths, display)
    if not display_socket_ready(paths.x11_socket):
        raise VncError(
            f"there is no X server on {display}; start the desktop with 'desk start' first"
        )
    environment = os.environ if environ is None else environ
    program = resolve_program("x0vncserver", paths.prefix, environment.get("PATH"))
    if program is None:
        raise VncError("x0vncserver was not found; run 'desk vnc setup' to install tigervnc")
    port = rfb_port(display)
    if not _port_free(port):
        raise VncError(
            f"port {port} is already in use; is another VNC server listening already?"
        )
    arguments = build_x0vncserver_arguments(display=display, view_only=view_only)
    log_path = mirror_log_path(paths, display)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    launcher = subprocess.Popen if spawn is None else spawn
    with log_path.open("ab") as log:
        try:
            process = launcher(
                [program, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            raise VncError(f"could not start x0vncserver: {error}") from error
    if not _wait_for_accept(port, timeout=ready_timeout):
        pid = getattr(process, "pid", 0)
        if pid:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        raise VncError(
            f"x0vncserver did not listen on port {port}; see {log_path}"
        )
    state = MirrorState(
        display=display,
        pid=int(getattr(process, "pid", 0)),
        port=port,
        started_at=time.time(),
        view_only=view_only,
    )
    write_mirror_state(paths, state)
    print(f"Mirroring the desktop on {display}.", file=output)
    print(f"  address: 127.0.0.1:{port}", file=output)
    print(f"  pid: {state.pid}", file=output)
    print(f"  log: {log_path}", file=output)
    if view_only:
        print("  view-only: keyboard, mouse, and clipboard input are ignored.", file=output)
    else:
        print(
            "  the mirror shows the desktop that is running on the device; "
            "keyboard and mouse input reaches it.",
            file=output,
        )
    return state


def run_mirror_stop(
    paths: Paths,
    *,
    display: str = MIRROR_DISPLAY,
    out: TextIO | None = None,
) -> bool:
    """Stop the x0vncserver mirror on display."""
    output = sys.stdout if out is None else out
    state = read_mirror_state(paths, display)
    if state is None:
        print(f"There is no VNC mirror on {display}.", file=output)
        return False
    stopped = False
    if pid_alive(state.pid):
        try:
            os.kill(state.pid, 15)
            stopped = True
        except OSError:
            pass
        deadline = time.monotonic() + 5.0
        while stopped and time.monotonic() < deadline:
            if not pid_alive(state.pid):
                break
            time.sleep(0.1)
        if pid_alive(state.pid):
            try:
                os.kill(state.pid, 9)
            except OSError:
                pass
    remove_mirror_state(paths, display)
    print(f"Stopped the VNC mirror on {display}.", file=output)
    return True


def run_mirror_status(
    paths: Paths,
    *,
    display: str = MIRROR_DISPLAY,
    out: TextIO | None = None,
) -> bool:
    """Print whether the x0vncserver mirror on display is running."""
    output = sys.stdout if out is None else out
    state = read_mirror_state(paths, display)
    if state is None or not pid_alive(state.pid):
        print(f"The VNC mirror on {display} is not running.", file=output)
        return False
    mode = " (view-only)" if state.view_only else ""
    print(
        f"The VNC mirror on {display} is running at 127.0.0.1:{state.port} "
        f"(pid {state.pid}){mode}.",
        file=output,
    )
    return True


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
            + ". Check the tigervnc and darkhttpd packages."
        )
    print(
        "Found the Xvnc server, vncpasswd, x0vncserver, and darkhttpd.",
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
    """Stop the VNC server and the web proxy on display."""
    from . import webproxy  # imported here to avoid a circular import

    output = sys.stdout if out is None else out
    if webproxy.read_web_state(paths, display) is not None:
        webproxy.run_web_stop(paths, display=display, out=out, session_stopped=True)
    if display == MIRROR_DISPLAY:
        if read_mirror_state(paths, display) is not None:
            run_mirror_stop(paths, display=display, out=output)
        else:
            print(f"There is no VNC mirror on {display}.", file=output)
        return
    stop_session(paths.with_display(display), runtime=runtime, out=out, **timeouts)


def run_vnc_status(
    paths: Paths,
    *,
    display: str = VNC_DISPLAY,
    runtime: SessionRuntime | None = None,
) -> StatusReport:
    """Report the VNC server on display."""
    if display == MIRROR_DISPLAY:
        state = read_mirror_state(paths, display)
        running = state is not None and pid_alive(state.pid)
        return StatusReport(
            running=running,
            lines=(
                f"The VNC mirror on {display} is running at "
                f"127.0.0.1:{state.port} (pid {state.pid})"
                + (" (view-only)." if state.view_only else "."),
            )
            if running and state is not None
            else (f"The VNC mirror on {display} is not running.",),
        )
    return session_status(paths.with_display(display), runtime=runtime)

def _first_output_line(result) -> str:
    text = (result.stdout or "") + (result.stderr or "")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def run_vnc_version(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Print the installed VNC programs and the pinned browser components."""
    output = sys.stdout if out is None else out
    commands_runner = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ
    search_path = environment.get("PATH")
    for name in ("Xvnc", "x0vncserver"):
        program = resolve_program(name, paths.prefix, search_path)
        if program is None:
            print(f"{name}: not found", file=output)
            continue
        line = _first_output_line(
            commands_runner.run((program, "-version"), capture=True)
        )
        print(f"{name}: {line or 'unknown version'} ({program})", file=output)
    from . import webproxy

    novnc_installed = (webproxy.novnc_dir(paths) / "vnc.html").is_file()
    rs_installed = webproxy.rs_proxy_path(paths).is_file()
    python_installed = (
        webproxy.python_proxy_dir(paths) / "websockify" / "__init__.py"
    ).is_file()
    print(
        f"noVNC: {webproxy.NOVNC_VERSION} "
        f"({'installed' if novnc_installed else 'not installed'})",
        file=output,
    )
    print(
        f"websockify_rs: {webproxy.RS_VERSION} "
        f"({'installed' if rs_installed else 'not installed'})",
        file=output,
    )
    print(
        f"websockify: {webproxy.WEBSOCKIFY_VERSION} "
        f"({'installed' if python_installed else 'not installed'})",
        file=output,
    )

def run_vnc_doctor(
    paths: Paths,
    *,
    out: TextIO | None = None,
    color: str = "auto",
) -> int:
    """Run only the VNC checks through the shared doctor report."""
    from . import doctor

    return doctor.run_doctor(paths, out=out, color=color, vnc_only=True)
