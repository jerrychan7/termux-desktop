"""Command line interface for desk.

Every command prints English text so that logs and pasted output stay easy to
search. The command returns 0 on success and 1 on a reported failure.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence, TextIO

from . import doctor, session, setup
from .config import ConfigError, DeskError, Paths, load_paths
from .session import StartInterrupted
from .state import append_log, read_log_tail

DEFAULT_LOG_LINES = 100
LOGGED_COMMANDS = frozenset({"setup", "start", "stop"})


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the six M0 commands."""
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

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the environment and the recorded session without changing anything",
        description="Report missing programs, display state, and session state.",
    )
    doctor_parser.set_defaults(handler=_handle_doctor)

    start_parser = subparsers.add_parser(
        "start",
        help="start the desktop session",
        description=(
            "Start termux-x11, the Termux:X11 Android app, and XFCE in one new "
            "process group. desk start never installs packages."
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


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """Run one desk command and return its exit status."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    output = sys.stdout if out is None else out
    command = arguments.command
    handler = arguments.handler

    try:
        paths = load_paths()
    except ConfigError as error:
        print(f"desk: error: {error}", file=sys.stderr)
        return 1

    if command in LOGGED_COMMANDS:
        append_log(paths.desk_log, f"{command}: started")

    try:
        exit_code = handler(paths, arguments, output)
    except StartInterrupted as interrupted:
        print(f"desk: interrupted by signal {interrupted.signum}.", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: interrupted by signal {interrupted.signum}")
        return 128 + interrupted.signum
    except DeskError as error:
        print(f"desk: error: {error}", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: failed: {error}")
        return 1
    except KeyboardInterrupt:
        print("desk: interrupted.", file=sys.stderr)
        if command in LOGGED_COMMANDS:
            append_log(paths.desk_log, f"{command}: interrupted")
        return 130

    if command in LOGGED_COMMANDS:
        append_log(paths.desk_log, f"{command}: finished with status {exit_code}")
    return exit_code


def _handle_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    setup.run_setup(paths, out=output)
    return 0


def _handle_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return doctor.run_doctor(paths, out=output)


def _handle_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    session.start_session(paths, out=output)
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
    output.write("".join(lines))
    if not lines[-1].endswith("\n"):
        output.write("\n")
    return 0
