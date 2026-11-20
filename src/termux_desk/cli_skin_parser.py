"""Argument parser entries for the desk skin commands."""

from __future__ import annotations

import argparse

from .cli_skin_commands import (
    _handle_appearance_apply,
    _handle_appearance_check,
    _handle_appearance_init,
    _handle_appearance_list,
    _handle_appearance_reset,
    _handle_appearance_show,
    _handle_layout_apply,
    _handle_layout_check,
    _handle_layout_init,
    _handle_layout_list,
    _handle_layout_reset,
    _handle_layout_show,
    _handle_skin_apply,
    _handle_skin_build,
    _handle_skin_check,
    _handle_skin_init,
    _handle_skin_list,
    _handle_skin_reset,
    _handle_skin_show,
)

DISPLAY_HELP = "X display of the running session (default: :0)"


def _add_apply_options(parser: argparse.ArgumentParser, *, assets: bool) -> None:
    parser.add_argument(
        "name", nargs="?", help="skin name (default: the one the profile selects)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the changes without writing them"
    )
    if assets:
        parser.add_argument(
            "--allow-unverified",
            action="store_true",
            help="download assets that have no sha256 in their manifest",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="reinstall assets even when they already exist",
        )
    parser.add_argument("--display", default=":0", metavar="DISPLAY", help=DISPLAY_HELP)


def _add_reset_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--display", default=":0", metavar="DISPLAY", help=DISPLAY_HELP)


def _add_appearance_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "appearance",
        help="work with the appearance half of a skin",
        description=(
            "Appearance presets change the GTK theme, window borders, icons, "
            "cursors, wallpaper and fonts. Assets are downloaded when a preset "
            "is applied, not when it is listed."
        ),
    )
    commands = parser.add_subparsers(dest="appearance_command", required=True, metavar="COMMAND")

    listing = commands.add_parser("list", help="list the appearance manifests and presets")
    listing.set_defaults(handler=_handle_appearance_list)

    show = commands.add_parser("show", help="print one appearance")
    show.add_argument("name", help="name")
    show.set_defaults(handler=_handle_appearance_show)

    init = commands.add_parser("init", help="write a starter appearance manifest")
    init.add_argument("name", metavar="NAME")
    init.add_argument("--force", action="store_true", help="replace an existing manifest")
    init.set_defaults(handler=_handle_appearance_init)
    check = commands.add_parser("check", help="validate one appearance preset")
    check.add_argument("name", help="name")
    check.set_defaults(handler=_handle_appearance_check)

    apply_parser = commands.add_parser(
        "apply", help="apply an appearance and snapshot the values it replaces"
    )
    _add_apply_options(apply_parser, assets=True)
    apply_parser.set_defaults(handler=_handle_appearance_apply)

    reset = commands.add_parser(
        "reset", help="restore the appearance that was in place before the last apply"
    )
    _add_reset_options(reset)
    reset.set_defaults(handler=_handle_appearance_reset)


def _add_layout_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "layout",
        help="work with the layout half of a skin",
        description=(
            "Layout presets change a whitelist of xfwm4 settings and the panel "
            "size. Applying one snapshots the previous values so reset can "
            "restore them."
        ),
    )
    commands = parser.add_subparsers(dest="layout_command", required=True, metavar="COMMAND")

    listing = commands.add_parser("list", help="list the layout manifests and presets")
    listing.set_defaults(handler=_handle_layout_list)

    show = commands.add_parser("show", help="print one layout")
    show.add_argument("name", help="name")
    show.set_defaults(handler=_handle_layout_show)

    init = commands.add_parser("init", help="write a starter layout manifest")
    init.add_argument("name", metavar="NAME")
    init.add_argument("--force", action="store_true", help="replace an existing manifest")
    init.set_defaults(handler=_handle_layout_init)
    check = commands.add_parser("check", help="validate one layout preset")
    check.add_argument("name", help="name")
    check.set_defaults(handler=_handle_layout_check)

    apply_parser = commands.add_parser(
        "apply", help="apply a layout and snapshot the values it replaces"
    )
    _add_apply_options(apply_parser, assets=False)
    apply_parser.set_defaults(handler=_handle_layout_apply)

    reset = commands.add_parser(
        "reset", help="restore the values that the last applied layout replaced"
    )
    _add_reset_options(reset)
    reset.set_defaults(handler=_handle_layout_reset)


def add_skin_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the skin command, its subcommands and its two sub-domains."""
    parser = subparsers.add_parser(
        "skin",
        help="apply a whole desktop look: appearance and layout together",
        description=(
            "A skin is one YAML manifest with an appearance section and a layout "
            "section. Applying a skin applies the appearance first and the layout "
            "second; if the layout fails, the appearance is rolled back."
        ),
    )
    commands = parser.add_subparsers(dest="skin_command", required=True, metavar="COMMAND")

    listing = commands.add_parser("list", help="list the skins of this user")
    listing.set_defaults(handler=_handle_skin_list)

    show = commands.add_parser("show", help="print one skin manifest")
    show.add_argument("name", help="skin name")
    show.set_defaults(handler=_handle_skin_show)

    check = commands.add_parser("check", help="validate one skin and print its changes")
    check.add_argument("name", help="skin name")
    check.set_defaults(handler=_handle_skin_check)

    init = commands.add_parser("init", help="write a commented starter skin manifest")
    init.add_argument("name", help="skin name")
    init.add_argument("--force", action="store_true", help="back up and replace an existing file")
    init.set_defaults(handler=_handle_skin_init)

    apply_parser = commands.add_parser(
        "apply", help="apply the appearance and the layout of a skin"
    )
    _add_apply_options(apply_parser, assets=True)
    apply_parser.set_defaults(handler=_handle_skin_apply)

    reset = commands.add_parser("reset", help="restore what the last skin apply replaced")
    _add_reset_options(reset)
    reset.set_defaults(handler=_handle_skin_reset)

    build = commands.add_parser(
        "build",
        help="experimental: build a skin's assets from a recipe inside a container",
        description=(
            "Run a recipe inside a proot container: download its source archive, "
            "run the listed commands, then copy the produced artifacts into "
            "~/.local/share/termux-desktop/builds/. The recipe runs third-party "
            "code, so this is opt-in and not part of the normal apply path."
        ),
    )
    build.add_argument("recipe", help="path to the build recipe YAML")
    build.add_argument("--yes", action="store_true", help="confirm running the recipe")
    build.set_defaults(handler=_handle_skin_build)

    _add_appearance_commands(commands)
    _add_layout_commands(commands)
