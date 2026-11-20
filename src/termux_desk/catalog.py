"""Skin catalog: one appearance family with every variant it can carry.

A catalog skin lists all themes, icons, cursors and fonts of one look and
names a default selection. Only the selected variants are turned into the
assets ``theme.apply_theme`` installs, so an apply downloads the default
group and ``--theme``/``--icons`` fetch another variant on demand.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import sanitize
from .config import DeskError, Paths
from .theme_model import (
    THEME_NAME_PATTERN,
    Theme,
    ThemeAsset,
    _load_yaml,
    _printable,
    _read_manifest,
    config_directory,
    presets_directory,
)

SKIN_FILE = "skin.yaml"
VARIANT_KINDS = ("theme", "icons", "cursors", "font")
DEFAULT_KINDS = ("theme", "icons", "cursors", "font")
MAX_SKIN_BYTES = 64 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class CatalogError(DeskError):
    """A catalog skin is missing, invalid or cannot be selected."""


@dataclass(frozen=True)
class Variant:
    """One installable variant of a skin."""

    kind: str
    name: str
    source: str
    sha256: str | None
    is_url: bool
    pick: str | None = None
    size: int = 0


@dataclass(frozen=True)
class Wallpaper:
    """The optional wallpaper of a skin."""

    source: str
    sha256: str | None
    is_url: bool
    pick: str | None = None
    target: str = "wallpaper"
    size: int = 0


@dataclass(frozen=True)
class CatalogSkin:
    """One parsed skin catalog entry."""

    id: str
    name: str
    path: Path
    summary: str = ""
    notes: str = ""
    layout: str | None = None
    defaults: Mapping[str, str] = field(default_factory=dict)
    themes: tuple[Variant, ...] = ()
    icons: tuple[Variant, ...] = ()
    cursors: tuple[Variant, ...] = ()
    fonts: tuple[Variant, ...] = ()
    wallpaper: Wallpaper | None = None
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    def variants(self, kind: str) -> tuple[Variant, ...]:
        """Return the variants of one kind."""
        return {
            "theme": self.themes,
            "icons": self.icons,
            "cursors": self.cursors,
            "font": self.fonts,
        }[kind]

    def find(self, kind: str, name: str) -> Variant | None:
        """Return one variant by kind and name."""
        return next((item for item in self.variants(kind) if item.name == name), None)


@dataclass(frozen=True)
class Selection:
    """The variants one apply should install and use."""

    theme: Variant
    icons: Variant
    cursors: Variant | None = None
    font: Variant | None = None


def catalog_directory(paths: Paths, environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory that holds user skin catalogs."""
    return config_directory(paths, environ) / "skins"


def builtin_directory() -> Path:
    """Return the shipped skin catalog directory."""
    return presets_directory() / "skins"


