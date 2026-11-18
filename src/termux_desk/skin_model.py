"""Skin model: one manifest that holds an appearance and a layout section.

A skin is the user-facing unit of "how the desktop looks and behaves". Its
appearance half names the GTK theme, window border, icons, cursors, wallpaper
and fonts; its layout half names the whitelisted window manager settings and
the panel size. Each half may be written inline or named after a preset that
ships with desk.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TextIO

from . import layout as layout_tools
from . import sanitize, theme_model
from .config import DeskError, Paths
from .layout import Layout, LayoutError
from .theme_model import (
    Theme,
    ThemeError,
    _assets,
    _components,
    _fonts,
    _load_yaml,
    _printable,
    _read_manifest,
    config_directory,
    presets_directory,
)

SKIN_FILE_SUFFIX = ".yaml"
SKIN_NAME_PATTERN = theme_model.THEME_NAME_PATTERN
APPEARANCE_KEYS = (
    "gtk_theme",
    "window_border",
    "icons",
    "cursors",
    "wallpaper",
    "fonts",
    "assets",
)
COMPONENT_KEYS = {
    "gtk_theme": "gtk",
    "window_border": "xfwm",
    "icons": "icons",
    "cursors": "cursors",
    "wallpaper": "wallpaper",
}
TEXT_COMPONENT_KEYS = ("gtk_theme", "window_border", "icons", "cursors", "wallpaper")
LAYOUT_SECTIONS = ("xfwm4", "panel")

INIT_TEMPLATE = """skin: {name}
description: "What this skin changes."

# Either write the keys inline, or name a preset (for example stock).
appearance:
  gtk_theme: Adwaita
  window_border: Default
  icons: Adwaita

# Either write the keys inline, or name a preset (for example compact).
layout:
  xfwm4:
    button_layout: "O|SHMC"
    title_alignment: center
  panel:
    size: 32
"""


class SkinError(DeskError):
    """A skin manifest cannot be read, resolved or applied."""


@dataclass(frozen=True)
class Skin:
    """One resolved skin: both halves are ready to be applied."""

    name: str
    path: Path
    description: str
    theme: Theme | None = None
    layout: Layout | None = None


def skins_directory(paths: Paths, environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory that holds user skin manifests."""
    return config_directory(paths, environ) / "skins"


def appearance_presets_directory() -> Path:
    """Return the appearance presets that ship with desk."""
    return presets_directory() / "appearance"


def layout_presets_directory() -> Path:
    """Return the layout presets that ship with desk."""
    return presets_directory() / "layout"


def applied_path(paths: Paths) -> Path:
    """Return the file that records the skin applied last."""
    return paths.state_dir / "skin.json"


def read_applied(paths: Paths) -> tuple[str, str]:
    """Return the applied skin name and its display, or two empty strings."""
    path = applied_path(paths)
    if not path.is_file():
        return "", ""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SkinError(f"could not read {path}: {error}") from error
    if not isinstance(record, dict):
        raise SkinError(f"{path} is not a valid skin record")
    name = record.get("active", "")
    display = record.get("display", "")
    return (name if isinstance(name, str) else ""), (display if isinstance(display, str) else "")


def write_applied(paths: Paths, name: str, display: str) -> None:
    """Record the skin that is applied now."""
    path = applied_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"active": name, "display": display}, indent=2, sort_keys=True) + "\n"
    temporary = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".skin.", suffix=".tmp", delete=False
    )
    try:
        with temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary.name, path)
    except OSError as error:
        try:
            os.unlink(temporary.name)
        except OSError:
            pass
        raise SkinError(f"could not write {path}: {error}") from error


def clear_applied(paths: Paths) -> None:
    """Forget the applied skin."""
    try:
        applied_path(paths).unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SkinError(f"could not remove {applied_path(paths)}: {error}") from error


def _check_name(name: str, kind: str) -> None:
    if not SKIN_NAME_PATTERN.fullmatch(name):
        raise SkinError(
            f"{name!r} is not a usable {kind} name; use letters, digits, '.', '_', '+' or '-'"
        )


def skin_paths(paths: Paths, environ: Mapping[str, str] | None = None) -> dict[str, Path]:
    """Return name -> manifest for the skins of this user."""
    directory = skins_directory(paths, environ)
    try:
        entries = sorted(directory.glob(f"*{SKIN_FILE_SUFFIX}"))
    except OSError:
        return {}
    return {entry.stem: entry for entry in entries if entry.is_file()}


