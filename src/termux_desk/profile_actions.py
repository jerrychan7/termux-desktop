"""Apply the theme and layout selected by one profile as a single action."""

from __future__ import annotations

from typing import Mapping, TextIO

from . import layout as layout_tools
from . import settings
from . import theme as theme_tools
from .config import DeskError, Paths


def run_profile_apply(
    paths: Paths,
    *,
    display: str = ":0",
    dry_run: bool = False,
    allow_unverified: bool = False,
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch=None,
    runner=None,
    matcher=None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Apply the selected theme first, then the selected layout.

    If the layout fails after the theme was applied, the theme is reset again
    so the combined action either completes or leaves the previous appearance.
    """
    config = settings.load_settings(paths, environ=environ)
    theme_name = config.theme or None
    layout_name = config.layout or None
    if theme_name is None and layout_name is None:
        print("The profile selects no theme or layout; nothing to apply.", file=out)
        return 0
    if dry_run:
        return _dry_run(paths, theme_name, layout_name, environ, out)
    applied_theme = False
    if theme_name is not None:
        theme_tools.run_theme_apply(
            paths,
            theme_name,
            allow_unverified=allow_unverified,
            display=display,
            out=out,
            environ=environ,
            fetch=fetch,
            runner=runner,
            matcher=matcher,
            session=session,
        )
        applied_theme = True
    if layout_name is not None:
        try:
            layout_tools.run_layout_apply(
                paths, layout_name, display=display, out=out
            )
        except DeskError:
            if applied_theme:
                try:
                    theme_tools.run_theme_reset(
                        paths,
                        display=display,
                        out=out,
                        environ=environ,
                        runner=runner,
                        session=session,
                    )
                except DeskError as reset_error:
                    raise DeskError(
                        "the layout failed and the theme could not be rolled back: "
                        f"{reset_error}"
                    ) from None
            raise
    print(_applied_message(theme_name, layout_name), file=out)
    return 0


def run_profile_reset(
    paths: Paths,
    *,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    runner=None,
    session: Mapping[str, str] | None = None,
    layout_session=None,
) -> int:
    """Reset the layout, then the theme; report every layer that failed."""
    failures: list[str] = []
    try:
        layout_tools.run_layout_reset(paths, display=display, out=out, session=layout_session)
    except DeskError as error:
        failures.append(str(error))
    try:
        theme_tools.run_theme_reset(
            paths, display=display, out=out, environ=environ, runner=runner, session=session
        )
    except DeskError as error:
        failures.append(str(error))
    if failures:
        raise DeskError("; ".join(failures))
    print("The profile's theme and layout were reset.", file=out)
    return 0


def _dry_run(paths, theme_name, layout_name, environ, out) -> int:
    if theme_name is not None:
        theme_tools.load_theme(paths, theme_name, environ)
    if layout_name is not None:
        layout_tools.load_layout(paths, layout_name)
    parts = []
    if theme_name is not None:
        parts.append(f"theme {theme_name!r}")
    if layout_name is not None:
        parts.append(f"layout {layout_name!r}")
    print("Would apply " + " and ".join(parts) + ".", file=out)
    return 0


def _applied_message(theme_name, layout_name) -> str:
    if theme_name is not None and layout_name is not None:
        return f"Applied the profile's theme {theme_name!r} and layout {layout_name!r}."
    if theme_name is not None:
        return f"Applied the profile's theme {theme_name!r}."
    return f"Applied the profile's layout {layout_name!r}."
