"""Commands for the skin catalog: list, show, check, apply and reset."""

from __future__ import annotations

import textwrap
from typing import Mapping, TextIO

from . import catalog, optional, settings, skin_baseline, skin_model
from . import layout as layout_tools
from . import theme as theme_tools
from .catalog import CatalogError, CatalogSkin, Selection
from .config import DeskError, Paths
from .skin_model import SkinError


def resolve(
    paths: Paths, name: str | None, environ: Mapping[str, str] | None = None
) -> CatalogSkin:
    """Return the named skin, or the one the configuration selects."""
    if name is None:
        config = settings.load_settings(paths, allow_missing_pyyaml=True)
        selected = config.skin
        if not selected:
            raise SkinError(
                "no skin is selected; pass a name or set skin in the configuration "
                "(desk config set skin NAME)"
            )
        name = selected
    return catalog.load(paths, name, environ)


def _selection(skin: CatalogSkin, *, theme=None, icons=None, cursors=None) -> Selection:
    return catalog.resolve_selection(skin, theme=theme, icons=icons, cursors=cursors)


def _short(text: str, width: int = 52) -> str:
    """Return a printable single-line summary."""
    line = " ".join(text.split())
    if len(line) <= width:
        return line
    return line[: width - 1] + "…"


def _size_label(size: int) -> str:
    """Render a download size, or say the variants are built locally."""
    if size <= 0:
        return "local"
    return catalog.format_size(size)


def run_list(paths: Paths, *, out: TextIO, environ: Mapping[str, str] | None = None) -> int:
    """Print every skin with its variant counts and sizes."""
    ids = catalog.list_ids(paths, environ)
    if not ids:
        print("No skins are available.", file=out)
        return 0
    try:
        active = skin_model.read_applied(paths)[0]
    except SkinError:
        active = ""
    rows: list[tuple[str, str, str, str, str, str]] = []
    for skin_id in ids:
        try:
            skin = catalog.load(paths, skin_id, environ)
            selection = catalog.resolve_selection(skin)
            default_size = _size_label(catalog.group_size(skin, selection))
            all_size = _size_label(catalog.group_size(skin, all_variants=True))
            theme_count = f"{len(skin.themes)}"
            icon_count = f"{len(skin.icons)}"
            deps = ",".join(skin.requires) or "-"
            rows.append((skin_id, theme_count, icon_count, default_size, all_size, deps))
        except CatalogError as error:
            rows.append((skin_id, "invalid", "-", "-", "-", _short(str(error))))
    widths = [max(len(row[index]) for row in rows + [("ID", "TH", "IC", "DEFAULT", "ALL", "REQUIRES")]) for index in range(6)]
    header = ("ID", "TH", "IC", "DEFAULT", "ALL", "REQUIRES")
    print(
        f"{header[0]:<{widths[0]}} {header[1]:>{widths[1]}} {header[2]:>{widths[2]}} "
        f"{header[3]:>{widths[3]}} {header[4]:>{widths[4]}} {header[5]}",
        file=out,
    )
    for skin_id, themes, icons, default_size, all_size, deps in rows:
        marker = "*" if skin_id == active else " "
        print(
            f"{marker}{skin_id:<{widths[0] - 1}} {themes:>{widths[1]}} {icons:>{widths[2]}} "
            f"{default_size:>{widths[3]}} {all_size:>{widths[4]}} {deps}",
            file=out,
        )
    return 0


