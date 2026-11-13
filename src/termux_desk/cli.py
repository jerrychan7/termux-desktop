"""Command line interface for desk.

Every command prints English text so that logs and pasted output stay easy to
search. The command returns 0 on success and 1 on a reported failure.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__, app, container, doctor, fonts, install, menu, pkg, session, setup, upgrade, vnc, webproxy
from .config import DISPLAY_NAME, ConfigError, DeskError, Paths, load_paths
from .session import StartInterrupted
from .state import append_log, read_log_tail

DEFAULT_LOG_LINES = 100
LOGGED_COMMANDS = frozenset(
    {
        "setup",
        "upgrade",
        "font",
        "container",
        "app",
        "pkg",
        "start",
        "stop",
        "install",
        "uninstall",
        "update",
    }
)

SESSION_LOG_MARKERS = re.compile(
    r"(?P<error>\b(?:error|failed|failure|fatal|traceback)\b)"
    r"|(?P<error_phrase>\b(?:not\s+found|exited\s+before|did\s+not\s+appear|could\s+not)\b)"
    r"|(?P<status>\b(?:finished|exited)\b with status\s+(?P<status_number>[+-]?\d+))"
    r"|(?P<warning>\b(?:warning|warn|interrupted)\b)",
    re.IGNORECASE,
)
DESK_LOG_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)(?: (.*))?$")
DESK_LOG_MARKERS = re.compile(
    r"(?P<failed>\bfailed\b)"
    r"|(?P<status>(?:finished|exited) with status\s+(?P<status_number>[+-]?\d+))"
    r"|(?P<interrupted>\binterrupted\b)"
    r"|(?P<warning>\bwarn(?:ing)?\b)",
    re.IGNORECASE,
)


def _session_message_color(match: re.Match[str]) -> str | None:
    """Return the color for one marked word in a session log line."""
    if match.group("error") or match.group("error_phrase"):
        return doctor.ANSI_RED
    if match.group("status"):
        if int(match.group("status_number")) != 0:
            return doctor.ANSI_RED
        return None
    return doctor.ANSI_YELLOW


def _highlight_session_message(message: str) -> str:
    """Color only the marked words and phrases in one session log message."""
    pieces: list[str] = []
    last = 0
    for match in SESSION_LOG_MARKERS.finditer(message):
        color = _session_message_color(match)
        if color is None:
            continue
        pieces.append(message[last:match.start()])
        pieces.append(f"{color}{match.group(0)}{doctor.ANSI_RESET}")
        last = match.end()
    if not pieces:
        return message
    pieces.append(message[last:])
    return "".join(pieces)


def _format_session_log_line(line: str) -> str:
    """Color a session log line: marker, marked words, or a whole separator."""
    newline = ""
    content = line
    if content.endswith("\n"):
        content = content[:-1]
        newline = "\n"
    header = content.lower().strip()
    if header.startswith("=== desk") and header.endswith("==="):
        return f"{doctor.ANSI_SEPARATOR}{content}{doctor.ANSI_RESET}{newline}"
    marker = ""
    body = content
    if content.lower().startswith("[desk]"):
        marker = f"{doctor.ANSI_CYAN}{content[:6]}{doctor.ANSI_RESET}"
        body = content[6:]
    return f"{marker}{_highlight_session_message(body)}{newline}"


def _desk_message_color(match: re.Match[str]) -> str | None:
    """Return the color for one marked word in a command log message."""
    if match.group("failed"):
        return doctor.ANSI_RED
    if match.group("status"):
        if int(match.group("status_number")) != 0:
            return doctor.ANSI_RED
        return None
    return doctor.ANSI_YELLOW


def _highlight_desk_message(message: str) -> str:
    """Color only the marked words in one command log message."""
    pieces: list[str] = []
    last = 0
    for match in DESK_LOG_MARKERS.finditer(message):
        color = _desk_message_color(match)
        if color is None:
            continue
        pieces.append(message[last:match.start()])
        pieces.append(f"{color}{match.group(0)}{doctor.ANSI_RESET}")
        last = match.end()
    if not pieces:
        return message
    pieces.append(message[last:])
    return "".join(pieces)


def _format_desk_log_line(line: str) -> str:
    """Color a command log line: gray timestamp and marked message words."""
    newline = ""
    content = line
    if content.endswith("\n"):
        content = content[:-1]
        newline = "\n"
    match = DESK_LOG_ENTRY.match(content)
    if match is None:
        return f"{_highlight_desk_message(content)}{newline}"
    stamp = match.group(1)
    message = match.group(2)
    colored_stamp = f"{doctor.ANSI_GRAY}{stamp}{doctor.ANSI_RESET}"
    if message is None:
        return f"{colored_stamp}{newline}"
    return f"{colored_stamp} {_highlight_desk_message(message)}{newline}"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the M0 commands and help."""
    parser = argparse.ArgumentParser(
        prog="desk",
        description="Manage one XFCE desktop session on Termux.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"desk {__version__}",
        help="print the desk version and exit",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    setup_parser = subparsers.add_parser(
        "setup",
        help="install the fixed Termux packages that the desktop needs",
        description=(
            "Install x11-repo, termux-x11-nightly, xfce4, dbus, and termux-am. "
            "Only missing packages are installed and nothing is upgraded. "
            "After the packages, desk setup writes a font preference file that "
            "names the CJK faces installed on the device for its language, and "
            "language rules for the other CJK languages."
        ),
    )
    setup_parser.add_argument(
        "--vnc",
        action="store_true",
        help="also install the packages desk vnc needs",
    )
    setup_parser.set_defaults(handler=_handle_setup)

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="refresh the package index and upgrade the installed X11 packages",
        description=(
            "Refresh the package index with pkg update, then upgrade only the "
            "installed packages that the X11 repository provides. Packages "
            "from termux-main are not upgraded directly, but apt may upgrade "
            "one when an X11 package needs it."
        ),
    )
    upgrade_parser.set_defaults(handler=_handle_upgrade)

    font_parser = subparsers.add_parser(
        "font",
        help="manage the font preference that desk setup writes",
        description=(
            "desk setup writes a fontconfig file that names the CJK faces "
            "installed on the device for its language. desk font remove "
            "deletes that file. A file that desk setup did not write is left "
            "alone."
        ),
    )
    font_subparsers = font_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    font_remove_parser = font_subparsers.add_parser(
        "remove",
        help="remove the font preference file written by desk setup",
        description=(
            "Delete $PREFIX/etc/fonts/conf.d/63-cjk-prefer.conf when it "
            "carries the marker that desk setup writes."
        ),
    )
    font_remove_parser.set_defaults(handler=_handle_font_remove)

    container_parser = subparsers.add_parser(
        "container",
        help="create and prepare proot containers for GUI applications",
        description=(
            "Create a container with proot-distro and prepare it so its GUI "
            "applications can display on the desktop."
        ),
    )
    container_subparsers = container_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    init_parser = container_subparsers.add_parser(
        "init",
        help="create a container if needed and prepare it for GUI applications",
        description=(
            "Create the container when it does not exist, then create the mount "
            "points and install an X client and fonts inside it. Running it "
            "again only repeats the missing steps."
        ),
    )
    init_parser.add_argument("name", metavar="NAME", help="the container name")
    init_parser.add_argument(
        "--image",
        default=container.DEFAULT_IMAGE,
        metavar="IMAGE",
        help=f"the image to create the container from (default: {container.DEFAULT_IMAGE})",
    )
    init_parser.set_defaults(handler=_handle_container_init)

    app_parser = subparsers.add_parser(
        "app",
        help="launch GUI applications from proot containers",
        description="Run a container application on the display of the desk session.",
    )
    app_subparsers = app_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    launch_parser = app_subparsers.add_parser(
        "launch",
        help="launch one application from a container",
        description=(
            "Run the application inside the container on the display of the "
            "running desk session. The application stays in the process group "
            "of the caller, so desk stop reaches it too."
        ),
    )
    launch_parser.add_argument(
        "target",
        metavar="CONTAINER/APP",
        help="the container name and the program name, for example ubuntu-desktop-test/xterm",
    )
    launch_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        metavar="-- ARG",
        help="arguments passed to the program after '--'",
    )
    launch_parser.set_defaults(handler=_handle_app_launch)

    refresh_parser = app_subparsers.add_parser(
        "refresh",
        help="scan the containers and rewrite the generated menu entries",
        description=(
            "Read /usr/share/applications in every installed container and write "
            "one menu entry per application into the per-user applications "
            "directory. Entries written by an earlier refresh are removed."
        ),
    )
    refresh_parser.set_defaults(handler=_handle_app_refresh)

    pkg_parser = subparsers.add_parser(
        "pkg",
        help="run the package manager of a container",
        description=(
            "Forward the arguments to the package manager of the container, "
            "then refresh the menu entries. desk adds no flags: the package "
            "manager keeps its own defaults, prompts, output, and exit status. "
            "Use 'add' for install and 'rm' for remove."
        ),
    )
    pkg_parser.add_argument("container", metavar="CONTAINER", help="the container name")
    pkg_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        metavar="ARGS",
        help="arguments for the container package manager, for example install gimp",
    )
    pkg_parser.set_defaults(handler=_handle_pkg)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the environment and the recorded session without changing anything",
        description="Report missing programs, display state, and session state.",
    )
    doctor_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the status tags and the summary line "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    doctor_parser.set_defaults(handler=_handle_doctor)

    subparsers.add_parser(
        "help",
        help="show the top-level help message and exit",
        add_help=False,
    )

    start_parser = subparsers.add_parser(
        "start",
        help="start the desktop session",
        description=(
            "Start termux-x11, the Termux:X11 Android app, and XFCE in one new "
            "process group. desk start never installs packages."
        ),
    )
    start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the progress and error output "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    start_parser.set_defaults(handler=_handle_start)

    status_parser = subparsers.add_parser(
        "status",
        help="report whether recorded sessions are running",
        description=(
            "Report every recorded session, or only the session on DISPLAY. "
            "Exit with 0 while at least one session runs and with 1 otherwise."
        ),
    )
    status_parser.add_argument(
        "--display",
        metavar="DISPLAY",
        help="report only this X display, e.g. :0 or :1 (default: every session)",
    )
    status_parser.set_defaults(handler=_handle_status)

    stop_parser = subparsers.add_parser(
        "stop",
        help="stop the recorded session by signalling its verified process group",
        description=(
            "Send SIGTERM to the session process group, then SIGKILL after the "
            "timeout. desk does not search for processes by name."
        ),
    )
    stop_group = stop_parser.add_mutually_exclusive_group()
    stop_group.add_argument(
        "--display",
        metavar="DISPLAY",
        help="stop only the session on this X display (default: :0)",
    )
    stop_group.add_argument(
        "--all",
        action="store_true",
        help="stop every recorded session",
    )
    stop_parser.set_defaults(handler=_handle_stop)

    logs_parser = subparsers.add_parser(
        "logs",
        help="print the end of the session log",
        description="Print the last 100 lines of the session log by default.",
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=_positive_or_zero,
        default=DEFAULT_LOG_LINES,
        metavar="COUNT",
        help=f"number of lines to print (default: {DEFAULT_LOG_LINES})",
    )
    logs_parser.add_argument(
        "--display",
        default=DISPLAY_NAME,
        metavar="DISPLAY",
        help="read the session log of this X display (default: :0)",
    )
    logs_parser.add_argument(
        "--desk",
        action="store_true",
        help="read the desk command log instead of the session log",
    )
    logs_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the log lines "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    logs_parser.set_defaults(handler=_handle_logs)

    vnc_parser = subparsers.add_parser(
        "vnc",
        help="start and control a VNC desktop session",
        description=(
            "Run XFCE on an Xvnc display next to the termux-x11 desktop. "
            "Xvnc listens on localhost only; connect through an SSH tunnel "
            "with any VNC viewer."
        ),
    )
    vnc_subparsers = vnc_parser.add_subparsers(
        dest="vnc_command", required=True, metavar="COMMAND"
    )
    vnc_setup_parser = vnc_subparsers.add_parser(
        "setup",
        help="install tigervnc and darkhttpd, then verify the VNC programs",
        description=(
            "Install the x11-repo, tigervnc and darkhttpd packages when they are "
            "missing, then check for Xvnc, vncpasswd, x0vncserver and darkhttpd."
        ),
    )
    vnc_setup_parser.set_defaults(handler=_handle_vnc_setup)

    vnc_start_parser = vnc_subparsers.add_parser(
        "start",
        help="start the VNC session and wait until XFCE is ready",
        description="Start Xvnc and XFCE in one new process group.",
    )
    vnc_start_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    vnc_start_parser.add_argument(
        "--geometry",
        default=vnc.VNC_GEOMETRY,
        metavar="WIDTHxHEIGHT",
        help=f"the size of the VNC desktop (default: {vnc.VNC_GEOMETRY})",
    )
    vnc_start_parser.add_argument(
        "--password-file",
        metavar="PATH",
        help="use VncAuth with this password file instead of no authentication",
    )
    vnc_start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help="color the progress output (default: auto; auto colors only a terminal)",
    )
    vnc_start_parser.set_defaults(handler=_handle_vnc_start)

    vnc_stop_parser = vnc_subparsers.add_parser(
        "stop",
        help="stop the VNC session by signalling its verified process group",
        description="Send SIGTERM to the VNC session process group, then SIGKILL.",
    )
    vnc_stop_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    vnc_stop_parser.set_defaults(handler=_handle_vnc_stop)

    vnc_status_parser = vnc_subparsers.add_parser(
        "status",
        help="report whether the VNC session on a display is running",
        description="Exit with 0 while the session runs and with 1 otherwise.",
    )
    vnc_status_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    vnc_status_parser.set_defaults(handler=_handle_vnc_status)

    web_parser = vnc_subparsers.add_parser(
        "web",
        help="serve the VNC display to a web browser with noVNC",
        description=(
            "Download noVNC and a WebSocket proxy, then serve the page from "
            "the loopback interface. Starting the proxy starts the VNC session "
            "when it is not running; stopping the proxy leaves the session."
        ),
    )
    web_subparsers = web_parser.add_subparsers(
        dest="web_command", required=True, metavar="COMMAND"
    )
    web_setup_parser = web_subparsers.add_parser(
        "setup",
        help="download noVNC and the WebSocket proxy",
        description=(
            "Unpack the pinned noVNC release and the chosen proxy; the Rust "
            "proxy is the default and the Python proxy covers other CPUs."
        ),
    )
    web_setup_parser.add_argument(
        "--proxy",
        choices=webproxy.PROXY_CHOICES,
        default=webproxy.PROXY_RS,
        help="which proxy to install (default: rs)",
    )
    web_setup_parser.add_argument(
        "--force",
        action="store_true",
        help="replace files that are already installed",
    )
    web_setup_parser.set_defaults(handler=_handle_vnc_web_setup)

    web_start_parser = web_subparsers.add_parser(
        "start",
        help="start the browser page and its WebSocket proxy",
        description=(
            "Start the VNC session when needed, then the page and the proxy. "
            "The page listens on 127.0.0.1 by default."
        ),
    )
    web_start_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_start_parser.add_argument(
        "--proxy",
        choices=(webproxy.PROXY_PY, webproxy.PROXY_RS),
        default=webproxy.PROXY_RS,
        help="which proxy to run (default: rs)",
    )
    web_start_parser.add_argument(
        "--host",
        default=webproxy.DEFAULT_HOST,
        metavar="ADDRESS",
        help=f"the address the page listens on (default: {webproxy.DEFAULT_HOST})",
    )
    web_start_parser.add_argument(
        "--port",
        type=int,
        default=webproxy.DEFAULT_PORT,
        metavar="PORT",
        help=f"the page port (default: {webproxy.DEFAULT_PORT})",
    )
    web_start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help="color the progress output (default: auto; auto colors only a terminal)",
    )
    web_start_parser.set_defaults(handler=_handle_vnc_web_start)

    web_stop_parser = web_subparsers.add_parser(
        "stop",
        help="stop the WebSocket proxy but leave the VNC session running",
    )
    web_stop_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_stop_parser.set_defaults(handler=_handle_vnc_web_stop)

    web_status_parser = web_subparsers.add_parser(
        "status",
        help="report whether the browser proxy is running",
        description="Exit with 0 while the proxy runs and with 1 otherwise.",
    )
    web_status_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_status_parser.set_defaults(handler=_handle_vnc_web_status)

    install_parser = subparsers.add_parser(
        "install",
        help="install the desk command below the Termux prefix",
        description=(
            "Copy the sources and the session template to "
            "$PREFIX/lib/termux-desktop, copy the entry file to "
            "$PREFIX/bin/desk, and record the installed version. Running it "
            "again replaces the installed copy and keeps the previous one "
            "as lib/termux-desktop.previous."
        ),
    )
    install_parser.add_argument(
        "--source",
        default=".",
        metavar="DIR",
        help="the directory that holds desk, src, and templates (default: the current directory)",
    )
    install_parser.add_argument(
        "--release",
        default=None,
        metavar="TAG",
        help="record this release tag as the source; install.sh passes it after a download",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing $PREFIX/bin/desk that desk did not install",
    )
    install_parser.set_defaults(handler=_handle_install)

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="remove the installed desk command",
        description=(
            "Remove the installed package, the previous copy, and the entry "
            "file. State, logs, generated menu entries, and the font "
            "preference file that desk setup writes stay unless --purge is "
            "given, and a recorded session keeps desk from running with "
            "--purge."
        ),
    )
    uninstall_parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "also remove the state directory, the logs, the generated menu "
            "entries, and the font preference file"
        ),
    )
    uninstall_parser.set_defaults(handler=_handle_uninstall)

    update_parser = subparsers.add_parser(
        "update",
        help="replace the installed copy with a release",
        description=(
            "Download one release archive from GitHub and install it. The "
            "latest release is used when --version is not given. An update "
            "keeps the previous copy until the next successful update, and "
            "it does not touch the state, the logs, or the menu entries."
        ),
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        help="print the installed and the latest version without changing anything",
    )
    update_parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help="install this version instead of the latest release, for example 0.5.0",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="also replace a copy that was installed from a checkout",
    )
    update_parser.set_defaults(handler=_handle_update)

    version_parser = subparsers.add_parser(
        "version",
        help="print the desk version and where the installed copy came from",
        description=(
            "Print the recorded version, its source, and the install "
            "directory. A checkout that is not installed reports the version "
            "of this file and no install directory."
        ),
    )
    version_parser.set_defaults(handler=_handle_version)

    return parser


