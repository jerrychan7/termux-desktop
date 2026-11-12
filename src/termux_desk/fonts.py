"""Write the fontconfig preference for the CJK faces the device ships.

The fontconfig configuration that Termux ships names only the Korean faces
(Noto Sans CJK KR, Noto Serif CJK KR, Noto Sans Mono CJK KR), so CJK text
falls back to a Korean or Japanese face whatever the user's language is.

desk setup writes a small fontconfig file that names the faces of the user's
language first. The language comes from the Android system locale (getprop
persist.sys.locale), so a Japanese or Korean user gets that region's glyphs
instead of Simplified Chinese ones. When the device also has other regional
faces, the file adds language rules: an application that tells fontconfig
the language of the text gets that region's face even when the desktop runs
in a C.UTF-8 session.

The number 63 puts the file after 60-latin (which adds DejaVu to the Latin
lists) and before 65-nonlatin (which names the Korean faces). Latin text
keeps using DejaVu.

The families are probed, not hardcoded: the device may replace the Noto
faces with a vendor family such as OPPO Sans 4.0 SC. A candidate that is not
installed is skipped.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Protocol, TextIO

from .config import ConfigError, DeskError, Paths

PREFERENCE_FILE_NAME = "63-cjk-prefer.conf"
LEGACY_PREFERENCE_FILE_NAMES = ("63-cjk-prefer-sc.conf",)
GENERATED_MARK = "Written by desk setup"

DEFAULT_LANGUAGE = "zh-Hans"

# The lists are checked in order. Noto comes first because it is the same
# family on every device that has it; the vendor names are fallbacks for
# devices that replace the default CJK font.
SANS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "zh-Hans": (
        "Noto Sans CJK SC",
        "OPPO Sans 4.0 SC",
        "Source Han Sans SC",
        "Noto Sans SC",
        "MiSans",
        "HarmonyOS Sans SC",
    ),
    "zh-Hant": (
        "Noto Sans CJK TC",
        "OPPO Sans 4.0 TC",
        "Source Han Sans TC",
        "Noto Sans TC",
    ),
    "zh-Hant-HK": (
        "Noto Sans CJK HK",
        "Noto Sans CJK TC",
        "OPPO Sans 4.0 TC",
        "Source Han Sans HK",
        "Noto Sans HK",
    ),
    "ja": (
        "Noto Sans CJK JP",
        "Source Han Sans JP",
        "Noto Sans JP",
    ),
    "ko": (
        "Noto Sans CJK KR",
        "Source Han Sans KR",
        "Noto Sans KR",
    ),
}

SERIF_CANDIDATES: dict[str, tuple[str, ...]] = {
    "zh-Hans": (
        "Noto Serif CJK SC",
        "OPPO Serif SC",
        "Source Han Serif SC",
        "Noto Serif SC",
    ),
    "zh-Hant": (
        "Noto Serif CJK TC",
        "OPPO Serif TC",
        "Source Han Serif TC",
        "Noto Serif TC",
    ),
    "zh-Hant-HK": (
        "Noto Serif CJK HK",
        "Noto Serif CJK TC",
        "OPPO Serif TC",
        "Source Han Serif HK",
        "Noto Serif HK",
    ),
    "ja": (
        "Noto Serif CJK JP",
        "Source Han Serif JP",
        "Noto Serif JP",
    ),
    "ko": (
        "Noto Serif CJK KR",
        "Source Han Serif KR",
        "Noto Serif KR",
    ),
}

MONO_CANDIDATES: dict[str, tuple[str, ...]] = {
    "zh-Hans": (
        "Noto Sans Mono CJK SC",
        "Source Han Sans Mono SC",
        "Sarasa Mono SC",
    ),
    "zh-Hant": (
        "Noto Sans Mono CJK TC",
        "Source Han Sans Mono TC",
        "Sarasa Mono TC",
    ),
    "zh-Hant-HK": (
        "Noto Sans Mono CJK HK",
        "Noto Sans Mono CJK TC",
        "Sarasa Mono TC",
    ),
    "ja": (
        "Noto Sans Mono CJK JP",
        "Source Han Sans Mono JP",
        "Sarasa Mono JP",
    ),
    "ko": (
        "Noto Sans Mono CJK KR",
        "Source Han Sans Mono KR",
        "Sarasa Mono KR",
    ),
}

LOCALE_PROPERTIES = ("persist.sys.locale", "ro.product.locale")

# Each entry is (what the pattern's lang contains, the language whose faces
# are preferred). The rules are written in this order, and a later rule
# prepends after an earlier one, so a specific language wins over a broader
# one: zh-tw after plain zh, zh-hant-hk after zh-hant.
LANGUAGE_RULES: tuple[tuple[str, str], ...] = (
    ("zh", "zh-Hans"),
    ("zh-hant", "zh-Hant"),
    ("zh-hant-hk", "zh-Hant-HK"),
    ("zh-tw", "zh-Hant"),
    ("zh-hk", "zh-Hant-HK"),
    ("ja", "ja"),
    ("ko", "ko"),
)

GENERIC_FAMILIES = ("sans-serif", "serif", "monospace")


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


def language_tag(value: str | None) -> str | None:
    """Return the language tag used by the font tables.

    Android uses tags such as zh-Hans-CN, zh-Hant-TW, ja-JP, and ko-KR. A
    plain zh is treated as Simplified Chinese; a Hong Kong or Macao region
    selects the Hong Kong faces, and Taiwan or zh-Hant selects the
    Traditional Chinese faces.
    """
    if not value:
        return None
    tag = value.strip().replace("_", "-").lower()
    if not tag:
        return None
    parts = [part for part in tag.split("-") if part]
    if not parts or not parts[0].isalpha():
        return None
    primary = parts[0]
    if primary == "zh":
        if "hk" in parts or "mo" in parts:
            return "zh-Hant-HK"
        if "tw" in parts or "hant" in parts:
            return "zh-Hant"
        return "zh-Hans"
    return primary


def read_system_locale(
    *,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
    command: str | None = None,
) -> str | None:
    """Return the Android system locale, or None when getprop is missing."""
    runner = subprocess.run if run is None else run
    getprop = command if command is not None else shutil.which("getprop")
    if getprop is None:
        return None
    for prop in LOCALE_PROPERTIES:
        try:
            result = runner(
                [getprop, prop],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = (result.stdout or "").strip()
        if value:
            return value
    return None


def detected_language(
    *,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
    command: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the language of the device, falling back to $LC_ALL or $LANG."""
    raw = read_system_locale(run=run, command=command)
    if raw is None:
        environment = os.environ if environ is None else environ
        raw = environment.get("LC_ALL") or environment.get("LANG")
    return language_tag(raw)


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
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str | None, str | None, str | None]:
    """Return the sans-serif, serif, and monospace families to prefer.

    The monospace family falls back to the sans-serif family because a
    device usually has no monospace CJK face. None means no installed family
    was found for that role.
    """
    sans = _first_installed(matcher, SANS_CANDIDATES[language])
    serif = _first_installed(matcher, SERIF_CANDIDATES[language])
    mono = _first_installed(matcher, MONO_CANDIDATES[language]) or sans
    return sans, serif, mono