def skin_file(paths: Paths, skin_id: str, environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the manifest path of one skin, user entries first."""
    candidates = (
        catalog_directory(paths, environ) / skin_id / SKIN_FILE,
        builtin_directory() / skin_id / SKIN_FILE,
    )
    return next((path for path in candidates if path.is_file()), None)


def list_ids(paths: Paths, environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the ids of every catalog skin, user entries first."""
    found: list[str] = []
    for directory in (catalog_directory(paths, environ), builtin_directory()):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_dir() and (entry / SKIN_FILE).is_file() and entry.name not in found:
                found.append(entry.name)
    return tuple(found)


def load(paths: Paths, skin_id: str, environ: Mapping[str, str] | None = None) -> CatalogSkin:
    """Read and validate one catalog skin."""
    if not THEME_NAME_PATTERN.fullmatch(skin_id):
        raise CatalogError(
            f"{skin_id!r} is not a skin name; use letters, digits, '.', '_', '+' or '-'"
        )
    path = skin_file(paths, skin_id, environ)
    if path is None:
        raise CatalogError(
            f"there is no skin named {skin_id!r}; run 'desk skin list' to see the catalog"
        )
    record = _load_yaml(_read_manifest_limited(path), path)
    if not isinstance(record, dict):
        raise CatalogError(f"{path} must contain a mapping")
    allowed = {
        "id",
        "name",
        "summary",
        "notes",
        "layout",
        "defaults",
        "themes",
        "icons",
        "cursors",
        "fonts",
        "wallpaper",
        "requires",
        "optional",
    }
    unknown = sanitize.unknown_keys(record, allowed)
    if unknown:
        raise CatalogError(f"{path} uses unknown keys: {', '.join(unknown)}")
    declared = record.get("id", skin_id)
    if not isinstance(declared, str) or declared != skin_id:
        raise CatalogError(f"{path} must declare 'id: {skin_id}'")
    name = _text(record.get("name"), path, "name", default=skin_id)
    summary = _text(record.get("summary"), path, "summary", default="")
    notes = _text(record.get("notes"), path, "notes", default="")
    layout = _layout_name(record.get("layout"), path)
    themes = _variants(record.get("themes"), "theme", path, required=True)
    icons = _variants(record.get("icons"), "icons", path, required=True)
    cursors = _variants(record.get("cursors"), "cursors", path, required=False)
    fonts = _variants(record.get("fonts"), "font", path, required=False)
    wallpaper = _wallpaper(record.get("wallpaper"), path)
    defaults = _defaults(record.get("defaults"), path)
    skin = CatalogSkin(
        id=skin_id,
        name=name,
        path=path,
        summary=summary,
        notes=notes,
        layout=layout,
        defaults=defaults,
        themes=themes,
        icons=icons,
        cursors=cursors,
        fonts=fonts,
        wallpaper=wallpaper,
        requires=_names(record.get("requires"), path, "requires"),
        optional=_names(record.get("optional"), path, "optional"),
    )
    _check_defaults(skin, path)
    return skin


def _read_manifest_limited(path: Path) -> str:
    """Read one catalog manifest through the theme reader."""
    text = _read_manifest(path)
    if len(text.encode("utf-8")) > MAX_SKIN_BYTES:  # pragma: no cover - _read_manifest caps first
        raise CatalogError(f"{_printable(str(path))} is larger than {MAX_SKIN_BYTES} bytes")
    return text


def _text(value: object, source: Path, key: str, *, default: str) -> str:
    """Return one optional string field."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise CatalogError(f"{source} has a non-string {key!r}")
    return value


def _layout_name(value: object, source: Path) -> str | None:
    """Validate the optional layout name."""
    if value is None:
        return None
    if not isinstance(value, str) or not THEME_NAME_PATTERN.fullmatch(value):
        raise CatalogError(f"{source} has an invalid layout name {value!r}")
    return value


def _defaults(value: object, source: Path) -> Mapping[str, str]:
    """Validate the defaults mapping."""
    if not isinstance(value, dict):
        raise CatalogError(f"{source} has no 'defaults' mapping")
    unknown = sanitize.unknown_keys(value, DEFAULT_KINDS)
    if unknown:
        raise CatalogError(f"{source} uses unknown defaults: {', '.join(unknown)}")
    result: dict[str, str] = {}
    for kind, name in value.items():
        if not isinstance(name, str) or not THEME_NAME_PATTERN.fullmatch(name):
            raise CatalogError(f"{source} has an invalid default {kind} {name!r}")
        result[str(kind)] = name
    return result


def _names(value: object, source: Path, key: str) -> tuple[str, ...]:
    """Validate a list of component names."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise CatalogError(f"{source} has a non-list {key!r}")
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not THEME_NAME_PATTERN.fullmatch(entry):
            raise CatalogError(f"{source} has an invalid {key} entry {entry!r}")
        if entry not in result:
            result.append(entry)
    return tuple(result)


def _pick(value: object, source: Path, name: str) -> str | None:
    """Validate the optional archive pick of one variant."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{source} has an empty 'pick' for {name}")
    normalized = posixpath.normpath(value.strip())
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise CatalogError(f"{source} has a 'pick' that escapes the archive for {name}")
    return normalized


def _source(entry: Mapping[str, object], source: Path, name: str) -> tuple[str, bool]:
    """Validate url/path and return (source, is_url)."""
    url = entry.get("url")
    path = entry.get("path")
    if url is not None and path is not None:
        raise CatalogError(f"{source} gives both 'url' and 'path' for {name}")
    value = url if url is not None else path
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{source} has no 'url' or 'path' for {name}")
    return value, url is not None


def _sha(value: object, source: Path, name: str) -> str | None:
    """Validate the optional sha256 of one variant."""
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise CatalogError(f"{source} has an invalid sha256 for {name}")
    return value.lower()


def _size(value: object, source: Path, name: str) -> int:
    """Validate the optional size of one variant."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CatalogError(f"{source} has an invalid size for {name}")
    return value


def _variants(
    value: object, kind: str, source: Path, *, required: bool
) -> tuple[Variant, ...]:
    """Validate one variant list."""
    if value is None:
        if required:
            raise CatalogError(f"{source} needs at least one {kind}")
        return ()
    if not isinstance(value, list) or not value:
        raise CatalogError(f"{source} has a non-list or empty {kind!r}")
    result: list[Variant] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise CatalogError(f"{source} has a {kind} entry that is not a mapping")
        unknown = sanitize.unknown_keys(entry, {"name", "url", "path", "sha256", "pick", "size"})
        if unknown:
            raise CatalogError(f"{source} uses unknown {kind} keys: {', '.join(unknown)}")
        name = entry.get("name")
        if not isinstance(name, str) or not THEME_NAME_PATTERN.fullmatch(name):
            raise CatalogError(f"{source} has an invalid {kind} name {name!r}")
        if name in seen:
            raise CatalogError(f"{source} repeats the {kind} {name!r}")
        seen.add(name)
        asset_source, is_url = _source(entry, source, name)
        result.append(
            Variant(
                kind=kind,
                name=name,
                source=asset_source,
                sha256=_sha(entry.get("sha256"), source, name),
                is_url=is_url,
                pick=_pick(entry.get("pick"), source, name),
                size=_size(entry.get("size"), source, name),
            )
        )
    return tuple(result)


def _wallpaper(value: object, source: Path) -> Wallpaper | None:
    """Validate the optional wallpaper entry."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CatalogError(f"{source} has a non-mapping 'wallpaper'")
    unknown = sanitize.unknown_keys(value, {"url", "path", "sha256", "pick", "target", "size"})
    if unknown:
        raise CatalogError(f"{source} uses unknown wallpaper keys: {', '.join(unknown)}")
    asset_source, is_url = _source(value, source, "wallpaper")
    target = value.get("target")
    if target is not None and (not isinstance(target, str) or not target.strip()):
        raise CatalogError(f"{source} has an invalid wallpaper target {target!r}")
    pick = _pick(value.get("pick"), source, "wallpaper")
    if target is None:
        if pick is None:
            raise CatalogError(f"{source} has a wallpaper without 'pick' or 'target'")
        target = posixpath.basename(pick)
    return Wallpaper(
        source=asset_source,
        sha256=_sha(value.get("sha256"), source, "wallpaper"),
        is_url=is_url,
        pick=pick,
        target=target,
        size=_size(value.get("size"), source, "wallpaper"),
    )


def _check_defaults(skin: CatalogSkin, source: Path) -> None:
    """Make sure every default names a declared variant."""
    for kind in ("theme", "icons"):
        if kind not in skin.defaults:
            raise CatalogError(f"{source} has no default {kind}")
    for kind, name in skin.defaults.items():
        if skin.find(kind, name) is None:
            raise CatalogError(f"{source} defaults to {kind} {name!r}, which it does not list")


def resolve_selection(
    skin: CatalogSkin,
    *,
    theme: str | None = None,
    icons: str | None = None,
    cursors: str | None = None,
    font: str | None = None,
) -> Selection:
    """Return the variants named on the command line, or the defaults."""
    chosen_theme = theme or skin.defaults["theme"]
    chosen_icons = icons or skin.defaults["icons"]
    chosen_cursors = cursors if cursors is not None else skin.defaults.get("cursors")
    chosen_font = font if font is not None else skin.defaults.get("font")
    theme_variant = skin.find("theme", chosen_theme)
    if theme_variant is None:
        raise CatalogError(
            f"the skin {skin.id!r} has no theme {chosen_theme!r}; "
            + "choose one of: "
            + ", ".join(item.name for item in skin.themes)
        )
    icons_variant = skin.find("icons", chosen_icons)
    if icons_variant is None:
        raise CatalogError(
            f"the skin {skin.id!r} has no icons {chosen_icons!r}; "
            + "choose one of: "
            + ", ".join(item.name for item in skin.icons)
        )
    cursor_variant = None
    if chosen_cursors:
        cursor_variant = skin.find("cursors", chosen_cursors)
        if cursor_variant is None:
            raise CatalogError(
                f"the skin {skin.id!r} has no cursors {chosen_cursors!r}; "
                + (
                    "it lists none"
                    if not skin.cursors
                    else "choose one of: " + ", ".join(item.name for item in skin.cursors)
                )
            )
    font_variant = None
    if chosen_font:
        font_variant = skin.find("font", chosen_font)
        if font_variant is None:
            raise CatalogError(
                f"the skin {skin.id!r} has no font {chosen_font!r}; "
                + (
                    "it lists none"
                    if not skin.fonts
                    else "choose one of: " + ", ".join(item.name for item in skin.fonts)
                )
            )
    return Selection(
        theme=theme_variant, icons=icons_variant, cursors=cursor_variant, font=font_variant
    )


def build_theme(skin: CatalogSkin, selection: Selection, *, install_all: bool = False) -> Theme:
    """Return the appearance manifest that installs one selection.

    With ``install_all`` every declared variant becomes an asset, while the
    components still point at the selection.
    """
    variants: list[Variant] = []
    if install_all:
        for kind in VARIANT_KINDS:
            variants.extend(skin.variants(kind))
    else:
        variants.extend(
            item
            for item in (selection.theme, selection.icons, selection.cursors, selection.font)
            if item is not None
        )
    assets = [
        ThemeAsset(
            kind=item.kind,
            target=item.name,
            source=item.source,
            sha256=item.sha256,
            is_url=item.is_url,
            pick=item.pick,
        )
        for item in variants
    ]
    if skin.wallpaper is not None:
        assets.append(
            ThemeAsset(
                kind="wallpaper",
                target=skin.wallpaper.target,
                source=skin.wallpaper.source,
                sha256=skin.wallpaper.sha256,
                is_url=skin.wallpaper.is_url,
                pick=skin.wallpaper.pick,
            )
        )
    components = {
        "gtk": selection.theme.name,
        "xfwm": selection.theme.name,
        "icons": selection.icons.name,
    }
    if selection.cursors is not None:
        components["cursors"] = selection.cursors.name
    fonts: dict[str, str] = {}
    if selection.font is not None:
        fonts["default"] = selection.font.name
    return Theme(
        name=skin.id,
        path=skin.path,
        description=skin.summary,
        components=components,
        fonts=fonts,
        assets=tuple(assets),
    )


def group_size(
    skin: CatalogSkin, selection: Selection | None = None, *, all_variants: bool = False
) -> int:
    """Return the declared download size of one install group, in bytes."""
    if all_variants:
        total = sum(item.size for kind in VARIANT_KINDS for item in skin.variants(kind))
    else:
        assert selection is not None
        total = sum(
            item.size
            for item in (selection.theme, selection.icons, selection.cursors, selection.font)
            if item is not None
        )
    if skin.wallpaper is not None:
        total += skin.wallpaper.size
    return total


def format_size(size: int) -> str:
    """Render a byte count for the catalog table."""
    if size <= 0:
        return "-"
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"  # pragma: no cover - unreachable
