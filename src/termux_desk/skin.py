"""Apply and reset a whole skin: the appearance first, then the layout."""

from __future__ import annotations

from typing import Mapping, TextIO

from . import fonts as font_tools
from . import layout as layout_tools
from . import settings, skin_model
from . import theme as theme_tools
from .config import DeskError, Paths
from .skin_model import Skin, SkinError


def resolve_skin(
    paths: Paths, name: str | None, environ: Mapping[str, str] | None = None
) -> Skin:
    """Return the named skin, or the one the configuration selects."""
    if name is None:
        selected = settings.load_settings(paths, allow_missing_pyyaml=True).skin
        if not selected:
            config = settings.load_settings(paths, allow_missing_pyyaml=True)
            if config.legacy_theme or config.legacy_layout:
                raise SkinError(
                    "this configuration still selects a theme or a layout with the 0.8.0 "
                    "keys; create a skin and select it first "
                    "(desk skin init NAME, then desk config set skin NAME, then "
                    "desk config unset theme and desk config unset layout)"
                )
            raise SkinError(
                "no skin is selected; pass a name or set skin in the configuration "
                "(desk config set skin NAME)"
            )
        name = selected
    return skin_model.load_skin(paths, name, environ)


def run_skin_apply(
    paths: Paths,
    name: str | None,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch: theme_tools.Fetcher | None = None,
    runner: theme_tools.Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Apply the appearance and the layout of one skin."""
    skin = resolve_skin(paths, name, environ)
    applied: theme_tools.AppearanceApplication | None = None
    if skin.theme is not None:
        applied = theme_tools.apply_appearance(
            paths,
            skin.theme,
            allow_unverified=allow_unverified,
            force=force,
            dry_run=dry_run,
            display=display,
            out=out,
            fetch=fetch,
            runner=runner,
            matcher=matcher,
            session=session,
        )
    if skin.layout is not None:
        try:
            layout_tools.apply_layout(
                paths, skin.layout, dry_run=dry_run, display=display, out=out
            )
        except DeskError as error:
            if applied is None:
                raise
            try:
                theme_tools.rollback_appearance(
                    paths, applied, out=out, runner=runner, session=session
                )
            except DeskError as rollback_error:
                raise SkinError(
                    f"the layout failed ({error}) and the appearance could not be "
                    f"rolled back: {rollback_error}"
                ) from error
            print(
                "rolled the appearance back to the values from before this apply",
                file=out,
            )
            raise
    if not dry_run:
        skin_model.write_applied(paths, skin.name, display)
        print(f"applied skin {skin.name}", file=out)
    return 0


def run_skin_reset(
    paths: Paths,
    *,
    display: str = ":0",
    out: TextIO,
    runner: theme_tools.Runner | None = None,
    session: Mapping[str, str] | object | None = None,
) -> int:
    """Restore the layout and then the appearance recorded by the last apply."""
    failures: list[str] = []
    try:
        layout_tools.run_layout_reset(paths, display=display, out=out)
    except DeskError as error:
        failures.append(str(error))
    try:
        theme_tools.run_theme_reset(
            paths, out=out, runner=runner, session=session, display=display
        )
    except DeskError as error:
        failures.append(str(error))
    if failures:
        raise SkinError("; ".join(failures))
    skin_model.clear_applied(paths)
    return 0


def run_appearance_apply(
    paths: Paths,
    name: str | None,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch: theme_tools.Fetcher | None = None,
    runner: theme_tools.Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Apply one appearance preset, or the appearance of the selected skin."""
    skin = (
        resolve_skin(paths, name, environ)
        if name is None
        else skin_model.load_appearance(paths, name, environ)
    )
    if skin.theme is None:
        raise SkinError(f"the skin {skin.name!r} has no appearance section")
    return theme_tools.apply_theme(
        paths,
        skin.theme,
        allow_unverified=allow_unverified,
        force=force,
        dry_run=dry_run,
        display=display,
        out=out,
        fetch=fetch,
        runner=runner,
        matcher=matcher,
        session=session,
    )


def run_layout_apply(
    paths: Paths,
    name: str | None,
    *,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Apply one layout preset, or the layout of the selected skin."""
    skin = (
        resolve_skin(paths, name, environ)
        if name is None
        else skin_model.load_layout_preset(paths, name, environ)
    )
    if skin.layout is None:
        raise SkinError(f"the skin {skin.name!r} has no layout section")
    return layout_tools.apply_layout(
        paths, skin.layout, dry_run=dry_run, display=display, out=out
    )


def run_appearance_reset(
    paths: Paths, *, display: str = ":0", out: TextIO, runner=None, session=None
) -> int:
    """Restore the appearance recorded by the last apply."""
    return theme_tools.run_theme_reset(
        paths, out=out, runner=runner, session=session, display=display
    )


def run_layout_reset(paths: Paths, *, display: str = ":0", out: TextIO) -> int:
    """Restore the layout recorded by the last apply."""
    return layout_tools.run_layout_reset(paths, display=display, out=out)