def _alias_line(family: str, face: str) -> str:
    return (
        f"  <alias><family>{family}</family>"
        f"<prefer><family>{face}</family></prefer></alias>"
    )


def _rule_lines(family: str, test: str, face: str) -> list[str]:
    return [
        '  <match target="pattern">',
        f'    <test qual="any" name="family" compare="eq"><string>{family}</string></test>',
        f'    <test qual="any" name="lang" compare="contains"><string>{test}</string></test>',
        f'    <edit name="family" mode="prepend" binding="strong"><string>{face}</string></edit>',
        "  </match>",
    ]


def build_preference_text(
    *,
    language: str,
    families: Mapping[str, tuple[str, str | None, str | None]],
) -> str:
    """Return the text of the fontconfig file.

    ``language`` names the user's language and must be present in
    ``families``. Each other language in ``families`` contributes rules that
    prefer its faces when an application says the text is in that language.
    """
    sans, serif, mono = families[language]
    lines = [
        '<?xml version="1.0"?>',
        '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">',
        f"<!-- {GENERATED_MARK}. Run desk setup to write it again. -->",
        "<fontconfig>",
        _alias_line("sans-serif", sans),
    ]
    if serif is not None:
        lines.append(_alias_line("serif", serif))
    if mono is not None:
        lines.append(_alias_line("monospace", mono))
    for test, tag in LANGUAGE_RULES:
        if tag == language or tag not in families:
            continue
        rule_sans, rule_serif, rule_mono = families[tag]
        lines.extend(_rule_lines("sans-serif", test, rule_sans))
        if rule_serif is not None:
            lines.extend(_rule_lines("serif", test, rule_serif))
        if rule_mono is not None:
            lines.extend(_rule_lines("monospace", test, rule_mono))
    lines.append("</fontconfig>")
    return "\n".join(lines) + "\n"


