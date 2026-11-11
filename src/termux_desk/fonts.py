"""Write the fontconfig preference for the Simplified Chinese faces.

The fontconfig configuration that Termux ships names only the Korean faces
(Noto Sans CJK KR, Noto Serif CJK KR, Noto Sans Mono CJK KR), so a request
for characters that cover Simplified Chinese falls back to a Korean or
Japanese face. The glyphs then carry Korean or Japanese shapes.

desk setup writes a small fontconfig file that names the Simplified Chinese
faces installed on the device. The number 63 puts it after 60-latin (which
adds DejaVu to the Latin lists) and before 65-nonlatin (which names the
Korean faces). Latin text keeps using DejaVu, and Chinese text gets the
Simplified Chinese face.

The families are probed, not hardcoded: the device may replace the Noto
faces with a vendor family such as OPPO Sans 4.0 SC. A candidate that is not
installed is skipped.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Protocol, TextIO

from .config import ConfigError, Paths

PREFERENCE_FILE_NAME = "63-cjk-prefer-sc.conf"
GENERATED_MARK = "Written by desk setup"

# The lists are checked in order. Noto comes first because it is the same
# family on every device that has it; the vendor names are fallbacks for
# devices that replace the default Chinese font.
SANS_CANDIDATES = (
    "Noto Sans CJK SC",
    "OPPO Sans 4.0 SC",
    "Source Han Sans SC",
    "Noto Sans SC",
    "MiSans",
    "HarmonyOS Sans SC",
)
SERIF_CANDIDATES = (
    "Noto Serif CJK SC",
    "OPPO Serif SC",
    "Source Han Serif SC",
    "Noto Serif SC",
)
MONO_CANDIDATES = (
    "Noto Sans Mono CJK SC",
    "Source Han Sans Mono SC",
    "Sarasa Mono SC",
)


class FamilyMatcher(Protocol):
    """Answer whether one font family is installed."""

    def family_exists(self, name: str) -> bool:
        """Return True when fontconfig can resolve the family to itself."""


class FontconfigMatcher:
    """Check installed families through the fontconfig library.

    The fontconfig package does not install fc-match or fc-list, so the
    library is called through ctypes. The check asks fontconfig to match a
    pattern that contains only the family name. When the family is installed
    the match returns that same family; otherwise fontconfig falls back to
    another font with a different family name.
    """

    def __init__(self, library_path: Path):
        library = ctypes.CDLL(str(library_path))
        library.FcInit.restype = ctypes.c_int
        library.FcInit.argtypes = []
        library.FcConfigGetCurrent.restype = ctypes.c_void_p
        library.FcNameParse.argtypes = [ctypes.c_char_p]
        library.FcNameParse.restype = ctypes.c_void_p
        library.FcDefaultSubstitute.argtypes = [ctypes.c_void_p]
        library.FcDefaultSubstitute.restype = None
        library.FcConfigSubstitute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        library.FcConfigSubstitute.restype = ctypes.c_int
        library.FcFontMatch.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
        ]
        library.FcFontMatch.restype = ctypes.c_void_p
        library.FcPatternGetString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p),
        ]
        library.FcPatternGetString.restype = ctypes.c_int
        library.FcInit()
        self._library = library
        self._config = library.FcConfigGetCurrent()

    def family_exists(self, name: str) -> bool:
        """Return True when fontconfig resolves the family to itself."""
        library = self._library
        pattern = library.FcNameParse(name.encode("utf-8"))
        if not pattern:
            return False
        library.FcDefaultSubstitute(pattern)
        library.FcConfigSubstitute(self._config, pattern, 0)
        result = ctypes.c_int(0)
        font = library.FcFontMatch(self._config, pattern, ctypes.byref(result))
        if not font:
            return False
        value = ctypes.c_char_p()
        if library.FcPatternGetString(font, b"family", 0, ctypes.byref(value)) != 0:
            return False
        if not value.value:
            return False
        return value.value.decode("utf-8") == name


def preference_path(paths: Paths) -> Path:
    """Return the fontconfig file that desk setup writes."""
    prefix = paths.prefix
    if prefix is None:
        raise ConfigError("PREFIX is not set.")
    return prefix / "etc" / "fonts" / "conf.d" / PREFERENCE_FILE_NAME


def _first_installed(
    matcher: FamilyMatcher,
    candidates: tuple[str, ...],
) -> str | None:
    for name in candidates:
        if matcher.family_exists(name):
            return name
    return None


def choose_families(
    matcher: FamilyMatcher,
) -> tuple[str | None, str | None, str | None]:
    """Return the sans-serif, serif, and monospace families to prefer.

    The monospace family falls back to the sans-serif family because a
    device usually has no monospace Simplified Chinese face. None means no
    installed family was found for that role.
    """
    sans = _first_installed(matcher, SANS_CANDIDATES)
    serif = _first_installed(matcher, SERIF_CANDIDATES)
    mono = _first_installed(matcher, MONO_CANDIDATES) or sans
    return sans, serif, mono


def build_preference_text(
    *,
    sans: str,
    serif: str | None,
    mono: str | None,
) -> str:
    """Return the text of the fontconfig file."""
    lines = [
        '<?xml version="1.0"?>',
        '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
        f"<!-- {GENERATED_MARK}. Run desk setup to write it again. -->",
        "<fontconfig>",
        f"  <alias><family>sans-serif</family><prefer><family>{sans}</family></prefer></alias>",
    ]
    if serif is not None:
        lines.append(
            "  <alias><family>serif</family>"
            f"<prefer><family>{serif}</family></prefer></alias>"
        )
    if mono is not None:
        lines.append(
            "  <alias><family>monospace</family>"
            f"<prefer><family>{mono}</family></prefer></alias>"
        )
    lines.append("</fontconfig>")
    return "\n".join(lines) + "\n"


def write_preference(paths: Paths, text: str) -> str:
    """Write the preference file and return what happened.

    The returned value is one of "written", "unchanged", or "kept". "kept"
    means the file exists with different content and without the marker that
    desk setup writes, so it was left alone.
    """
    path = preference_path(paths)
    directory = path.parent
    if not directory.is_dir():
        return "missing-directory"
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing == text:
            return "unchanged"
        if GENERATED_MARK not in existing:
            return "kept"
    path.write_text(text, encoding="utf-8")
    return "written"


def run_font_preference(
    paths: Paths,
    *,
    out: TextIO | None = None,
    matcher: FamilyMatcher | None = None,
) -> None:
    """Write the font preference file when the device has the faces.

    A missing fontconfig library, a missing fonts directory, or a device
    without a Simplified Chinese face only prints a warning. desk setup does
    not fail because of the font preference.
    """
    output = sys.stdout if out is None else out
    if paths.prefix is None or not paths.prefix.is_dir():
        print("The font preference was not written: PREFIX is not set.", file=output)
        return

    if matcher is None:
        library_path = paths.prefix / "lib" / "libfontconfig.so"
        if not library_path.is_file():
            print(
                "The font preference was not written: the fontconfig library was not found.",
                file=output,
            )
            return
        try:
            matcher = FontconfigMatcher(library_path)
        except OSError as error:
            print(f"The font preference was not written: {error}", file=output)
            return

    sans, serif, mono = choose_families(matcher)
    if sans is None:
        print(
            "The font preference was not written: no Simplified Chinese font was found.",
            file=output,
        )
        return

    text = build_preference_text(sans=sans, serif=serif, mono=mono)
    try:
        status = write_preference(paths, text)
    except OSError as error:
        print(f"The font preference was not written: {error}", file=output)
        return

    if status == "missing-directory":
        print(
            "The font preference was not written: the fontconfig directory was not found.",
            file=output,
        )
        return

    chosen = [f"{sans} (sans-serif)"]
    if serif is not None:
        chosen.append(f"{serif} (serif)")
    if mono is not None and mono != sans:
        chosen.append(f"{mono} (monospace)")
    print(f"Simplified Chinese font preference: {', '.join(chosen)}.", file=output)

    path = preference_path(paths)
    if status == "kept":
        print(
            f"The font preference at {path} was not changed: it has other content.",
            file=output,
        )
    elif status == "unchanged":
        print(f"The font preference is already in place at {path}.", file=output)
    else:
        print(
            f"Wrote the font preference to {path}. Applications use it when they start.",
            file=output,
        )
