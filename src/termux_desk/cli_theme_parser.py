"""Argument parser entries for the desk theme commands."""

from __future__ import annotations

import argparse

from .cli_theme_commands import (
    _handle_theme_apply,
    _handle_theme_init,
    _handle_theme_list,
    _handle_theme_reset,
    _handle_theme_show,
)


def add_theme_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the theme command and its subcommands."""
    theme_parser = subparsers.add_parser(
        "theme",
        help="list, show, apply or reset appearance themes",
        description=(
            "Appearance themes are YAML manifests that name the GTK theme, xfwm4 "
            "theme, icons, cursors, wallpaper and fonts. Applying one writes the "
            "running session's xfconf values and keeps the previous values so "
            "'desk theme reset' can restore them."
        ),
    )
    theme_subparsers = theme_parser.add_subparsers(dest="theme_command", metavar="COMMAND")
    theme_subparsers.required = True

    list_parser = theme_subparsers.add_parser(
        "list", help="list the available themes and mark the applied one"
    )
    list_parser.set_defaults(handler=_handle_theme_list)

    show_parser = theme_subparsers.add_parser("show", help="print one theme manifest")
    show_parser.add_argument("name", help="theme name")
    show_parser.set_defaults(handler=_handle_theme_show)

    apply_parser = theme_subparsers.add_parser(
        "apply",
        help="install missing assets and apply a theme",
        description=(
            "Install the assets the manifest declares, check that every component "
            "resolves, then write the session's xfconf values. Themes without a "
            "sha256 need --allow-unverified the first time; the downloaded hash "
            "is recorded and checked on later applies."
        ),
    )
    apply_parser.add_argument("name", nargs="?", help="theme name (default: the selected one)")
    apply_parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="download an asset without a sha256 and record its hash",
    )
    apply_parser.add_argument(
        "--force", action="store_true", help="replace assets that are already installed"
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check the manifest and report the values it would set, without writing anything",
    )
    apply_parser.add_argument(
        "--display",
        default=":0",
        metavar="DISPLAY",
        help="session to apply the theme to (default :0; VNC sessions need a restart)",
    )
    apply_parser.set_defaults(handler=_handle_theme_apply)

    reset_parser = theme_subparsers.add_parser(
        "reset", help="restore the values that the applied theme replaced"
    )
    reset_parser.add_argument(
        "--display",
        default=":0",
        metavar="DISPLAY",
        help="session to reset (default :0)",
    )
    reset_parser.set_defaults(handler=_handle_theme_reset)

    init_parser = theme_subparsers.add_parser(
        "init", help="write a starter theme manifest for editing"
    )
    init_parser.add_argument("name", help="theme name")
    init_parser.add_argument(
        "--force", action="store_true", help="replace an existing manifest"
    )
    init_parser.set_defaults(handler=_handle_theme_init)
