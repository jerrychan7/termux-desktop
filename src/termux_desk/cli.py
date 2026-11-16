"""Command line interface for desk.

Every command prints English text so that logs and pasted output stay easy to
search. The command returns 0 on success and 1 on a reported failure.
"""

from __future__ import annotations

import os
import sys
from typing import Sequence, TextIO

from . import doctor, settings
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


def _apply_profile(argv: list[str]) -> list[str]:
    """Pull --profile out of the command line and select the profile.

    The option may appear anywhere, before or after the subcommand. The
    selected name is stored in the environment so that every handler and any
    session started from this process sees the same profile.
    """
    remaining: list[str] = []
    profile: str | None = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--":
            remaining.extend(argv[index:])
            break
        if item == "--profile":
            if index + 1 >= len(argv):
                raise DeskError("--profile needs a profile name.")
            profile = argv[index + 1]
            index += 2
            continue
        if item.startswith("--profile="):
            profile = item.split("=", 1)[1]
            index += 1
            continue
        remaining.append(item)
        index += 1
    if profile is not None:
        os.environ[settings.PROFILE_ENVIRONMENT] = profile
        settings.profile_name()  # validate the name before dispatch
    return remaining


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """Run one desk command and return its exit status."""
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        raw = _apply_profile(raw)
    except DeskError as error:
        print(f"desk: error: {error}", file=sys.stderr)
        return 1
    parser = build_parser()
    arguments = parser.parse_args(raw)
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