def load_skin(paths: Paths, name: str, environ: Mapping[str, str] | None = None) -> Skin:
    """Read and validate one skin manifest."""
    _check_name(name, "skin")
    path = skin_paths(paths, environ).get(name)
    if path is None:
        raise SkinError(
            f"there is no skin named {name!r}; run 'desk skin list' to see the skins, "
            f"or 'desk skin init {name}' to create one"
        )
    text = _read_manifest(path)
    try:
        record = _load_yaml(text, path)
    except ThemeError as error:
        raise SkinError(str(error)) from error
    if not isinstance(record, dict):
        raise SkinError(f"{_printable(str(path))} must contain a mapping")
    allowed = {"skin", "description", "appearance", "layout"}
    unknown = sanitize.unknown_keys(record, allowed)
    if unknown:
        raise SkinError(f"{_printable(str(path))} has unknown keys: {', '.join(unknown)}")
    declared = record.get("skin", name)
    if not isinstance(declared, str) or declared != name:
        raise SkinError(
            f"{_printable(str(path))} declares the skin {declared!r}; "
            f"it must match the file name {name!r}"
        )
    description = record.get("description", "")
    if not isinstance(description, str):
        raise SkinError(f"{_printable(str(path))} has a non-string 'description'")
    theme = _theme(record.get("appearance"), name, path, description, paths, environ)
    layout = _layout(record.get("layout"), name, path, description, paths)
    if theme is None and layout is None:
        raise SkinError(f"{_printable(str(path))} does not change anything")
    return Skin(name=name, path=path, description=description.strip(), theme=theme, layout=layout)


def _skinned(
    paths: Paths, name: str, environ: Mapping[str, str] | None, half: str
) -> Skin | None:
    """Return the requested half of a skin when a skin has this name."""
    if name not in skin_paths(paths, environ):
        return None
    skin = load_skin(paths, name, environ)
    return skin if getattr(skin, half) is not None else None


def load_appearance(paths: Paths, name: str, environ: Mapping[str, str] | None = None) -> Skin:
    """Return the appearance of a skin, an appearance manifest, or a preset."""
    _check_name(name, "appearance")
    skinned = _skinned(paths, name, environ, "theme")
    if skinned is not None:
        return skinned
    theme = theme_model.load_theme(paths, name, environ)
    return Skin(name=name, path=theme.path, description=theme.description, theme=theme)


def load_layout_preset(paths: Paths, name: str, environ: Mapping[str, str] | None = None) -> Skin:
    """Return the layout of a skin, a layout manifest, or a preset."""
    _check_name(name, "layout")
    skinned = _skinned(paths, name, environ, "layout")
    if skinned is not None:
        return skinned
    layout = layout_tools.load_layout(paths, name)
    return Skin(name=name, path=layout.source, description=layout.description, layout=layout)


def _section(value: object, key: str, source: Path) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        raise SkinError(f"{_printable(str(source))} has a non-mapping {key!r}")
    return value


def _theme(
    value: object,
    name: str,
    source: Path,
    description: str,
    paths: Paths,
    environ: Mapping[str, str] | None,
) -> Theme | None:
    """Resolve the appearance half, which is a preset name or a mapping."""
    section = _section(value, "appearance", source)
    if section is None:
        return None
    if isinstance(section, str):
        _check_name(section, "appearance")
        try:
            return theme_model.load_theme(paths, section, environ)
        except ThemeError as error:
            message = str(error)
            if "there is no theme named" in message:
                raise SkinError(
                    f"{_printable(str(source))} names the appearance {section!r}: there is no "
                    f"appearance preset with that name; run 'desk skin appearance list'"
                ) from error
            raise SkinError(
                f"{_printable(str(source))} names the appearance {section!r}: {message}"
            ) from error
    return _theme_from_mapping(section, name, source, description)


def _theme_from_mapping(
    section: Mapping[str, object], name: str, source: Path, description: str
) -> Theme:
    unknown = sanitize.unknown_keys(section, APPEARANCE_KEYS)
    if unknown:
        raise SkinError(
            f"{_printable(str(source))} uses unknown appearance keys: {', '.join(unknown)}"
        )
    raw = {COMPONENT_KEYS[key]: section[key] for key in TEXT_COMPONENT_KEYS if key in section}
    try:
        components = _components(raw, source)
        fonts = _fonts(section.get("fonts"), source)
        assets = _assets(section.get("assets"), source)
    except ThemeError as error:
        raise SkinError(str(error)) from error
    return Theme(
        name=name,
        path=source,
        description=description,
        components=components,
        fonts=fonts,
        assets=assets,
    )