def run_show(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Print one skin: its components, sources and defaults."""
    skin = catalog.load(paths, name, environ)
    print(f"{skin.name} · {skin.summary}".rstrip(" ·"), file=out)
    print(f"manifest: {skin.path}", file=out)
    if skin.layout:
        print(f"layout: {skin.layout}", file=out)
    selection = catalog.resolve_selection(skin)
    print("defaults: " + " ".join(f"{key}={value}" for key, value in skin.defaults.items()), file=out)
    for kind, title in (("theme", "themes"), ("icons", "icons"), ("cursors", "cursors"), ("font", "fonts")):
        variants = skin.variants(kind)
        if not variants:
            continue
        print(f"\n{title}", file=out)
        for variant in variants:
            mark = "*" if variant is selection.theme or variant is selection.icons or variant is selection.cursors or variant is selection.font else " "
            source = variant.source
            if variant.is_url:
                source = source.split("//", 1)[-1]
            digest = f"sha256 {variant.sha256[:8]}…" if variant.sha256 else "no sha256"
            print(f"{mark} {variant.name:<24} {_short(source, 48):<48} {digest}", file=out)
    if skin.wallpaper is not None:
        wallpaper = skin.wallpaper
        print("\nwallpaper", file=out)
        digest = f"sha256 {wallpaper.sha256[:8]}…" if wallpaper.sha256 else "no sha256"
        print(f"  {wallpaper.target} · {digest}", file=out)
    if skin.requires or skin.optional:
        print("", file=out)
        if skin.requires:
            print("requires: " + " ".join(skin.requires), file=out)
        if skin.optional:
            print("optional: " + " ".join(skin.optional), file=out)
    if skin.notes:
        print("", file=out)
        print(textwrap.fill(skin.notes.strip(), width=78), file=out)
    return 0


def run_check(
    paths: Paths,
    name: str,
    *,
    theme: str | None = None,
    icons: str | None = None,
    cursors: str | None = None,
    out: TextIO,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Validate one skin and print the group a default apply would install."""
    skin = catalog.load(paths, name, environ)
    selection = _selection(skin, theme=theme, icons=icons, cursors=cursors)
    manifest = catalog.build_theme(skin, selection)
    print(f"{skin.id}: theme {selection.theme.name}, icons {selection.icons.name}", file=out)
    if selection.cursors is not None:
        print(f"  cursors {selection.cursors.name}", file=out)
    if skin.layout:
        print(f"  layout {skin.layout}", file=out)
    for asset in manifest.assets:
        digest = "verified" if asset.sha256 else "no sha256 (needs --allow-unverified)"
        print(f"  asset {asset.kind} {asset.target}: {digest}", file=out)
    print(f"  download {catalog.format_size(catalog.group_size(skin, selection))}", file=out)
    print(f"skin {skin.id} is valid.", file=out)
    return 0


def run_apply(
    paths: Paths,
    name: str | None,
    *,
    theme: str | None = None,
    icons: str | None = None,
    cursors: str | None = None,
    install_all: bool = False,
    with_optional: bool = False,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch: theme_tools.Fetcher | None = None,
    runner: theme_tools.Runner | None = None,
    matcher=None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Install the selected variants, apply the appearance, then the layout."""
    skin = catalog.load(paths, name, environ) if name else resolve(paths, None, environ)
    selection = _selection(skin, theme=theme, icons=icons, cursors=cursors)
    manifest = catalog.build_theme(skin, selection, install_all=install_all)
    layout = layout_tools.load_layout(paths, skin.layout) if skin.layout else None
    print(
        f"{skin.id}: theme {selection.theme.name}, icons {selection.icons.name}"
        + (f", cursors {selection.cursors.name}" if selection.cursors else "")
        + (" (installing every variant)" if install_all else ""),
        file=out,
    )
    baseline = None
    if not dry_run:
        try:
            environment = (
                dict(session) if session is not None else theme_tools._session_environment(display)
            )
            effective = runner or theme_tools._default_runner
            baseline = skin_baseline.prepare(
                paths,
                display,
                skin_baseline.keys_for(manifest, layout, environment, effective),
                environment,
                effective,
                out,
            )
        except DeskError as error:
            print(f"warning: could not prepare the settings baseline ({error})", file=out)
    applied = theme_tools.apply_appearance(
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
    if layout is not None and dry_run:
        print(f"would apply layout {skin.layout}", file=out)
    elif layout is not None:
        try:
            layout_tools.apply_layout(paths, layout, dry_run=dry_run, display=display, out=out)
        except DeskError as error:
            if applied is None:  # dry run: nothing was written, nothing to roll back
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
            print("rolled the appearance back to the values from before this apply", file=out)
            raise
    if not dry_run:
        if baseline is not None:
            skin_baseline.record(paths, baseline, skin.id)
        skin_model.write_applied(paths, skin.id, display)
        print(f"applied skin {skin.id}", file=out)
    if with_optional and (skin.requires or skin.optional):
        optional.install(
            paths,
            skin.requires + skin.optional,
            out=out,
            environ=environ,
            dry_run=dry_run,
        )
    return 0


def run_reset(
    paths: Paths, *, display: str = ":0", out: TextIO, runner=None, session=None
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
    skin_baseline.forget(paths)
    skin_model.clear_applied(paths)
    return 0


INIT_TEMPLATE = """\
id: {name}
name: {name}
summary: "What this skin changes."
layout: panel-stock

defaults:
  theme: MyTheme
  icons: MyIcons

themes:
  - name: MyTheme
    url: https://example.invalid/MyTheme.tar.gz
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    pick: MyTheme

icons:
  - name: MyIcons
    url: https://example.invalid/MyIcons.tar.gz
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    pick: MyIcons
"""


def run_init(
    paths: Paths, name: str, *, force: bool, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Write a starter catalog skin in the user directory."""
    if not catalog.THEME_NAME_PATTERN.fullmatch(name):
        raise SkinError(
            f"{name!r} is not a usable skin name; use letters, digits, '.', '_', '+' or '-'"
        )
    directory = catalog.catalog_directory(paths, environ) / name
    path = directory / catalog.SKIN_FILE
    if path.exists() and not force:
        raise SkinError(f"{path} already exists; pass --force to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(INIT_TEMPLATE.format(name=name), encoding="utf-8")
    except OSError as error:
        raise SkinError(f"could not write {path}: {error}") from error
    print(f"wrote {path}", file=out)
    print(f"edit the manifest and run 'desk skin apply {name}'", file=out)
    return 0
