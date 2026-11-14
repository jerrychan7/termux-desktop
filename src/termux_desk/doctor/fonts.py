"""Read-only checks for the font preference."""

from __future__ import annotations

from typing import Mapping

from ..config import DeskError, Paths
from .base import FAILED, OK, WARNING, CheckResult


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
    checks: list[CheckResult] = []
    library = prefix / "lib" / "libfontconfig.so"
    if library.is_file():
        checks.append(CheckResult(name="fontconfig", status=OK, message=str(library)))
    else:
        checks.append(
            CheckResult(
                name="fontconfig",
                status=FAILED if required else OK,
                message=(
                    "the fontconfig library was not found."
                    if required
                    else "not installed (optional)."
                ),
                hint="Run 'desk setup' to install the font stack.",
            )
        )

    try:
        preference = fonts.preference_path(paths)
    except DeskError:
        preference = None
    if preference is not None and preference.is_file():
        text = preference.read_text(encoding="utf-8", errors="replace")
        if fonts.GENERATED_MARK in text:
            checks.append(
                CheckResult(
                    name="font preference",
                    status=OK,
                    message=f"{preference} (written by desk setup)",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="font preference",
                    status=WARNING,
                    message=f"{preference} has content that desk setup did not write.",
                    hint="Move it aside to let desk setup write the preference.",
                )
            )
    else:
        checks.append(
            CheckResult(
                name="font preference",
                status=WARNING if required else OK,
                message=(
                    f"not written at {preference}; run 'desk setup'."
                    if required
                    else "not written (optional)."
                ),
                hint="Run 'desk setup' to write the CJK font preference.",
            )
        )

    tag = (
        fonts.language_tag(locale)
        if locale is not None
        else fonts.detected_language(environ=environ)
    )
    language = tag if tag in fonts.SANS_CANDIDATES else fonts.DEFAULT_LANGUAGE
    checks.append(
        CheckResult(
            name="font language",
            status=OK,
            message=f"{tag or 'unknown'} (preference language: {language})",
        )
    )

    if matcher is None and library.is_file():
        try:
            matcher = fonts.FontconfigMatcher(library)
        except OSError as error:
            checks.append(
                CheckResult(
                    name="font families",
                    status=WARNING,
                    message=f"could not probe fontconfig: {error}",
                )
            )
    if matcher is not None:
        sans, serif, mono = fonts.choose_families(matcher, language)
        if sans is None:
            checks.append(
                CheckResult(
                    name="font families",
                    status=WARNING,
                    message=f"no CJK sans-serif face for {language}.",
                    hint="Install a CJK font for the device language.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="font families",
                    status=OK,
                    message=(
                        f"sans-serif {sans}, serif {serif or 'none'}, "
                        f"monospace {mono}"
                    ),
                )
            )
    return tuple(checks)
