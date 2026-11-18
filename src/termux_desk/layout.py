"""Named layout presets for the Xfce window manager and panel.

A layout manifest is deliberately small: it changes a whitelist of xfwm4
settings and the panel size, snapshots the previous values and can restore
them. It never rewrites configuration directories.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, TextIO

from .config import DeskError, Paths
from .theme_model import (
    _load_yaml,
    _printable,
    config_directory,
)
from .theme_model import (
    state_path as theme_state_path,
)
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
LAYOUT_STATE_VERSION = 1
PANEL_CHANNEL = "xfce4-panel"
PANEL_SIZE_PROPERTY = "/panels/panel-1/size"
XFWM4_CHANNEL = "xfwm4"

BUTTON_LAYOUT_PATTERN = re.compile(r"^[OSHMC]*(?:\|[OSHMC]*)?$")
TITLE_ALIGNMENTS = ("left", "center", "right")
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
)

INIT_TEMPLATE = """layout: {name}
description: "What this layout changes."

xfwm4:
  button_layout: "O|SHMC"
  title_alignment: center
  use_compositing: true

panel:
  size: 32
"""


class LayoutError(DeskError):
    """Raised when a layout manifest or an xfconf write is invalid."""


@dataclass(frozen=True)
class _Setting:
    channel: str
    property: str
    value: str
    key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", f"{self.channel}:{self.property}")


@dataclass(frozen=True)
class Layout:
    name: str
    description: str
    settings: tuple[_Setting, ...]
    source: Path


@dataclass
class LayoutState:
    active: str = ""
    display: str = ":0"
    values: dict[str, str | None] = field(default_factory=dict)


class LayoutSession(Protocol):
    """The xfconf operations a layout apply or reset needs."""

    def get(self, channel: str, name: str) -> str | None: ...

    def set(self, channel: str, name: str, value: str) -> None: ...

    def remove(self, channel: str, name: str) -> None: ...

    def reload(self, out: TextIO) -> None: ...


class XfconfSession:
    """Talk to the xfconf daemon of one running session."""

    def __init__(self, display: str) -> None:
        self.environment = _session_environment(display)

    def get(self, channel: str, name: str) -> str | None:
        return _get_property(channel, name, self.environment, _default_runner)

    def set(self, channel: str, name: str, value: str) -> None:
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
    if not isinstance(data, dict) or data.get("version") != LAYOUT_STATE_VERSION:
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
    return LayoutState(active=active, display=display, values=clean)


def _write_state(paths: Paths, state: LayoutState) -> None:
    path = state_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LAYOUT_STATE_VERSION,
        "active": state.active,
        "display": state.display,
        "values": state.values,
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


def _title_alignment(value: object) -> str:
    if not isinstance(value, str) or value not in TITLE_ALIGNMENTS:
        raise LayoutError("expected left, center or right")
    return value


def _panel_size(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or not 16 <= value <= 128:
        raise LayoutError("expected an integer between 16 and 128")
    return str(value)


def _xfwm4_settings(section: Mapping[str, object]) -> tuple[_Setting, ...]:
    settings: list[_Setting] = []
    for key, value in section.items():
        if key == "button_layout":
            normalized = _button_layout(value)
        elif key == "title_alignment":
            normalized = _title_alignment(value)
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


MAX_MANIFEST_BYTES = 64 * 1024


def _read_manifest(path: Path) -> str:
    """Read one layout manifest, refusing links, huge files and non-UTF-8 bytes."""
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise LayoutError(f"could not read {_printable(str(path))}: {error}") from error
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise LayoutError(f"{_printable(str(path))} is not a regular file")
        data = os.read(handle, MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise LayoutError(f"could not read {_printable(str(path))}: {error}") from error
    finally:
        os.close(handle)
    if len(data) > MAX_MANIFEST_BYTES:
        raise LayoutError(
            f"{_printable(str(path))} is larger than {MAX_MANIFEST_BYTES} bytes; it is not a layout manifest"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LayoutError(f"{_printable(str(path))} is not UTF-8; save it as UTF-8") from error


def _parse_layout(name: str, source: Path) -> Layout:
    if not LAYOUT_NAME_PATTERN.fullmatch(name):
        raise LayoutError(f"invalid layout name {name!r}")
    text = _read_manifest(source)
    try:
        data = _load_yaml(text, source)
    except Exception as error:  # ThemeError or YAML errors
        raise LayoutError(str(error)) from error
    if not isinstance(data, dict):
        raise LayoutError(f"{source} must contain a mapping")
    allowed = {"layout", "description", "xfwm4", "panel"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise LayoutError(f"{source} has unknown keys: {', '.join(map(str, unknown))}")
    declared = data.get("layout")
    if declared != name:
        raise LayoutError(f"{source} declares layout {declared!r}, expected {name!r}")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise LayoutError("description must be a string")

    settings: list[_Setting] = []
    for section_name, parser in (("xfwm4", _xfwm4_settings), ("panel", _panel_settings)):
        section = data.get(section_name)
        if section is None:
            continue
        if not isinstance(section, dict):
            raise LayoutError(f"{section_name} must be a mapping")
        settings.extend(parser(section))
    if not settings:
        raise LayoutError("the layout does not change anything")
    return Layout(name=name, description=description.strip(), settings=tuple(settings), source=source)


def load_layout(paths: Paths, name: str) -> Layout:
    """Return one layout manifest, user file first, then the presets."""
    found = _layout_paths(paths).get(name)
    if found is None:
        raise LayoutError(
            f"there is no layout named {name!r}; run 'desk layout init {name}' to create one"
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


def _describe(setting: _Setting, old: str | None) -> str:
    before = "unset" if old is None else _printable(old)
    return f"{setting.channel}{setting.property}: {before} -> {_printable(setting.value)}"


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
    active = _session(session, display)
    previous = _read_state(paths)
    old = {setting.key: active.get(setting.channel, setting.property) for setting in layout.settings}
    for setting in layout.settings:
        print(f"would set {_describe(setting, old[setting.key])}" if dry_run else f"set {_describe(setting, old[setting.key])}", file=out)
    if dry_run:
        return 0

    written: list[_Setting] = []
    try:
        for setting in layout.settings:
            active.set(setting.channel, setting.property, setting.value)
            written.append(setting)
        # 第一次 apply 才记录“最初”的值；重复 apply 只补新键，reset 才能回到原点
        values = {**old, **dict(previous.values)}
        _write_state(
            paths,
            LayoutState(active=layout.name, display=display, values=values),
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
    _remove_state(paths)
    try:
        active.reload(out)
    except OSError as error:
        print(f"warning: could not reload the window manager ({error})", file=out)
    print(f"reset the layout {state.active}", file=out)
    return 0
