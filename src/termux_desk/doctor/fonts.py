"""Read-only checks for the font preference."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..config import DeskError, Paths
from .base import FAILED, OK, WARNING, CheckResult


def _fontconfig_check(library: Path, required: bool) -> CheckResult:
    """Report the fontconfig shared library."""
    if library.is_file():
        return CheckResult(name="fontconfig", status=OK, message=str(library))
    return CheckResult(
        name="fontconfig",
        status=FAILED if required else OK,
        message=(
            "the fontconfig library was not found."
            if required
            else "not installed (optional)."
        ),
        hint="Run 'desk setup' to install the font stack.",
    )


def _preference_check(paths: Paths, fonts: object, required: bool) -> CheckResult:
    """Report the font preference file that desk setup writes."""
    try:
        preference = fonts.preference_path(paths)
    except DeskError:
        preference = None
    if preference is not None and preference.is_file():
        text = preference.read_text(encoding="utf-8", errors="replace")
        if fonts.GENERATED_MARK in text:
            return CheckResult(
                name="font preference",
                status=OK,
                message=f"{preference} (written by desk setup)",
            )
        return CheckResult(
            name="font preference",
            status=WARNING,
            message=f"{preference} has content that desk setup did not write.",
            hint="Move it aside to let desk setup write the preference.",
        )
    return CheckResult(
        name="font preference",
        status=WARNING if required else OK,
        message=(
            f"not written at {preference}; run 'desk setup'."
            if required
            else "not written (optional)."
        ),
        hint="Run 'desk setup' to write the CJK font preference.",
    )


def _language_check(
    fonts: object, locale: str | None, environ: Mapping[str, str]
) -> tuple[CheckResult, str]:
    """Report the detected language and return the language to probe for."""
    tag = (
        fonts.language_tag(locale)
        if locale is not None
        else fonts.detected_language(environ=environ)
    )
    language = tag if tag in fonts.SANS_CANDIDATES else fonts.DEFAULT_LANGUAGE
    check = CheckResult(
        name="font language",
        status=OK,
        message=f"{tag or 'unknown'} (preference language: {language})",
    )
    return check, language


def _families_check(
    fonts: object,
    library: Path,
    matcher: object | None,
    language: str,
) -> CheckResult | None:
    """Report the families that fontconfig returns for the language."""
    if matcher is None and library.is_file():
        try:
            matcher = fonts.FontconfigMatcher(library)
        except OSError as error:
            return CheckResult(
                name="font families",
                status=WARNING,
                message=f"could not probe fontconfig: {error}",
            )
    if matcher is None:
        return None
    sans, serif, mono = fonts.choose_families(matcher, language)
    if sans is None:
        return CheckResult(
            name="font families",
            status=WARNING,
            message=f"no CJK sans-serif face for {language}.",
            hint="Install a CJK font for the device language.",
        )
    return CheckResult(
        name="font families",
        status=OK,
        message=(
            f"sans-serif {sans}, serif {serif or 'none'}, "
            f"monospace {mono}"
        ),
    )


def collect_font_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
    matcher: object | None = None,
    locale: str | None = None,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the font domain.

    required is False when desk doctor aggregates them: a machine that never
    ran desk setup stays healthy, because the file and the library are only
    written or installed by explicit commands. desk doctor --font fails a
    missing fontconfig library and warns about the missing preference file.
    """
    from .. import fonts

    prefix = paths.prefix
    if prefix is None:
        return (CheckResult(name="fonts", status=FAILED, message="PREFIX is not set."),)

    library = prefix / "lib" / "libfontconfig.so"
    checks = [
        _fontconfig_check(library, required),
        _preference_check(paths, fonts, required),
    ]
    language_check, language = _language_check(fonts, locale, environ)
    checks.append(language_check)
    families = _families_check(fonts, library, matcher, language)
    if families is not None:
        checks.append(families)
    return tuple(checks)
