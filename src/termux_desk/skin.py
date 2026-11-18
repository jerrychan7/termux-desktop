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
    applied_theme = False
    if skin.theme is not None:
        theme_tools.apply_theme(
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
        applied_theme = True
    if skin.layout is not None:
        try:
            layout_tools.apply_layout(
                paths, skin.layout, dry_run=dry_run, display=display, out=out
            )
        except DeskError as error:
            if dry_run or not applied_theme:
                raise
            try:
                theme_tools.run_theme_reset(
                    paths, out=out, runner=runner, session=session, display=display
                )
            except DeskError as rollback_error:
                raise SkinError(
                    "the layout failed and the appearance could not be rolled back: "
                    f"{rollback_error}"
                ) from error
            raise
    if not dry_run:
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
    """Apply only the appearance half of a named skin or preset."""
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
    """Apply only the layout half of a named skin or preset."""
    skin = (
        resolve_skin(paths, name, environ)
        if name is None
        else skin_model.load_layout_preset(paths, name)
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
