"""Helpers for printing untrusted text safely."""

from __future__ import annotations

import unicodedata


def printable(value: str) -> str:
    """Escape characters that could steer a terminal before printing text."""
    pieces: list[str] = []
    for character in value:
        if character == "\n":
            pieces.append("\\n")
        elif character == "\t":
            pieces.append("\\t")
        elif character == "\r":
            pieces.append("\\r")
        elif (
            ord(character) < 32
            or 0x7F <= ord(character) <= 0x9F
            or unicodedata.category(character) in ("Cf", "Cs")
        ):
            code = ord(character)
            if code <= 0xFF:
                pieces.append(f"\\x{code:02x}")
            else:
                pieces.append(f"\\u{code:04x}")
        else:
            pieces.append(character)
    return "".join(pieces)


def single_line(value: str) -> str:
    """Return text that cannot break a one-line field or terminal line."""
    pieces: list[str] = []
    for character in value:
        code = ord(character)
        if (
            code < 0x20
            or 0x7F <= code <= 0x9F
            or character in ("\u0085", "\u2028", "\u2029")
        ):
            pieces.append(" ")
        else:
            pieces.append(character)
    return "".join(pieces)
