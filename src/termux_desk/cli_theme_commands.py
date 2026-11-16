"""Handlers for the desk theme commands."""

from __future__ import annotations

import argparse
from typing import TextIO

from . import theme as theme_manager
from .config import Paths


def _handle_theme_list(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return theme_manager.run_theme_list(paths, out=output)


def _handle_theme_show(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return theme_manager.run_theme_show(paths, arguments.name, out=output)


def _handle_theme_apply(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return theme_manager.run_theme_apply(
        paths,
        arguments.name,
        allow_unverified=arguments.allow_unverified,
        force=arguments.force,
        display=arguments.display,
        out=output,
    )


def _handle_theme_reset(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return theme_manager.run_theme_reset(paths, display=arguments.display, out=output)


def _handle_theme_init(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return theme_manager.run_theme_init(
        paths, arguments.name, force=arguments.force, out=output
    )
