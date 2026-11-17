"""Handlers for the desk profile apply/reset commands."""

from __future__ import annotations

import argparse
from typing import TextIO

from . import profile_actions
from .config import Paths


def _handle_profile_apply(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return profile_actions.run_profile_apply(
        paths,
        display=arguments.display,
        dry_run=arguments.dry_run,
        allow_unverified=arguments.allow_unverified,
        out=output,
    )


def _handle_profile_reset(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return profile_actions.run_profile_reset(paths, display=arguments.display, out=output)