def _layout(
    value: object, name: str, source: Path, description: str, paths: Paths
) -> Layout | None:
    """Resolve the layout half, which is a preset name or a mapping."""
    section = _section(value, "layout", source)
    if section is None:
        return None
    if isinstance(section, str):
        _check_name(section, "layout")
        try:
            loaded = layout_tools.load_layout(paths, section)
        except LayoutError as error:
            message = str(error)
            if "there is no layout named" in message:
                raise SkinError(
                    f"{_printable(str(source))} names the layout {section!r}: there is no "
                    f"layout preset with that name; run 'desk skin layout list'"
                ) from error
            raise SkinError(
                f"{_printable(str(source))} names the layout {section!r}: {message}"
            ) from error
        return Layout(
            name=loaded.name,
            description=loaded.description,
            settings=loaded.settings,
            source=loaded.source,
        )
    settings: list[layout_tools._Setting] = []
    unknown = sanitize.unknown_keys(section, LAYOUT_SECTIONS)
    if unknown:
        raise SkinError(
            f"{_printable(str(source))} uses unknown layout keys: {', '.join(unknown)}"
        )
    for section_name, parser in (
        ("xfwm4", layout_tools._xfwm4_settings),
        ("panel", layout_tools._panel_settings),
    ):
        part = section.get(section_name)
        if part is None:
            continue
        if not isinstance(part, Mapping):
            raise SkinError(f"{_printable(str(source))} has a non-mapping {section_name!r}")
        try:
            settings.extend(parser(part))
        except LayoutError as error:
            raise SkinError(f"{_printable(str(source))}: {error}") from error
    if not settings:
        return None
    return Layout(
        name=name, description=description, settings=tuple(settings), source=source
    )


def _backup(path: Path, out: TextIO) -> None:
    """Keep the file that --force is about to replace."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise SkinError(f"could not back up {_printable(str(path))}: {error}") from error
    backup = path.with_name(path.name + ".bak")
    try:
        with os.fdopen(descriptor, "rb") as source:
            data = source.read()
        with open(backup, "wb") as target:
            target.write(data)
        os.chmod(backup, 0o600)
    except OSError as error:
        raise SkinError(f"could not back up {_printable(str(path))}: {error}") from error
    print(f"backed up the previous file to {_printable(str(backup))}", file=out)


def _write_template(path: Path, name: str, force: bool, out: TextIO) -> None:
    if path.exists() and not path.is_file():
        raise SkinError(
            f"{_printable(str(path))} is not a regular file; remove it before using --force"
        )
    if path.exists():
        if not force:
            raise SkinError(
                f"{_printable(str(path))} already exists; pass --force to replace it"
            )
        _backup(path, out)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(INIT_TEMPLATE.format(name=name))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise SkinError(f"could not write {_printable(str(path))}: {error}") from error


def run_skin_init(
    paths: Paths, name: str, *, force: bool = False, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Write a commented skin template for this user."""
    _check_name(name, "skin")
    path = skins_directory(paths, environ) / f"{name}{SKIN_FILE_SUFFIX}"
    if path.exists() and not force:
        raise SkinError(f"{_printable(str(path))} already exists; pass --force to replace it")
    _write_template(path, name, force, out)
    print(f"wrote {_printable(str(path))}", file=out)
    return 0


def run_skin_list(paths: Paths, *, out: TextIO, environ: Mapping[str, str] | None = None) -> int:
    """Print the skins of this user, marking the applied one."""
    skins = skin_paths(paths, environ)
    if not skins:
        print("No skins are available. Run 'desk skin init NAME' to create one.", file=out)
        return 0
    try:
        active = read_applied(paths)[0]
    except SkinError:
        active = ""
    for name in sorted(skins):
        marker = "*" if name == active else " "
        print(f"{marker} {_printable(name)}", file=out)
    return 0


