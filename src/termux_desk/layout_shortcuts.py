"""Keyboard shortcut entries for layout manifests.

Split out of ``layout.py`` so that module stays within a readable size: the
rules here (channel name, group prefixes, key/value validation) do not depend
on the rest of the layout machinery.
"""

from __future__ import annotations

from typing import Mapping

from .config import DeskError

CHANNEL = "xfce4-keyboard-shortcuts"
GROUPS = {
    "shortcuts": "/commands",
    "window_shortcuts": "/xfwm4/custom",
}


class ShortcutError(DeskError):
    """A shortcut entry in a layout manifest is not usable."""


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ShortcutError(f"{what} must be a non-empty string of at most 256 characters")
    if any(character < " " or character == "\x7f" for character in value):
        raise ShortcutError(f"{what} must not contain control characters")
    return value


def entries(section: Mapping[str, object], group: str) -> tuple[tuple[str, str], ...]:
    """Return ``(property, value)`` pairs for one shortcut group."""
    pairs: list[tuple[str, str]] = []
    prefix = GROUPS[group]
    for key, value in section.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            raise ShortcutError("shortcut keys must be non-empty names of at most 64 characters")
        pairs.append((f"{prefix}/{key}", _text(value, f"the {key} shortcut")))
    return tuple(pairs)
