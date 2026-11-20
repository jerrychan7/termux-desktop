"""Handlers for the desk skin commands."""

from __future__ import annotations

import argparse
from typing import TextIO

from . import skin as skin_manager
from . import skin_model
from .config import Paths


def _handle_skin_list(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_skin_list(paths, out=output)


def _handle_skin_show(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_skin_show(paths, arguments.name, out=output)


def _handle_skin_check(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_skin_check(paths, arguments.name, out=output)


def _handle_skin_init(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_skin_init(
        paths, arguments.name, force=arguments.force, out=output
    )


def _handle_skin_apply(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_manager.run_skin_apply(
        paths,
        arguments.name,
        allow_unverified=arguments.allow_unverified,
        force=arguments.force,
        dry_run=arguments.dry_run,
        display=arguments.display,
        out=output,
    )


def _handle_skin_reset(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_manager.run_skin_reset(paths, display=arguments.display, out=output)


def _handle_appearance_list(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_model.run_appearance_list(paths, out=output)


def _handle_appearance_show(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_model.run_appearance_show(paths, arguments.name, out=output)


def _handle_appearance_check(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_model.run_appearance_check(paths, arguments.name, out=output)


def _handle_appearance_apply(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_manager.run_appearance_apply(
        paths,
        arguments.name,
        allow_unverified=arguments.allow_unverified,
        force=arguments.force,
        dry_run=arguments.dry_run,
        display=arguments.display,
        out=output,
    )


def _handle_appearance_reset(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_manager.run_appearance_reset(
        paths, display=arguments.display, out=output
    )


def _handle_layout_list(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_layout_preset_list(paths, out=output)


def _handle_layout_show(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_layout_preset_show(paths, arguments.name, out=output)


def _handle_layout_check(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_layout_preset_check(paths, arguments.name, out=output)


def _handle_layout_apply(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_manager.run_layout_apply(
        paths,
        arguments.name,
        dry_run=arguments.dry_run,
        display=arguments.display,
        out=output,
    )


def _handle_layout_reset(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_manager.run_layout_reset(paths, display=arguments.display, out=output)


def _handle_appearance_init(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return skin_model.run_appearance_init(
        paths, arguments.name, force=arguments.force, out=output
    )


def _handle_layout_init(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return skin_model.run_layout_init(paths, arguments.name, force=arguments.force, out=output)


def _handle_skin_build(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    """Run an experimental build recipe inside a container."""
    from pathlib import Path

    from . import skin_build

    return skin_build.run_skin_build(
        paths, Path(arguments.recipe).expanduser(), yes=arguments.yes, out=output
    )
