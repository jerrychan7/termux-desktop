"""Errors raised by the layout presets."""

from __future__ import annotations

from .config import DeskError


class LayoutError(DeskError):
    """Raised when a layout manifest or an xfconf write is invalid."""
