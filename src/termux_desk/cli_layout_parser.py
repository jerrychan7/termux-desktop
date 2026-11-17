"""Argument parser entries for the desk layout commands."""

from __future__ import annotations

import argparse

from .cli_layout_commands import (
    _handle_layout_apply,
    _handle_layout_check,
    _handle_layout_init,
    _handle_layout_list,
    _handle_layout_reset,
    _handle_layout_show,
)


def add_layout_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the layout command and its subcommands."""
    layout_parser = subparsers.add_parser(
        "layout",
        help="list, check, apply or reset window manager and panel layouts",
        description=(
            "Layouts are YAML manifests that change a whitelist of xfwm4 settings "
            "and the panel size. Applying one snapshots the previous values so "
            "'desk layout reset' can restore them."
        ),
    )
    layout_subparsers = layout_parser.add_subparsers(dest="layout_command", metavar="COMMAND")
    layout_subparsers.required = True

    list_parser = layout_subparsers.add_parser(
        "list", help="list the available layouts and mark the applied one"
    )
    list_parser.set_defaults(handler=_handle_layout_list)

    show_parser = layout_subparsers.add_parser("show", help="print one layout manifest")
    show_parser.add_argument("name", help="layout name")
    show_parser.set_defaults(handler=_handle_layout_show)

    check_parser = layout_subparsers.add_parser("check", help="validate one layout")
    check_parser.add_argument("name", help="layout name")
    check_parser.set_defaults(handler=_handle_layout_check)

    apply_parser = layout_subparsers.add_parser(
        "apply",
        help="apply a layout and snapshot the values it replaces",
        description=(
            "Change the selected xfwm4 settings and the panel size of the running "
            "session. Add --dry-run to see the changes without writing them."
        ),
    )
    apply_parser.add_argument("name", help="layout name")
    apply_parser.add_argument(
        "--dry-run", action="store_true", help="print the changes without writing them"
    )
    apply_parser.add_argument(
        "--display",
        default=":0",
        metavar="DISPLAY",
        help="session to apply the layout to (default :0)",
    )
    apply_parser.set_defaults(handler=_handle_layout_apply)

    reset_parser = layout_subparsers.add_parser(
        "reset", help="restore the values that the applied layout replaced"
    )
    reset_parser.add_argument(
        "--display",
        default=":0",
        metavar="DISPLAY",
        help="session to reset (default :0)",
    )
    reset_parser.set_defaults(handler=_handle_layout_reset)

    init_parser = layout_subparsers.add_parser("init", help="write a starter layout manifest")
    init_parser.add_argument("name", help="layout name")
    init_parser.add_argument("--force", action="store_true", help="replace an existing file")
    init_parser.set_defaults(handler=_handle_layout_init)
