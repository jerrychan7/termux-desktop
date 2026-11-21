"""Named layout presets for the Xfce window manager, panel and desktop.

A manifest changes a whitelist of settings, snapshots the previous values and
can restore them; it never rewrites configuration directories.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, TextIO

from . import layout_shortcuts, panel, sanitize
from .config import DeskError, Paths
from .layout_errors import LayoutError
from .layout_manifest import read_manifest
from .theme_model import _load_yaml, _printable, config_directory
from .theme_model import state_path as theme_state_path
from .theme_session import (
    _default_runner,
    _get_property,
    _reload,
    _remove_property,
    _session_environment,
    _set_property,
)

LAYOUT_FILE_SUFFIX = ".yaml"
LAYOUT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
LAYOUT_STATE_VERSION = 2
LEGACY_STATE_VERSIONS = (1,)
PANEL_CHANNEL = "xfce4-panel"
DESKTOP_CHANNEL = "xfce4-desktop"
XSETTINGS_CHANNEL = "xsettings"
DESKTOP_BOOLEAN_KEYS = {
    "show_filesystem": "/desktop-icons/file-icons/show-filesystem",
}
PANEL_SIZE_PROPERTY = "/panels/panel-1/size"
XFWM4_CHANNEL = "xfwm4"

BUTTON_LAYOUT_PATTERN = re.compile(r"^[OSHMC]*(?:\|[OSHMC]*)?$")
TITLE_ALIGNMENTS = ("left", "center", "right")
INTEGER_KEYS = ("workspace_count", "inactive_opacity")
WORKSPACE_COUNT_RANGE = (1, 36)
OPACITY_RANGE = (0, 100)
BOOLEAN_KEYS = (
    "use_compositing",
    "snap_to_border",
    "snap_to_windows",
    "wrap_windows",
    "tile_on_move",
    "box_move",
    "box_resize",
    "show_frame_shadow",
    "show_popup_shadow",
    "borderless_maximize",
    "full_width_title",
    "show_app_icon",
)
PLACEMENT_MODES = ("center", "mouse", "under-mouse")
THEME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

INIT_TEMPLATE = """layout: {name}
description: "What this layout changes."

xfwm4:
  button_layout: "O|SHMC"
  title_alignment: center
  use_compositing: true

panel:
  size: 32
"""


@dataclass(frozen=True)
class _Setting:
    channel: str
    property: str
    value: str | tuple[str, ...]
    key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", f"{self.channel}:{self.property}")


@dataclass(frozen=True)
class Layout:
    name: str
    description: str
    settings: tuple[_Setting, ...]
    source: Path
    panels: tuple[panel.Panel, ...] = ()


@dataclass
class LayoutState:
    active: str = ""
    display: str = ":0"
    values: dict[str, str | None] = field(default_factory=dict)
    # None means the applied layout did not replace the panel configuration.
    panel: tuple[panel.SnapshotEntry, ...] | None = None


class LayoutSession(Protocol):
    """The xfconf operations a layout apply or reset needs."""

    def get(self, channel: str, name: str) -> str | None: ...

    def set(self, channel: str, name: str, value: str | tuple[str, ...]) -> None: ...

    def remove(self, channel: str, name: str) -> None: ...

    def reload(self, out: TextIO) -> None: ...


class XfconfSession:
    """Talk to the xfconf daemon of one running session."""

    def __init__(self, display: str) -> None:
        self.environment = _session_environment(display)

    def get(self, channel: str, name: str) -> str | None:
        return _get_property(channel, name, self.environment, _default_runner)

    def set(self, channel: str, name: str, value: str | tuple[str, ...]) -> None:
        _set_property(channel, name, value, self.environment, _default_runner)

    def remove(self, channel: str, name: str) -> None:
        _remove_property(channel, name, self.environment, _default_runner)

    def reload(self, out: TextIO) -> None:
        _reload(self.environment, out)


def layouts_directory(paths: Paths, environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory that holds user layout manifests."""
    return config_directory(paths, environ) / "skins" / "layout"