def write_preference(paths: Paths, text: str) -> str:
    """Write the preference file and return what happened.

    The returned value is one of "written", "unchanged", "kept", or
    "missing-directory". "kept" means the file exists with different content
    and without the marker that desk setup writes, so it was left alone.
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


def _legacy_paths(paths: Paths) -> tuple[Path, ...]:
    prefix = paths.prefix
    if prefix is None:
        return ()
    return tuple(
        prefix / "etc" / "fonts" / "conf.d" / name
        for name in LEGACY_PREFERENCE_FILE_NAMES
    )


def _remove_legacy(paths: Paths, *, out: TextIO) -> None:
    """Remove the preference files that older desk versions wrote."""
    for legacy in _legacy_paths(paths):
        if not legacy.is_file():
            continue
        try:
            content = legacy.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if GENERATED_MARK not in content:
            continue
        try:
            legacy.unlink()
        except OSError:
            continue
        print(f"Removed the old font preference file at {legacy}.", file=out)


def run_font_preference(
    paths: Paths,
    *,
    out: TextIO | None = None,
    matcher: FamilyMatcher | None = None,
    locale: str | None = None,
) -> None:
    """Write the font preference file for the device language.

    A missing fontconfig library, a missing fonts directory, or a device
    without a CJK face for the user's language only prints a warning. desk
    setup does not fail because of the font preference. ``locale`` overrides
    the system locale and exists for tests.
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

    tag = language_tag(locale) if locale is not None else detected_language()
    language = tag if tag in SANS_CANDIDATES else DEFAULT_LANGUAGE
    families: dict[str, tuple[str, str | None, str | None]] = {}
    for name in SANS_CANDIDATES:
        sans, serif, mono = choose_families(matcher, name)
        if sans is not None:
            families[name] = (sans, serif, mono)

    if language not in families:
        if language == DEFAULT_LANGUAGE:
            print(
                "The font preference was not written: no Simplified Chinese font was found.",
                file=output,
            )
        else:
            print(
                f"The font preference was not written: no {language} font was found.",
                file=output,
            )
        return

    text = build_preference_text(language=language, families=families)
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

    sans, serif, mono = families[language]
    chosen = [f"{sans} (sans-serif)"]
    if serif is not None:
        chosen.append(f"{serif} (serif)")
    if mono is not None and mono != sans:
        chosen.append(f"{mono} (monospace)")
    print(f"Font preference for {language}: {', '.join(chosen)}.", file=output)

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

    _remove_legacy(paths, out=output)


def remove_preference(
    paths: Paths,
    *,
    out: TextIO | None = None,
) -> None:
    """Remove the font preference files that desk setup wrote.

    A file without the marker that desk setup writes is left alone, because
    another program or the user wrote it. A missing file is not an error.
    The file names that older desk versions wrote are removed too.
    """
    output = sys.stdout if out is None else out
    if paths.prefix is None:
        raise DeskError("PREFIX is not set. desk font remove must run inside Termux.")
    candidates = (preference_path(paths),) + _legacy_paths(paths)
    removed = 0
    kept = 0
    for path in candidates:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DeskError(f"could not read {path}: {error}") from error
        if GENERATED_MARK not in content:
            print(
                f"The file at {path} was not written by desk setup; it was not removed.",
                file=output,
            )
            kept += 1
            continue
        try:
            path.unlink()
        except OSError as error:
            raise DeskError(f"could not remove {path}: {error}") from error
        print(
            f"Removed the font preference file at {path}. "
            "Applications pick up the change when they restart.",
            file=output,
        )
        removed += 1
    if removed == 0 and kept == 0:
        print(
            f"There is no font preference file at {preference_path(paths)}.",
            file=output,
        )