def _positive_or_zero(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text} is not a whole number") from error
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text} is negative")
    return value


def _paint(text: str, color: str, enabled: bool) -> str:
    """Wrap text in ANSI color when enabled, otherwise return it unchanged."""
    if not enabled:
        return text
    return f"{color}{text}{doctor.ANSI_RESET}"


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """Run one desk command and return its exit status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    output = sys.stdout if out is None else out
    if arguments.command == "help":
        # desk help must work before desk reads the Termux configuration.
        parser.print_help(file=output)
        return 0
    command = arguments.command
    handler = arguments.handler
    error_color = (
        command == "start"
        and doctor.color_enabled(arguments.color, sys.stderr, os.environ)
    )
    error_word = _paint("error:", doctor.ANSI_RED, error_color)
    interrupted_word = _paint("interrupted", doctor.ANSI_YELLOW, error_color)

    try:
        paths = load_paths()
    except ConfigError as error:
        print(f"desk: {error_word} {error}", file=sys.stderr)
        return 1

    if command in LOGGED_COMMANDS:
        append_log(paths.desk_log, f"{command}: started")

    try:
        exit_code = handler(paths, arguments, output)
    except StartInterrupted as interrupted:
        print(f"desk: {interrupted_word} by signal {interrupted.signum}.", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: interrupted by signal {interrupted.signum}")
        return 128 + interrupted.signum
    except DeskError as error:
        print(f"desk: {error_word} {error}", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: failed: {error}")
        return 1
    except KeyboardInterrupt:
        print(f"desk: {interrupted_word}.", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: interrupted")
        return 130

    if command in LOGGED_COMMANDS:
        append_log(paths.desk_log, f"{command}: finished with status {exit_code}")
    return exit_code


def _handle_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    setup.run_setup(paths, out=output)
    if arguments.vnc:
        vnc.run_vnc_setup(paths, out=output)
    return 0


def _handle_upgrade(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    upgrade.run_upgrade(paths, out=output)
    return 0


def _handle_font_remove(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    fonts.remove_preference(paths, out=output)
    return 0


def _handle_container_init(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    container.run_container_init(
        paths, arguments.name, image=arguments.image, out=output
    )
    return 0


def _handle_app_launch(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    extra = list(arguments.arguments)
    if extra and extra[0] == "--":
        extra = extra[1:]
    app.run_app_launch(paths, arguments.target, out=output, arguments=extra)
    return 0


def _handle_app_refresh(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    menu.run_refresh(paths, out=output)
    return 0


def _handle_pkg(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return pkg.run_pkg(
        paths,
        arguments.container,
        list(arguments.arguments),
        out=output,
    )


def _handle_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return doctor.run_doctor(paths, out=output, color=arguments.color)


def _handle_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    menu.refresh_for_session(paths, out=output)
    session.start_session(paths, out=output, color=use_color)
    return 0


def _handle_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.display:
        targets = (paths.with_display(arguments.display),)
    else:
        targets = tuple(paths.with_display(display) for display in session.session_displays(paths))
    if not targets:
        targets = (paths,)
    running = False
    first = True
    for target in targets:
        report = session.session_status(target)
        if not first:
            print(file=output)
        first = False
        for line in report.lines:
            print(line, file=output)
        running = running or report.running
    return 0 if running else 1


def _handle_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.all:
        targets = [paths.with_display(display) for display in session.session_displays(paths)]
        if not targets:
            print("No desktop session is recorded.", file=output)
            return 0
    elif arguments.display:
        targets = [paths.with_display(arguments.display)]
    else:
        targets = [paths]
    for target in targets:
        session.stop_session(target, out=output)
    return 0


def _handle_vnc_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    vnc.run_vnc_setup(paths, out=output)
    return 0


def _handle_vnc_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    password_file = Path(arguments.password_file) if arguments.password_file else None
    vnc.run_vnc_start(
        paths,
        display=arguments.display,
        geometry=arguments.geometry,
        password_file=password_file,
        out=output,
        color=use_color,
    )
    return 0


def _handle_vnc_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    vnc.run_vnc_stop(paths, display=arguments.display, out=output)
    return 0


def _handle_vnc_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    report = vnc.run_vnc_status(paths, display=arguments.display)
    for line in report.lines:
        print(line, file=output)
    return 0 if report.running else 1


def _handle_vnc_web_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    webproxy.run_web_setup(paths, proxy=arguments.proxy, force=arguments.force, out=output)
    return 0


def _handle_vnc_web_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    webproxy.run_web_start(
        paths,
        display=arguments.display,
        proxy=arguments.proxy,
        host=arguments.host,
        port=arguments.port,
        color=use_color,
        out=output,
    )
    return 0


def _handle_vnc_web_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    webproxy.run_web_stop(paths, display=arguments.display, out=output)
    return 0


def _handle_vnc_web_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return 0 if webproxy.run_web_status(paths, display=arguments.display, out=output) else 1


def _handle_logs(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.lines == 0:
        return 0
    target = paths.with_display(arguments.display)
    log_path = target.desk_log if arguments.desk else target.session_log
    lines = read_log_tail(log_path, arguments.lines)
    if lines is None:
        print(f"No log file at {log_path}.", file=output)
        return 0
    if not lines:
        print(f"The log file {log_path} is empty.", file=output)
        return 0
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    if use_color:
        if arguments.desk:
            text = "".join(_format_desk_log_line(line) for line in lines)
        else:
            text = "".join(_format_session_log_line(line) for line in lines)
    else:
        text = "".join(lines)
    output.write(text)
    if not lines[-1].endswith("\n"):
        output.write("\n")
    return 0


def _handle_install(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_install(
        paths,
        Path(arguments.source),
        force=arguments.force,
        release=arguments.release,
        out=output,
    )
    return 0


def _handle_uninstall(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_uninstall(paths, purge=arguments.purge, out=output)
    return 0


def _handle_update(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_update(
        paths,
        version=arguments.version,
        check=arguments.check,
        force=arguments.force,
        out=output,
    )
    return 0


def _handle_version(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    installed = install.read_installed(paths)
    print(f"desk {__version__ if installed is None else installed.version}", file=output)
    if installed is None:
        print("not installed (running from a checkout)", file=output)
        return 0
    print(f"source: {installed.source}", file=output)
    print(f"installed in {paths.install_root}", file=output)
    return 0