def run_skin_show(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Print the manifest of one skin."""
    skin = load_skin(paths, name, environ)
    print(f"# {_printable(str(skin.path))}", file=out)
    print(skin.path.read_text(encoding="utf-8").rstrip(), file=out)
    return 0


def run_appearance_init(
    paths: Paths,
    name: str,
    *,
    force: bool,
    out: TextIO,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Write a starter appearance manifest."""
    return theme_model.run_theme_init(paths, name, force=force, out=out, environ=environ)


def run_layout_init(paths: Paths, name: str, *, force: bool, out: TextIO) -> int:
    """Write a starter layout manifest."""
    return layout_tools.run_layout_init(paths, name, force=force, out=out)


def _describe_theme(theme: theme_model.Theme, out: TextIO) -> None:
    for key in TEXT_COMPONENT_KEYS:
        value = theme.components.get(COMPONENT_KEYS[key])
        if value is not None:
            print(f"  {key} = {_printable(value)}", file=out)
    for key, value in sorted(theme.fonts.items()):
        print(f"  font {key} = {_printable(value)}", file=out)
    for asset in theme.assets:
        print(f"  asset {asset.kind} {_printable(asset.target)}", file=out)


def _describe_layout(layout: layout_tools.Layout, out: TextIO) -> None:
    for setting in layout.settings:
        print(
            f"  {_printable(setting.channel)}{_printable(setting.property)}"
            f" = {_printable(setting.value)}",
            file=out,
        )


def _describe(skin: Skin, out: TextIO) -> None:
    if skin.description:
        print(f"{_printable(skin.name)}: {_printable(skin.description)}", file=out)
    if skin.theme is not None:
        _describe_theme(skin.theme, out)
    if skin.layout is not None:
        _describe_layout(skin.layout, out)


def run_skin_check(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Validate one skin and print what it would change."""
    skin = load_skin(paths, name, environ)
    _describe(skin, out)
    print(f"skin {_printable(name)} is valid.", file=out)
    return 0


def run_appearance_show(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Print one appearance preset."""
    skin = load_appearance(paths, name, environ)
    print(f"# {_printable(str(skin.path))}", file=out)
    print(skin.path.read_text(encoding="utf-8").rstrip(), file=out)
    return 0


def run_appearance_check(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Validate one appearance preset and print what it would change."""
    skin = load_appearance(paths, name, environ)
    if skin.theme is not None:
        _describe_theme(skin.theme, out)
    print(f"appearance {_printable(name)} is valid.", file=out)
    return 0


def run_layout_preset_show(paths: Paths, name: str, *, out: TextIO) -> int:
    """Print one layout preset."""
    skin = load_layout_preset(paths, name)
    print(f"# {_printable(str(skin.path))}", file=out)
    print(skin.path.read_text(encoding="utf-8").rstrip(), file=out)
    return 0


def run_layout_preset_check(paths: Paths, name: str, *, out: TextIO) -> int:
    """Validate one layout preset and print what it would change."""
    skin = load_layout_preset(paths, name)
    if skin.layout is not None:
        _describe_layout(skin.layout, out)
    print(f"layout {_printable(name)} is valid.", file=out)
    return 0


def _preset_names(directory: Path) -> tuple[str, ...]:
    try:
        entries = sorted(directory.glob(f"*{SKIN_FILE_SUFFIX}"))
    except OSError:
        return ()
    return tuple(entry.stem for entry in entries if entry.is_file())


def run_appearance_list(paths: Paths, *, out: TextIO) -> int:
    """Print the appearance manifests of this user and the presets."""
    active = theme_model.read_state(paths).active
    user = _preset_names(theme_model.themes_directory(paths))
    presets = _preset_names(appearance_presets_directory())
    if not user and not presets:
        print("No appearance manifests are available.", file=out)
        return 0
    for name in user:
        marker = "*" if name == active else " "
        print(f"{marker} {_printable(name)}", file=out)
    for name in presets:
        marker = "*" if name == active else " "
        suffix = "" if name in user else " (preset)"
        print(f"{marker} {_printable(name)}{suffix}", file=out)
    return 0


def run_layout_preset_list(paths: Paths, *, out: TextIO) -> int:
    """Print the layout manifests of this user and the presets."""
    active = layout_tools._active_state(paths).active
    user = _preset_names(layout_tools.layouts_directory(paths))
    presets = _preset_names(layout_presets_directory())
    if not user and not presets:
        print("No layout manifests are available.", file=out)
        return 0
    for name in user:
        marker = "*" if name == active else " "
        print(f"{marker} {_printable(name)}", file=out)
    for name in presets:
        marker = "*" if name == active else " "
        suffix = "" if name in user else " (preset)"
        print(f"{marker} {_printable(name)}{suffix}", file=out)
    return 0
