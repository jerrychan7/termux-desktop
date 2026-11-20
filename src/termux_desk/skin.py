"""Apply and reset a whole skin: the appearance first, then the layout."""

from __future__ import annotations

from typing import Mapping, TextIO

from . import fonts as font_tools
from . import layout as layout_tools
from . import skin_model
from . import theme as theme_tools
from .config import Paths
from .skin_model import SkinError


def _selected_catalog_skin(paths: Paths, environ: Mapping[str, str] | None):
    """Return the catalog skin the configuration selects."""
    from . import skin_catalog

    return skin_catalog.resolve(paths, None, environ)


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
    if name is None:
        from . import catalog

        skin = _selected_catalog_skin(paths, environ)
        manifest = catalog.build_theme(skin, catalog.resolve_selection(skin))
    else:
        manifest = skin_model.load_appearance(paths, name, environ).theme
        if manifest is None:
            raise SkinError(f"the appearance {name!r} has no theme section")
    return theme_tools.apply_theme(
        paths,
        manifest,
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
    if name is None:
        skin = _selected_catalog_skin(paths, environ)
        if not skin.layout:
            raise SkinError(f"the skin {skin.id!r} has no layout")
        layout = layout_tools.load_layout(paths, skin.layout)
    else:
        parsed = skin_model.load_layout_preset(paths, name, environ)
        if parsed.layout is None:
            raise SkinError(f"the layout {name!r} has no panels or settings")
        layout = parsed.layout
    return layout_tools.apply_layout(
        paths, layout, dry_run=dry_run, display=display, out=out
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
