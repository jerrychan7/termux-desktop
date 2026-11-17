"""Handlers for the desk layout commands."""

from __future__ import annotations

import argparse
from typing import TextIO

from . import layout as layout_manager
from .config import Paths


def _handle_layout_list(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_list(paths, out=output)


def _handle_layout_show(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_show(paths, arguments.name, out=output)


def _handle_layout_check(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_check(paths, arguments.name, out=output)


def _handle_layout_apply(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_apply(
        paths,
        arguments.name,
        dry_run=arguments.dry_run,
        display=arguments.display,
        out=output,
    )


def _handle_layout_reset(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_reset(paths, display=arguments.display, out=output)


def _handle_layout_init(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return layout_manager.run_layout_init(
        paths, arguments.name, force=arguments.force, out=output
    )
