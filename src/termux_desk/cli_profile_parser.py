"""Argument parser entries for the desk profile apply/reset commands."""

from __future__ import annotations

import argparse

from .cli_profile_commands import _handle_profile_apply, _handle_profile_reset


def add_profile_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the profile command and its apply/reset subcommands."""
    profile_parser = subparsers.add_parser(
        "profile",
        help="apply or reset the theme and layout selected by a profile",
        description=(
            "A profile can name one theme and one layout. This command applies "
            "both in one action (theme first, then layout) and resets both in "
            "the reverse order."
        ),
    )
    profile_subparsers = profile_parser.add_subparsers(
        dest="profile_action", required=True, metavar="command"
    )
    apply_parser = profile_subparsers.add_parser(
        "apply", help="apply the selected theme and layout"
    )
    apply_parser.add_argument(
        "--dry-run", action="store_true", help="only list what would be applied"
    )
    apply_parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="download assets that have no sha256 in their manifest",
    )
    apply_parser.add_argument(
        "--display", default=":0", help="X display of the running session (default: :0)"
    )
    apply_parser.set_defaults(handler=_handle_profile_apply)
    reset_parser = profile_subparsers.add_parser(
        "reset", help="reset the layout and theme applied by a profile"
    )
    reset_parser.add_argument(
        "--display", default=":0", help="X display of the running session (default: :0)"
    )
    reset_parser.set_defaults(handler=_handle_profile_reset)
