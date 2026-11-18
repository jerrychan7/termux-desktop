"""The parsers of the configuration commands."""

from __future__ import annotations

import argparse

from .cli_commands import (
    _handle_config_check,
    _handle_config_init,
    _handle_config_path,
    _handle_config_profiles,
    _handle_config_set,
    _handle_config_set_name,
    _handle_config_set_section,
    _handle_config_show,
    _handle_config_unset,
    _handle_config_unset_name,
    _handle_config_unset_section,
)

VNC_KEYS = ("display", "geometry", "depth", "host", "port", "proxy", "view_only")
FONT_KEYS = ("language", "sans_serif", "serif", "monospace")


def add_config_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the commands that inspect or edit the configuration file."""
    config_parser = subparsers.add_parser(
        "config",
        help="inspect, validate, initialize, or edit the YAML configuration",
        description=(
            "Read the configuration file at "
            "$XDG_CONFIG_HOME/termux-desktop/config.yaml, or the built-in "
            "defaults when it does not exist. The set and unset commands "
            "rewrite one section and keep the rest of the file."
        ),
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_action", required=True, metavar="action"
    )
    _add_read_commands(config_subparsers)
    _add_set_commands(config_subparsers)
    _add_unset_commands(config_subparsers)


def _add_read_commands(config_subparsers: argparse._SubParsersAction) -> None:
    config_subparsers.add_parser(
        "path",
        help="print the configuration file path",
        description="Print the path desk reads the configuration from.",
    ).set_defaults(handler=_handle_config_path)
    config_subparsers.add_parser(
        "show",
        help="print the effective configuration",
    ).set_defaults(handler=_handle_config_show)
    config_subparsers.add_parser(
        "check",
        help="validate the configuration file",
    ).set_defaults(handler=_handle_config_check)
    config_subparsers.add_parser(
        "profiles",
        help="list the named configuration profiles",
        description=(
            "Print the active profile and the profiles that have a file in "
            "$XDG_CONFIG_HOME/termux-desktop/profiles."
        ),
    ).set_defaults(handler=_handle_config_profiles)
    init_parser = config_subparsers.add_parser(
        "init",
        help="copy the built-in defaults to the configuration path",
        description=(
            "Write the built-in commented defaults to the user configuration "
            "path. An existing file is only replaced when --force is given; "
            "the command then backs the old file up as config.yaml.bak first. "
            "Symbolic links and non-regular files are never replaced."
        ),
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing configuration file (a backup is kept)",
    )
    init_parser.set_defaults(handler=_handle_config_init)


def _add_set_commands(config_subparsers: argparse._SubParsersAction) -> None:
    set_parser = config_subparsers.add_parser(
        "set",
        help="change one configuration value",
        description=(
            "Change one section of the user configuration file. The section "
            "is rewritten; comments outside it and the rest of the file are "
            "kept. Use unset to reset a value to its default."
        ),
    )
    set_subparsers = set_parser.add_subparsers(
        dest="config_set_action", required=True, metavar="kind"
    )
    _add_set_app_commands(set_subparsers)
    vnc_parser = set_subparsers.add_parser("vnc", help="set a VNC default")
    vnc_parser.add_argument("key", choices=VNC_KEYS, metavar="KEY")
    vnc_parser.add_argument("value", metavar="VALUE")
    vnc_parser.set_defaults(handler=_handle_config_set_section)
    ime_parser = set_subparsers.add_parser("ime", help="set the input method default")
    ime_parser.add_argument("key", choices=("enabled",), metavar="KEY")
    ime_parser.add_argument("value", metavar="VALUE")
    ime_parser.set_defaults(handler=_handle_config_set_section)
    fonts_parser = set_subparsers.add_parser("fonts", help="set a font preference default")
    fonts_parser.add_argument("key", choices=FONT_KEYS, metavar="KEY")
    fonts_parser.add_argument("value", metavar="VALUE")
    fonts_parser.set_defaults(handler=_handle_config_set_section)
    skin_parser = set_subparsers.add_parser("skin", help="select the skin this profile uses")
    skin_parser.add_argument("name", metavar="NAME")
    skin_parser.set_defaults(handler=_handle_config_set_name)


def _add_set_app_commands(set_subparsers: argparse._SubParsersAction) -> None:
    app_parser = set_subparsers.add_parser(
        "app", help="set a launch override of one container application"
    )
    app_parser.add_argument("spec", metavar="CONTAINER/APP")
    app_subparsers = app_parser.add_subparsers(
        dest="config_set_field", required=True, metavar="field"
    )
    program_parser = app_subparsers.add_parser("program", help="set the program")
    program_parser.add_argument("value", metavar="PROGRAM")
    program_parser.set_defaults(handler=_handle_config_set)
    prefix_parser = app_subparsers.add_parser(
        "args-prefix", help="replace the argument prefix"
    )
    prefix_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        metavar="ARG",
        help=(
            "arguments inserted after the program; put '--' before the first "
            "argument when it starts with '-' (a '--' later in the list is "
            "passed to the application literally)"
        ),
    )
    prefix_parser.set_defaults(handler=_handle_config_set)
    environment_parser = app_subparsers.add_parser(
        "environment", help="set one environment variable"
    )
    environment_parser.add_argument("name", metavar="NAME")
    environment_parser.add_argument(
        "value",
        nargs=argparse.REMAINDER,
        metavar="VALUE",
        help="the value; put '--' before it when it starts with '-'",
    )
    environment_parser.set_defaults(handler=_handle_config_set)


def _add_unset_commands(config_subparsers: argparse._SubParsersAction) -> None:
    unset_parser = config_subparsers.add_parser(
        "unset",
        help="reset one configuration value to its default",
        description=(
            "Remove one value from the apps section or reset a vnc, ime, or "
            "fonts value to its built-in default. The section is rewritten; "
            "comments outside it and the rest of the file are kept."
        ),
    )
    unset_subparsers = unset_parser.add_subparsers(
        dest="config_unset_action", required=True, metavar="kind"
    )
    unset_app_parser = unset_subparsers.add_parser(
        "app", help="remove a launch override of one container application"
    )
    unset_app_parser.add_argument("spec", metavar="CONTAINER/APP")
    unset_app_subparsers = unset_app_parser.add_subparsers(
        dest="config_unset_field", required=True, metavar="field"
    )
    unset_app_subparsers.add_parser(
        "program", help="remove the program override"
    ).set_defaults(handler=_handle_config_unset)
    unset_app_subparsers.add_parser(
        "args-prefix", help="remove the argument prefix"
    ).set_defaults(handler=_handle_config_unset)
    unset_environment_parser = unset_app_subparsers.add_parser(
        "environment", help="remove one environment override"
    )
    unset_environment_parser.add_argument("name", metavar="NAME")
    unset_environment_parser.set_defaults(handler=_handle_config_unset)
    vnc_unset = unset_subparsers.add_parser("vnc", help="reset a VNC default")
    vnc_unset.add_argument("key", choices=VNC_KEYS, metavar="KEY")
    vnc_unset.set_defaults(handler=_handle_config_unset_section)
    ime_unset = unset_subparsers.add_parser("ime", help="reset the input method default")
    ime_unset.add_argument("key", choices=("enabled",), metavar="KEY")
    ime_unset.set_defaults(handler=_handle_config_unset_section)
    fonts_unset = unset_subparsers.add_parser("fonts", help="reset a font default")
    fonts_unset.add_argument("key", choices=FONT_KEYS, metavar="KEY")
    fonts_unset.set_defaults(handler=_handle_config_unset_section)
    skin_unset = unset_subparsers.add_parser("skin", help="clear the selected skin")
    skin_unset.set_defaults(handler=_handle_config_unset_name)
