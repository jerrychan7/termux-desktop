"""Command line interface for desk.

Every command prints English text so that logs and pasted output stay easy to
search. The command returns 0 on success and 1 on a reported failure.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence, TextIO

from . import doctor
from .cli_commands import _paint
from .cli_parser import build_parser
from .config import ConfigError, DeskError, load_paths
from .session import StartInterrupted
from .state import append_log

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


