"""Command line interface for desk.

Every command prints English text so that logs and pasted output stay easy to
search. The command returns 0 on success and 1 on a reported failure.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Sequence, TextIO

from . import app, container, doctor, session, setup
from .config import ConfigError, DeskError, Paths, load_paths
from .session import StartInterrupted
from .state import append_log, read_log_tail

DEFAULT_LOG_LINES = 100
LOGGED_COMMANDS = frozenset({"setup", "container", "app", "start", "stop"})

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
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    setup_parser = subparsers.add_parser(
        "setup",
        help="install the fixed Termux packages that the desktop needs",
        description=(
            "Install x11-repo, termux-x11-nightly, xfce4, dbus, and termux-am. "
            "Only missing packages are installed and nothing is upgraded."
        ),
    )
    setup_parser.set_defaults(handler=_handle_setup)

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
    launch_parser.set_defaults(handler=_handle_app_launch)

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
        help="report whether the recorded session is running",
        description="Exit with 0 while the session runs and with 1 otherwise.",
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
    return 0


def _handle_container_init(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    container.run_container_init(
        paths, arguments.name, image=arguments.image, out=output
    )
    return 0


def _handle_app_launch(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    app.run_app_launch(paths, arguments.target, out=output)
    return 0


def _handle_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return doctor.run_doctor(paths, out=output, color=arguments.color)


def _handle_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    session.start_session(paths, out=output, color=use_color)
    return 0


def _handle_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    report = session.session_status(paths)
    for line in report.lines:
        print(line, file=output)
    return 0 if report.running else 1


def _handle_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    session.stop_session(paths, out=output)
    return 0


def _handle_logs(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.lines == 0:
        return 0
    log_path = paths.desk_log if arguments.desk else paths.session_log
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