def preset_layouts_directory() -> Path:
    """Return the layout presets that ship with desk."""
    return Path(__file__).resolve().parent / "presets" / "layout"


def state_path(paths: Paths) -> Path:
    """Return the file that records the applied layout."""
    return theme_state_path(paths).with_name("layout.json")


def _read_state(paths: Paths) -> LayoutState:
    path = state_path(paths)
    if not path.exists():
        return LayoutState()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LayoutError(f"could not read {_printable(str(path))}: {error}") from error
    try:
        data = json.loads(text)
    except ValueError as error:
        raise LayoutError(f"{_printable(str(path))} is not valid JSON: {error}") from error
    if not isinstance(data, dict) or data.get("version") not in (LAYOUT_STATE_VERSION, *LEGACY_STATE_VERSIONS):
        raise LayoutError(f"{_printable(str(path))} does not contain a layout state this desk understands")
    active = data.get("active")
    display = data.get("display")
    values = data.get("values")
    if not isinstance(active, str) or not isinstance(display, str) or not isinstance(values, dict):
        return LayoutState()
    clean: dict[str, str | None] = {}
    for key, value in values.items():
        if isinstance(key, str) and (value is None or isinstance(value, str)):
            clean[key] = value
    recorded = data.get("panel")
    if recorded is None:
        snapshot = None
    else:
        try:
            snapshot = tuple(panel.snapshot_from_json(recorded).entries)
        except panel.PanelError as error:
            raise LayoutError(f"{_printable(str(path))}: {error}") from error
    return LayoutState(active=active, display=display, values=clean, panel=snapshot)


def _write_state(paths: Paths, state: LayoutState) -> None:
    path = state_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LAYOUT_STATE_VERSION,
        "active": state.active,
        "display": state.display,
        "values": state.values,
        "panel": (
            None
            if state.panel is None
            else panel.snapshot_to_json(panel.PanelSnapshot(entries=state.panel))
        ),
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".layout-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise LayoutError(f"could not write {path}: {error}") from error


def _remove_state(paths: Paths) -> None:
    try:
        state_path(paths).unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise LayoutError(f"could not remove {state_path(paths)}: {error}") from error


def _layout_paths(paths: Paths) -> dict[str, tuple[Path, bool]]:
    """Return name -> (path, is_preset); the user file wins over a preset."""
    found: dict[str, tuple[Path, bool]] = {}
    for directory, preset in ((preset_layouts_directory(), True), (layouts_directory(paths), False)):
        try:
            entries = sorted(directory.glob(f"*{LAYOUT_FILE_SUFFIX}"))
        except OSError:
            continue
        for entry in entries:
            found[entry.stem] = (entry, preset)
    return found


def list_layouts(paths: Paths) -> dict[str, tuple[Path, bool]]:
    """Return the available layouts."""
    return _layout_paths(paths)


def _boolean(value: object) -> str:
    if not isinstance(value, bool):
        raise LayoutError("expected true or false")
    return "true" if value else "false"


def _button_layout(value: object) -> str:
    if not isinstance(value, str) or not BUTTON_LAYOUT_PATTERN.fullmatch(value):
        raise LayoutError("expected letters from OSHMC with at most one '|'")
    return value


def _placement_mode(value: object) -> str:
    if not isinstance(value, str) or value not in PLACEMENT_MODES:
        raise LayoutError("expected center, mouse or under-mouse")
    return value


def _theme_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64 or not THEME_NAME_PATTERN.fullmatch(value):
        raise LayoutError("expected a theme directory name")
    return value


def _title_alignment(value: object) -> str:
    if not isinstance(value, str) or value not in TITLE_ALIGNMENTS:
        raise LayoutError("expected left, center or right")
    return value


def _panel_size(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= 128:
        raise LayoutError("expected an integer between 16 and 128")
    return str(value)


def _workspace_count(value: object) -> str:
    low, high = WORKSPACE_COUNT_RANGE
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LayoutError(f"expected an integer between {low} and {high}")
    return str(value)


def _opacity(value: object) -> str:
    low, high = OPACITY_RANGE
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LayoutError(f"expected an integer between {low} and {high}")
    return str(value)


def _workspace_names(value: object) -> tuple[str, ...]:
    low, high = WORKSPACE_COUNT_RANGE
    if not isinstance(value, (list, tuple)) or not low <= len(value) <= high:
        raise LayoutError(f"expected a list of {low} to {high} names")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 64 or "\0" in item:
            raise LayoutError("expected non-empty names of at most 64 characters")
        names.append(item)
    return tuple(names)


def _xfwm4_settings(section: Mapping[str, object]) -> tuple[_Setting, ...]:
    settings: list[_Setting] = []
    for key, value in section.items():
        if key == "button_layout":
            normalized = _button_layout(value)
        elif key == "title_alignment":
            normalized = _title_alignment(value)
        elif key == "workspace_count":
            normalized = _workspace_count(value)
        elif key == "placement_mode":
            normalized = _placement_mode(value)
        elif key == "theme":
            normalized = _theme_name(value)
        elif key == "inactive_opacity":
            normalized = _opacity(value)
        elif key == "workspace_names":
            normalized = _workspace_names(value)
        elif key in BOOLEAN_KEYS:
            normalized = _boolean(value)
        else:
            raise LayoutError(f"unknown xfwm4 setting {key!r}")
        settings.append(
            _Setting(channel=XFWM4_CHANNEL, property=f"/general/{key}", value=normalized)
        )
    return tuple(settings)


def _panel_settings(section: Mapping[str, object]) -> tuple[_Setting, ...]:
    settings: list[_Setting] = []
    for key, value in section.items():
        if key != "size":
            raise LayoutError(f"unknown panel setting {key!r}")
        settings.append(
            _Setting(channel=PANEL_CHANNEL, property=PANEL_SIZE_PROPERTY, value=_panel_size(value))
        )
    return tuple(settings)


DECORATION_LAYOUT_PATTERN = re.compile(r"^:?[A-Za-z]+(?:,[A-Za-z]+)*$")


def _decoration_layout(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64 or not DECORATION_LAYOUT_PATTERN.fullmatch(value):
        raise LayoutError("expected a decoration layout such as ':minimize,maximize,close'")
    return value


def _xsettings_settings(section: Mapping[str, object]) -> tuple[_Setting, ...]:
    settings: list[_Setting] = []
    for key, value in section.items():
        if key == "decoration_layout":
            settings.append(
                _Setting(
                    channel=XSETTINGS_CHANNEL,
                    property="/Gtk/DecorationLayout",
                    value=_decoration_layout(value),
                )
            )
        elif key == "sync_themes":
            settings.append(
                _Setting(
                    channel=XSETTINGS_CHANNEL,
                    property="/Xfce/SyncThemes",
                    value=_boolean(value),
                )
            )
        else:
            raise LayoutError(f"unknown xsettings setting {key!r}")
    return tuple(settings)


def _shortcut_settings(section: Mapping[str, object], group: str) -> tuple[_Setting, ...]:
    try:
        pairs = layout_shortcuts.entries(section, group)
    except layout_shortcuts.ShortcutError as error:
        raise LayoutError(str(error)) from error
    return tuple(_Setting(channel=layout_shortcuts.CHANNEL, property=path, value=value) for path, value in pairs)


def _desktop_settings(section: Mapping[str, object]) -> tuple[_Setting, ...]:
    settings: list[_Setting] = []
    for key, value in section.items():
        property_name = DESKTOP_BOOLEAN_KEYS.get(key)
        if property_name is None:
            raise LayoutError(f"unknown desktop setting {key!r}")
        settings.append(
            _Setting(channel=DESKTOP_CHANNEL, property=property_name, value=_boolean(value))
        )
    return tuple(settings)


def _settings_from(document: Mapping[str, object]) -> list[_Setting]:
    """Collect the settings every declared section of a layout contributes."""
    settings: list[_Setting] = []
    xfwm4 = document.get("xfwm4")
    if xfwm4 is not None:
        if not isinstance(xfwm4, dict):
            raise LayoutError("xfwm4 must be a mapping")
        settings.extend(_xfwm4_settings(xfwm4))
    xsettings = document.get("xsettings")
    if xsettings is not None:
        if not isinstance(xsettings, dict):
            raise LayoutError("xsettings must be a mapping")
        settings.extend(_xsettings_settings(xsettings))
    desktop = document.get("desktop")
    if desktop is not None:
        if not isinstance(desktop, dict):
            raise LayoutError("desktop must be a mapping")
        settings.extend(_desktop_settings(desktop))
    for group in layout_shortcuts.GROUPS:
        section = document.get(group)
        if section is not None:
            if not isinstance(section, dict):
                raise LayoutError(f"{group} must be a mapping of key to command")
            settings.extend(_shortcut_settings(section, group))
    return settings


def _parse_layout(name: str, source: Path) -> Layout:
    if not LAYOUT_NAME_PATTERN.fullmatch(name):
        raise LayoutError(f"invalid layout name {name!r}")
    text = read_manifest(source)
    try:
        data = _load_yaml(text, source)
    except Exception as error:  # ThemeError or YAML errors
        raise LayoutError(str(error)) from error
    if not isinstance(data, dict):
        raise LayoutError(f"{source} must contain a mapping")
    allowed = {
        "layout",
        "description",
        "xfwm4",
        "xsettings",
        "desktop",
        "shortcuts",
        "window_shortcuts",
        "panel",
        "panels",
    }
    unknown = sanitize.unknown_keys(data, allowed)
    if unknown:
        raise LayoutError(f"{source} has unknown keys: {', '.join(unknown)}")
    declared = data.get("layout")
    if declared != name:
        raise LayoutError(f"{source} declares layout {declared!r}, expected {name!r}")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise LayoutError("description must be a string")

    settings = _settings_from(data)
    legacy = data.get("panel")
    if "panels" not in data and isinstance(legacy, dict) and "plugins" not in legacy:
        settings.extend(_panel_settings(legacy))
    panels = _parse_panels(data)
    if not settings and not panels:
        raise LayoutError("the layout does not change anything")
    return Layout(
        name=name,
        description=description.strip(),
        settings=tuple(settings),
        source=source,
        panels=panels,
    )


def _parse_panels(document: Mapping[str, object]) -> tuple[panel.Panel, ...]:
    """Accept either the legacy panel size or a whole panel description."""
    if "panels" in document:
        try:
            return panel.parse_panels(document)
        except panel.PanelError as error:
            raise LayoutError(str(error)) from error
    section = document.get("panel")
    if section is None:
        return ()
    if not isinstance(section, dict):
        raise LayoutError("panel must be a mapping")
    if "plugins" not in section:
        return ()
    try:
        return panel.parse_panels(document)
    except panel.PanelError as error:
        raise LayoutError(str(error)) from error


def load_layout(paths: Paths, name: str) -> Layout:
    """Return one layout manifest, user file first, then the presets."""
    found = _layout_paths(paths).get(name)
    if found is None:
        raise LayoutError(
            f"there is no layout named {name!r}; "
            f"run 'desk skin layout init {name}' to create one"
        )
    return _parse_layout(name, found[0])


def _active_state(paths: Paths) -> LayoutState:
    return _read_state(paths)


def run_layout_list(paths: Paths, *, out: TextIO) -> int:
    """Print the available layouts and mark the applied one."""
    layouts = _layout_paths(paths)
    if not layouts:
        print("No layouts are available.", file=out)
        return 0
    active = _active_state(paths).active
    for name, (_path, preset) in sorted(layouts.items()):
        marker = "*" if name == active else " "
        suffix = " (preset)" if preset else ""
        print(f"{marker} {_printable(name)}{suffix}", file=out)
    return 0


def run_layout_show(paths: Paths, name: str, *, out: TextIO) -> int:
    """Print one layout manifest."""
    layout = load_layout(paths, name)
    print(f"# {layout.source}", file=out)
    print(layout.source.read_text(encoding="utf-8").rstrip(), file=out)
    return 0


def _plugin_directory(paths: Paths) -> Path:
    return paths.prefix / "lib" / "xfce4" / "panel" / "plugins"


def _require_plugins(layout: Layout, paths: Paths) -> None:
    missing = panel.missing_plugins(layout.panels, _plugin_directory(paths))
    if missing:
        names = ", ".join(missing)
        raise LayoutError(
            f"this layout needs panel plugins that are not installed: {names}; "
            f"run '{panel.install_hint(missing)}'"
        )


def run_layout_check(paths: Paths, name: str, *, out: TextIO) -> int:
    """Validate one layout and print the settings it would change."""
    layout = load_layout(paths, name)
    if layout.description:
        print(f"{name}: {_printable(layout.description)}", file=out)
    for setting in layout.settings:
        print(
            f"  {_printable(setting.channel)}{_printable(setting.property)} = {_printable(setting.value)}",
            file=out,
        )
    for index, panel_spec in enumerate(layout.panels, start=1):
        types = ", ".join(plugin.type for plugin in panel_spec.plugins)
        print(
            f"  panel {index}: {panel_spec.position}, size {panel_spec.size}, "
            f"plugins: {_printable(types)}",
            file=out,
        )
    missing = panel.missing_plugins(layout.panels, _plugin_directory(paths))
    if missing:
        print(
            f"layout {name} needs missing panel plugins: {', '.join(missing)}; "
            f"run '{panel.install_hint(missing)}'",
            file=out,
        )
        return 1
    print(f"layout {name} is valid.", file=out)
    return 0


def _write_text_atomic(path: Path, text: str, error_type: type[DeskError]) -> None:
    """Write a text file through a temporary file and a rename."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise error_type(f"could not write {_printable(str(path))}: {error}") from error


def _backup_file(path: Path, out: TextIO, error_type: type[DeskError]) -> None:
    """Keep a copy of a file that --force is about to replace."""
    backup = path.with_name(path.name + ".bak")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as source:
            data = source.read()
        _write_text_atomic(backup, data.decode("utf-8", "surrogateescape"), error_type)
    except OSError as error:
        raise error_type(f"could not back up {_printable(str(path))}: {error}") from error
    print(f"backed up the previous file to {_printable(str(backup))}", file=out)


def run_layout_init(paths: Paths, name: str, *, force: bool, out: TextIO) -> int:
    """Write a starter layout manifest."""
    if not LAYOUT_NAME_PATTERN.fullmatch(name):
        raise LayoutError(
            f"{_printable(name)} is not a usable layout name; "
            "use letters, digits, '.', '_', '+' or '-'"
        )
    path = layouts_directory(paths) / f"{name}{LAYOUT_FILE_SUFFIX}"
    if path.exists() or path.is_symlink():
        if not force:
            raise LayoutError(f"{_printable(str(path))} already exists; pass --force to replace it")
        if path.is_symlink() or not path.is_file():
            raise LayoutError(
                f"{_printable(str(path))} is not a regular file; remove it before using --force"
            )
        _backup_file(path, out, LayoutError)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, INIT_TEMPLATE.format(name=name), LayoutError)
    print(f"wrote {_printable(str(path))}", file=out)
    return 0


def _session(session: LayoutSession | None, display: str) -> LayoutSession:
    return session if session is not None else XfconfSession(display)


def _text(value: str | tuple[str, ...]) -> str:
    """Render a setting value for messages and dry runs."""
    return value if isinstance(value, str) else ", ".join(value)


def _describe(setting: _Setting, old: str | None) -> str:
    before = "unset" if old is None else _printable(old)
    return f"{setting.channel}{setting.property}: {before} -> {_printable(_text(setting.value))}"


def run_layout_apply(
    paths: Paths,
    name: str,
    *,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    session: LayoutSession | None = None,
) -> int:
    """Apply the layout with this name."""
    layout = load_layout(paths, name)
    return apply_layout(paths, layout, dry_run=dry_run, display=display, out=out, session=session)


def apply_layout(
    paths: Paths,
    layout: Layout,
    *,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    session: LayoutSession | None = None,
) -> int:
    """Apply one parsed layout and remember the values it replaces."""
    previous = _read_state(paths)
    environment: Mapping[str, str] | None = None
    if layout.panels:
        _require_plugins(layout, paths)
        environment = _session_environment(display)
    if dry_run:
        for setting in layout.settings:
            print(
                f"would set {setting.channel}{setting.property}: {sanitize.printable(_text(setting.value))}",
                file=out,
            )
        for index, panel_spec in enumerate(layout.panels, start=1):
            print(
                f"would replace panel {index}: {panel_spec.position}, "
                f"{len(panel_spec.plugins)} plugins",
                file=out,
            )
        return 0
    active = _session(session, display)
    old = {setting.key: active.get(setting.channel, setting.property) for setting in layout.settings}
    for setting in layout.settings:
        print(f"set {_describe(setting, old[setting.key])}", file=out)

    written: list[_Setting] = []
    applied_panel: panel.PanelSnapshot | None = None
    try:
        for setting in layout.settings:
            active.set(setting.channel, setting.property, setting.value)
            written.append(setting)
        if layout.panels:
            applied_panel = panel.apply_panels(
                layout.panels, environment or {}, paths.home, _default_runner
            )
            print(f"replaced the panel configuration for {display}", file=out)
        # 同一份布局重复 apply 时保留第一次记录的原值，reset 才能回到原点；
        # 换到别的布局时，记录这次 apply 之前的值。
        same_layout = previous.active == layout.name
        values = {**old, **dict(previous.values)} if same_layout else dict(old)
        recorded_panel = previous.panel if same_layout else None
        if recorded_panel is None and applied_panel is not None:
            recorded_panel = applied_panel.entries
        _write_state(
            paths,
            LayoutState(active=layout.name, display=display, values=values, panel=recorded_panel),
        )
    except Exception as error:
        for setting in reversed(written):
            try:
                if old[setting.key] is None:
                    active.remove(setting.channel, setting.property)
                else:
                    active.set(setting.channel, setting.property, old[setting.key] or "")
            except Exception:  # keep the original failure
                pass
        if applied_panel is not None and environment is not None:
            try:
                panel.restore_snapshot(applied_panel, environment, _default_runner)
            except Exception:  # keep the original failure
                pass
        if isinstance(error, LayoutError):
            raise
        raise LayoutError(f"could not apply layout {layout.name}: {error}") from error

    try:
        active.reload(out)
    except OSError as error:
        print(f"warning: could not reload the window manager ({error})", file=out)
    print(f"applied layout {layout.name}", file=out)
    return 0


def run_layout_reset(
    paths: Paths, *, display: str = ":0", out: TextIO, session: LayoutSession | None = None
) -> int:
    """Restore the values recorded by the last apply."""
    state = _read_state(paths)
    if not state.active:
        print("No layout is applied.", file=out)
        return 0
    if state.display != display:
        raise LayoutError(
            f"the layout was applied on {state.display}; pass --display {state.display} to reset it there"
        )
    active = _session(session, display)
    failures: list[str] = []
    for key, value in sorted(state.values.items()):
        channel, _, prop = key.partition(":")
        try:
            if value is None:
                active.remove(channel, prop)
            else:
                active.set(channel, prop, value)
        except Exception as error:  # one bad property should not hide the rest
            failures.append(f"{channel}{prop}: {error}")
    if failures:
        raise LayoutError("could not restore every setting:\n  " + "\n  ".join(failures))
    if state.panel is not None:
        environment = _session_environment(display)
        try:
            panel.restore_snapshot(
                panel.PanelSnapshot(entries=state.panel), environment, _default_runner
            )
        except Exception as error:
            raise LayoutError(f"could not restore the panel configuration: {error}") from error
        print(f"restored the panel configuration for {display}", file=out)
    _remove_state(paths)
    try:
        active.reload(out)
    except OSError as error:
        print(f"warning: could not reload the window manager ({error})", file=out)
    print(f"reset the layout {state.active}", file=out)
    return 0
