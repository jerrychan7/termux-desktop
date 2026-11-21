"""Whole-panel layouts for Xfce, written through xfconf.

A layout manifest may describe one or two panels: where they sit, how big
they are and which plugins they contain, in order.  Applying a panel layout
replaces the panel configuration of the running session, so the previous
configuration is snapshotted first and restored by a reset.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

PANEL_CHANNEL = "xfce4-panel"
# p=10 anchors the panel in the middle of the bottom edge; a one-percent
# length then becomes a centered dock instead of a strip in the corner.
PANEL_POSITIONS = {
    "top": "p=6;x=0;y=0",
    "bottom": "p=8;x=0;y=0",
    "bottom-center": "p=10;x=0;y=0",
}
PLUGIN_TYPES = (
    "actions",
    "applicationsmenu",
    "clock",
    "datetime",
    "directorymenu",
    "launcher",
    "notification-plugin",
    "pager",
    "separator",
    "showdesktop",
    "systray",
    "tasklist",
    "thunar-tpa",
    "whiskermenu",
    "windowmenu",
    "xfce4-clipman-plugin",
    "xfce4-power-manager",
    "xfce4-pulseaudio-plugin",
    "xfce4-screenshooter",
    "screenshooter",
)
# Only these settings can be written; everything else keeps its default.
PLUGIN_SETTINGS: dict[str, dict[str, str]] = {
    "actions": {"appearance": "uint"},
    "datetime": {
        "layout": "uint",
        "date_font": "string",
        "time_font": "string",
        "date_format": "string",
        "time_format": "string",
    },
    "clock": {
        "mode": "uint",
        "digital-layout": "uint",
        "digital-time-format": "string",
        "digital-date-format": "string",
        "digital-time-font": "string",
        "digital-date-font": "string",
        "show-military": "bool",
        "show-meridiem": "bool",
        "flash-separators": "bool",
        "show-inactive": "bool",
    },
    "pager": {
        "rows": "uint",
        "miniature-view": "bool",
        "wrap-workspaces": "bool",
        "workspace-scrolling": "bool",
        "numbering": "bool",
    },
    "separator": {"expand": "bool", "style": "uint"},
    "systray": {"square-icons": "bool"},
    "tasklist": {
        "grouping": "uint",
        "show-labels": "bool",
        "show-handle": "bool",
        "flat-buttons": "bool",
        "sort-order": "uint",
        "middle-click": "uint",
        "show-only-minimized": "bool",
        "include-all-workspaces": "bool",
        "window-scrolling": "bool",
        "show-wireframes": "bool",
        "show-tooltips": "bool",
    },
    "whiskermenu": {
        "launcher-icon-size": "int",
        "category-icon-size": "int",
        "menu-width": "int",
        "menu-height": "int",
        "menu-opacity": "int",
        "category-show-name": "bool",
        "launcher-show-name": "bool",
        "launcher-show-tooltip": "bool",
        "show-command-shutdown": "bool",
        "show-command-lock": "bool",
        "show-command-settings": "bool",
        "show-command-logout": "bool",
        "show-command-menueditor": "bool",
        "view-mode": "int",
        "button-icon": "string",
        "button-title": "string",
        "button-single-row": "bool",
        "hover-switch-category": "bool",
        "default-category": "int",
        "profile-shape": "int",
        "favorites-in-recent": "bool",
        "position-categories-alternate": "bool",
        "position-categories-horizontal": "bool",
        "position-commands-alternate": "bool",
        "position-profile-alternate": "bool",
        "position-search-alternate": "bool",
    },
    "launcher": {
        "menu-height": "int",
        "menu-width": "int",
        "menu-opacity": "int",
    },
}
PLUGIN_ITEMS = ("launcher", "actions")
PANEL_MAX_PLUGINS = 32
PANEL_MAX_PANELS = 2


class PanelError(Exception):
    """Raised when a panel section or an xfconf write is invalid."""


@dataclass(frozen=True)
class PanelPlugin:
    type: str
    settings: tuple[tuple[str, str, str], ...] = ()  # (name, kind, value)
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class Panel:
    position: str
    size: int
    length: int | None
    plugins: tuple[PanelPlugin, ...]
    icon_size: int | None = None
    length_adjust: bool | None = None


@dataclass(frozen=True)
class SnapshotEntry:
    path: str
    kind: str  # scalar type, or "array-int"/"array-string"
    values: tuple[str, ...]


@dataclass
class PanelSnapshot:
    entries: tuple[SnapshotEntry, ...] = ()


def _plugin(entry: object, label: str) -> PanelPlugin:
    if not isinstance(entry, dict):
        raise PanelError(f"{label} must be a mapping")
    unknown = set(entry) - {"type", "settings", "items"}
    if unknown:
        raise PanelError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")
    kind = entry.get("type")
    if not isinstance(kind, str) or kind not in PLUGIN_TYPES:
        raise PanelError(f"{label}: {kind!r} is not a known panel plugin")
    raw_settings = entry.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise PanelError(f"{label}: settings must be a mapping")
    allowed = PLUGIN_SETTINGS.get(kind, {})
    unknown = set(raw_settings) - set(allowed)
    if unknown:
        raise PanelError(f"{label}: unknown {kind} settings: {', '.join(sorted(unknown))}")
    settings = tuple(
        (name, allowed[name], _plugin_value(name, value, allowed[name])) for name, value in raw_settings.items()
    )
    raw_items = entry.get("items", ())
    if raw_items:
        if kind not in PLUGIN_ITEMS:
            raise PanelError(f"{label}: {kind} does not take items")
        if not isinstance(raw_items, (list, tuple)) or not all(
            isinstance(item, str) and item and "/" not in item and "\0" not in item for item in raw_items
        ):
            raise PanelError(f"{label}: items must be a list of desktop ids")
        items = tuple(raw_items)
    else:
        items = ()
    if not isinstance(entry.get("items", ()), (list, tuple)):
        raise PanelError(f"{label}: items must be a list of desktop ids")
    return PanelPlugin(type=kind, settings=settings, items=items)


def _plugin_value(name: str, value: object, kind: str) -> str:
    if kind == "bool":
        if not isinstance(value, bool):
            raise PanelError(f"{name} must be true or false")
        return "true" if value else "false"
    if kind == "string":
        if not isinstance(value, str) or "\0" in value:
            raise PanelError(f"{name} must be a string")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise PanelError(f"{name} must be an integer")
    if kind == "uint" and value < 0:
        raise PanelError(f"{name} must not be negative")
    return str(value)


def _panel(body: object, label: str) -> Panel:
    if not isinstance(body, dict):
        raise PanelError(f"{label} must be a mapping")
    unknown = set(body) - {"position", "size", "length", "icon-size", "length-adjust", "plugins"}
    if unknown:
        raise PanelError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")
    position = body.get("position", "bottom")
    if position not in PANEL_POSITIONS:
        raise PanelError(f"{label}: position must be top, bottom or bottom-center")
    size = body.get("size", 36)
    if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 128:
        raise PanelError(f"{label}: size must be between 16 and 128")
    length_value = body.get("length", 100)
    if length_value in ("auto", None):
        length: int | None = None
    elif isinstance(length_value, bool) or not isinstance(length_value, int) or not 1 <= length_value <= 100:
        raise PanelError(f"{label}: length must be 'auto' or between 1 and 100")
    else:
        length = length_value
    icon_size_value = body.get("icon-size")
    if icon_size_value is None:
        icon_size: int | None = None
    elif (
        isinstance(icon_size_value, bool)
        or not isinstance(icon_size_value, int)
        or not 16 <= icon_size_value <= 256
    ):
        raise PanelError(f"{label}: icon-size must be between 16 and 256")
    else:
        icon_size = icon_size_value
    adjust_value = body.get("length-adjust")
    if adjust_value is None:
        length_adjust: bool | None = None
    elif not isinstance(adjust_value, bool):
        raise PanelError(f"{label}: length-adjust must be true or false")
    else:
        length_adjust = adjust_value
    raw_plugins = body.get("plugins")
    if not isinstance(raw_plugins, (list, tuple)) or not raw_plugins:
        raise PanelError(f"{label}: plugins must be a non-empty list")
    if len(raw_plugins) > PANEL_MAX_PLUGINS:
        raise PanelError(f"{label}: at most {PANEL_MAX_PLUGINS} plugins are supported")
    plugins = tuple(
        _plugin(entry, f"{label} plugin {index + 1}") for index, entry in enumerate(raw_plugins)
    )
    return Panel(
        position=position,
        size=size,
        length=length,
        plugins=plugins,
        icon_size=icon_size,
        length_adjust=length_adjust,
    )


def parse_panels(document: Mapping[str, object]) -> tuple[Panel, ...]:
    """Read the panel or panels section of a layout manifest."""
    if "panels" in document and "panel" in document:
        raise PanelError("use either panel or panels, not both")
    if "panels" in document:
        raw = document["panels"]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise PanelError("panels must be a non-empty list")
        if len(raw) > PANEL_MAX_PANELS:
            raise PanelError(f"at most {PANEL_MAX_PANELS} panels are supported")
        return tuple(_panel(body, f"panels[{index}]") for index, body in enumerate(raw))
    if "panel" in document:
        body = document["panel"]
        if isinstance(body, dict) and "plugins" not in body and "panels" in body:
            # A short form like panel: {panels: [...]} is not supported.
            raise PanelError("put plugins under panel, or use a panels list")
        return (_panel(body, "panel"),)
    return ()


def panel_commands(panels: Sequence[Panel]) -> list[tuple[str, ...]]:
    """Return the xfconf-query commands that build these panels."""
    commands: list[tuple[str, ...]] = []
    commands.append(_scalar("/configver", "int", "2"))
    index_values = [str(index + 1) for index in range(len(panels))]
    commands.append(_array_command("/panels", index_values, "int"))
    commands.append(_scalar("/panels/dark-mode", "bool", "true"))
    plugin_index = 0
    for panel_index, panel in enumerate(panels, start=1):
        prefix = f"/panels/panel-{panel_index}"
        commands.append(_scalar(f"{prefix}/position", "string", PANEL_POSITIONS[panel.position]))
        length = 1 if panel.length is None else panel.length
        commands.append(_scalar(f"{prefix}/length", "uint", str(length)))
        adjust = panel.length is None if panel.length_adjust is None else panel.length_adjust
        commands.append(_scalar(f"{prefix}/length-adjust", "bool", "true" if adjust else "false"))
        commands.append(_scalar(f"{prefix}/position-locked", "bool", "true"))
        commands.append(_scalar(f"{prefix}/size", "uint", str(panel.size)))
        if panel.icon_size is not None:
            commands.append(_scalar(f"{prefix}/icon-size", "uint", str(panel.icon_size)))
        ids: list[str] = []
        for plugin in panel.plugins:
            plugin_index += 1
            ids.append(str(plugin_index))
            commands.append(_scalar(f"/plugins/plugin-{plugin_index}", "string", plugin.type))
            for name, kind, value in plugin.settings:
                commands.append(_scalar(f"/plugins/plugin-{plugin_index}/{name}", kind, value))
            if plugin.items:
                commands.append(
                    _array_command(f"/plugins/plugin-{plugin_index}/items", plugin.items, "string")
                )
        commands.append(_array_command(f"{prefix}/plugin-ids", ids, "int"))
    return commands


def _scalar(path: str, kind: str, value: str) -> tuple[str, ...]:
    return ("xfconf-query", "-c", PANEL_CHANNEL, "-p", path, "-n", "-t", kind, "-s", value)


def _array_command(path: str, values: Sequence[str], kind: str) -> tuple[str, ...]:
    # Without --force-array xfconf stores a single-element array as a scalar,
    # and the panel refuses to load a panel whose id list is not an array.
    command = ["xfconf-query", "-c", PANEL_CHANNEL, "-p", path, "-n", "-a"]
    for value in values:
        command.extend(("-t", kind, "-s", value))
    return tuple(command)


def channel_reset_commands() -> list[tuple[str, ...]]:
    return [("xfconf-query", "-c", PANEL_CHANNEL, "-p", "/", "-r", "-R")]


def config_root(environ: Mapping[str, str], home: Path) -> Path:
    return Path(environ.get("XDG_CONFIG_HOME") or home / ".config")


def snapshot_from_xml(text: str) -> PanelSnapshot:
    """Turn a xfce4-panel.xml document into a flat list of entries."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise PanelError(f"could not read the panel configuration: {error}") from error
    entries: list[SnapshotEntry] = []

    def walk(element: ElementTree.Element, prefix: str) -> None:
        for property_element in element.findall("property"):
            name = property_element.get("name")
            if not name:
                continue
            path = f"{prefix}/{name}"
            kind = property_element.get("type", "")
            value = property_element.get("value")
            children = property_element.findall("property")
            values = property_element.findall("value")
            if kind == "empty" or (children and kind != "array"):
                if kind != "empty" and value is not None:
                    entries.append(SnapshotEntry(path=path, kind=kind, values=(value,)))
                entries.append(SnapshotEntry(path=path, kind="empty", values=()))
                walk(property_element, path)
            elif kind == "array" or values:
                element_kind = "array-int"
                if values and values[0].get("type") == "string":
                    element_kind = "array-string"
                entries.append(
                    SnapshotEntry(
                        path=path,
                        kind=element_kind,
                        values=tuple(value_element.get("value", "") for value_element in values),
                    )
                )
                # An array property can also carry child properties (panels
                # holds dark-mode and panel-N), so keep walking into it.
                if children:
                    walk(property_element, path)
            elif value is not None:
                entries.append(SnapshotEntry(path=path, kind=kind or "string", values=(value,)))
            else:
                entries.append(SnapshotEntry(path=path, kind=kind or "string", values=()))

    walk(root, "")
    return PanelSnapshot(entries=tuple(entries))


_DBUS_TYPES = {
    "int32": "int",
    "uint32": "uint",
    "int64": "int64",
    "uint64": "uint64",
    "double": "double",
    "boolean": "bool",
    "string": "string",
}


def _capture(command: Sequence[str], environ: Mapping[str, str], runner) -> str:
    result = runner(command, environ)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        raise PanelError(f"{command[0]} failed{suffix}")
    return result.stdout or ""


_DBUS_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _dbus_text(kind: str, raw: str) -> str:
    """Return a D-Bus printed value as the text xfconf-query should receive.

    dbus-send prints strings as ``"value"`` with backslash escapes, so a
    verbatim round trip would store the quotes themselves and the panel would
    then look for a plugin literally called ``"whiskermenu"``.
    """
    if kind != "string":
        return raw
    text = raw.strip()
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    body = text[1:-1]
    return re.sub(r"\\(.)", lambda match: _DBUS_ESCAPES.get(match.group(1), match.group(1)), body)


def _parse_dbus_value(path: str, text: str) -> SnapshotEntry:
    """Turn one GetProperty reply into a snapshot entry with its real type."""
    lines = [line for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("variant") and "array" in stripped and stripped.endswith("["):
            values: list[str] = []
            element_kind = "int"
            for inner in lines[index + 1 :]:
                closing = inner.strip()
                if closing == "]":
                    break
                match = re.match(r"(?:variant\s+)?(\w+)\s+(.*)$", closing)
                if match is None:
                    raise PanelError(f"could not read the value of {path}")
                element_kind = _DBUS_TYPES.get(match.group(1), match.group(1))
                values.append(_dbus_text(element_kind, match.group(2)))
            return SnapshotEntry(path=path, kind=f"array-{element_kind}", values=tuple(values))
        match = re.match(r"variant\s+(\w+)\s+(.*)$", stripped)
        if match is not None:
            kind = _DBUS_TYPES.get(match.group(1), match.group(1))
            return SnapshotEntry(path=path, kind=kind, values=(_dbus_text(kind, match.group(2)),))
    raise PanelError(f"could not read the value of {path}")


def read_snapshot(environ: Mapping[str, str], home: Path | None = None, runner=None) -> PanelSnapshot:
    """Read the current panel channel with the real values and their types."""
    if runner is None:
        runner = _fallback_runner
    listing = _capture(("xfconf-query", "-c", PANEL_CHANNEL, "-l"), environ, runner)
    entries: list[SnapshotEntry] = []
    for path in (line.strip() for line in listing.splitlines()):
        if not path:
            continue
        reply = _capture(
            (
                "dbus-send",
                "--session",
                "--print-reply",
                "--dest=org.xfce.Xfconf",
                "/org/xfce/Xfconf",
                "org.xfce.Xfconf.GetProperty",
                f"string:{PANEL_CHANNEL}",
                f"string:{path}",
            ),
            environ,
            runner,
        )
        entries.append(_parse_dbus_value(path, reply))
    return PanelSnapshot(entries=tuple(entries))


def _fallback_runner(command, environ):
    return subprocess.run(
        list(command), env=dict(environ), capture_output=True, text=True, check=False
    )


def snapshot_commands(snapshot: PanelSnapshot) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []
    for entry in snapshot.entries:
        if entry.kind == "empty":
            continue
        if entry.kind.startswith("array-"):
            element_kind = entry.kind[len("array-") :]
            if entry.values:
                commands.append(_array_command_typed(entry.path, element_kind, entry.values))
        else:
            if not entry.values:
                continue
            commands.append(_scalar(entry.path, entry.kind, entry.values[0]))
    return commands


def _array_command_typed(path: str, element_kind: str, values: Sequence[str]) -> tuple[str, ...]:
    # --force-array is required: a one-element array would otherwise be stored
    # as a scalar and the panel would discard the configuration.
    command = ["xfconf-query", "-c", PANEL_CHANNEL, "-p", path, "-n", "-a"]
    for value in values:
        command.extend(("-t", element_kind, "-s", value))
    return tuple(command)


def _run(command: Sequence[str], environ: Mapping[str, str], runner) -> None:
    result = runner(command, environ)
    code = getattr(result, "returncode", 0)
    if code:
        message = (getattr(result, "stderr", "") or "").strip()
        detail = f": {message}" if message else ""
        raise PanelError(f"{command[0]} rejected {' '.join(command[1:])}{detail}")


def _panel_executable(environ: Mapping[str, str]) -> str:
    executable = shutil.which("xfce4-panel", path=environ.get("PATH"))
    if executable is None:
        raise PanelError("xfce4-panel was not found; run this while the desktop is running (desk start)")
    return executable


def _panel_pids() -> list[int]:
    """Return the pids of live xfce4-panel processes."""
    pids: list[int] = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            command = (directory / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if command and command[0].endswith(b"xfce4-panel"):
            pids.append(int(directory.name))
    return pids


def panel_running() -> bool:
    """Return True while an xfce4-panel process is alive."""
    return bool(_panel_pids())


def stop_panel(environ: Mapping[str, str], runner, *, timeout: float = 5.0) -> None:
    """Ask the running panel to quit and make sure it is gone.

    A panel that is showing an error dialog ignores ``-q``, so after the
    timeout the process is terminated directly.  This is our own process, and
    leaving it alive would let it overwrite the configuration we are about to
    write.
    """
    executable = _panel_executable(environ)
    pids = _panel_pids()
    if not pids:
        return
    runner([executable, "-q"], environ)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _panel_pids():
            return
        time.sleep(0.05)
    for pid in _panel_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _panel_pids():
            return
        time.sleep(0.05)
    _kill_panels()
    time.sleep(0.2)
    if _panel_pids():
        raise PanelError("the panel did not stop; close it and try again")


PANEL_START_ATTEMPTS = 4
PANEL_START_TIMEOUT = 2.0
PANEL_RETRY_SECONDS = 0.5
"""The session manager can restart the panel while we are writing its channel.

That instance keeps xfce4-panel's single-instance lock, so a panel we spawn
right after exits at once and the session is left without a visible panel.
start_panel notices the early exit and clears the interloper before retrying.
"""


def start_panel(
    environ: Mapping[str, str],
    spawn=None,
    *,
    sleep=time.sleep,
    attempts: int = PANEL_START_ATTEMPTS,
    timeout: float = PANEL_START_TIMEOUT,
) -> None:
    """Start a fresh panel that reads the configuration we just wrote."""
    executable = _panel_executable(environ)
    if spawn is None:
        spawn = _spawn_panel
    for _ in range(attempts):
        try:
            process = spawn(executable, environ)
        except OSError as error:
            raise PanelError(f"could not start the panel: {error}") from error
        if _panel_survives(process, sleep, timeout):
            return
        # A panel we did not start owns the lock; drop it and try again.
        _kill_panels()
        sleep(PANEL_RETRY_SECONDS)
    raise PanelError("the panel exited while starting; close any other panel and try again")


def _panel_survives(process: object, sleep, timeout: float) -> bool:
    """Return False when a spawned panel quit right away."""
    poll = getattr(process, "poll", None)
    if not callable(poll):
        # A caller-supplied spawn that returns nothing is taken at its word.
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if poll() is not None:
            return False
        sleep(0.05)
    return True


def _kill_panels() -> None:
    for pid in _panel_pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _spawn_panel(executable: str, environ: Mapping[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [executable],
        env=dict(environ),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


SETTLE_SECONDS = 0.4
"""How long the panel gets to flush its own settings after quitting.

xfce4-panel saves its configuration while exiting. Without this pause the save
can land after our channel reset and replace the values we just wrote.
"""


def apply_panels(
    panels: Sequence[Panel],
    environ: Mapping[str, str],
    home: Path,
    runner,
    spawn=None,
    sleep=time.sleep,
) -> PanelSnapshot:
    """Replace the panel configuration, returning the previous one."""
    previous = read_snapshot(environ, home, runner)
    try:
        stop_panel(environ, runner)
        sleep(SETTLE_SECONDS)
        for command in channel_reset_commands():
            _run(command, environ, runner)
        for command in panel_commands(panels):
            _run(command, environ, runner)
        start_panel(environ, spawn)
    except Exception:
        # Put the session back the way we found it before reporting the failure.
        try:
            restore_snapshot(previous, environ, runner, spawn)
        except Exception:
            pass
        raise
    return previous


def restore_snapshot(
    snapshot: PanelSnapshot,
    environ: Mapping[str, str],
    runner,
    spawn=None,
    sleep=time.sleep,
) -> None:
    stop_panel(environ, runner)
    sleep(SETTLE_SECONDS)
    for command in channel_reset_commands():
        _run(command, environ, runner)
    for command in snapshot_commands(snapshot):
        _run(command, environ, runner)
    start_panel(environ, spawn)


def snapshot_to_json(snapshot: PanelSnapshot) -> list[dict[str, object]]:
    return [
        {"path": entry.path, "kind": entry.kind, "values": list(entry.values)}
        for entry in snapshot.entries
    ]


def snapshot_from_json(raw: object) -> PanelSnapshot:
    if raw is None:
        return PanelSnapshot(entries=())
    if not isinstance(raw, list):
        raise PanelError("the recorded panel snapshot is not a list")
    entries: list[SnapshotEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise PanelError("the recorded panel snapshot is not a list of mappings")
        path = item.get("path")
        kind = item.get("kind")
        values = item.get("values", [])
        if not isinstance(path, str) or not isinstance(kind, str) or not isinstance(values, list):
            raise PanelError("the recorded panel snapshot has an invalid entry")
        entries.append(SnapshotEntry(path=path, kind=kind, values=tuple(str(value) for value in values)))
    return PanelSnapshot(entries=tuple(entries))


PLUGIN_LIBRARIES = {
    "whiskermenu": "libwhiskermenu.so",
    "notification-plugin": "libnotification-plugin.so",
    "xfce4-clipman-plugin": "libclipman.so",
    "xfce4-power-manager": "libxfce4powermanager.so",
    "xfce4-pulseaudio-plugin": "libpulseaudio-plugin.so",
    "xfce4-screenshooter": "libscreenshooter.so",
    "screenshooter": "libscreenshooterplugin.so",
}
PLUGIN_PACKAGES = {
    "whiskermenu": "xfce4-whiskermenu-plugin",
    "xfce4-clipman-plugin": "xfce4-clipman-plugin",
    "xfce4-pulseaudio-plugin": "xfce4-pulseaudio-plugin",
    "screenshooter": "xfce4-screenshooter",
}
LEGACY_PLUGIN_FILES = {
    "xfce4-power-manager": "libxfce4powermanager.so",
    "xfce4-pulseaudio-plugin": "libpulseaudio-plugin.so",
}


def library_name(kind: str) -> str:
    """Return the shared library that provides one plugin type."""
    if kind in PLUGIN_LIBRARIES:
        return PLUGIN_LIBRARIES[kind]
    return f"lib{kind}.so"


def required_plugins(panels: Sequence[Panel]) -> list[str]:
    """Return the plugin types used by these panels, without duplicates."""
    seen: list[str] = []
    for panel_spec in panels:
        for plugin in panel_spec.plugins:
            if plugin.type not in seen:
                seen.append(plugin.type)
    return seen


def missing_plugins(panels: Sequence[Panel], plugin_directory: Path) -> list[str]:
    """Return the plugin types whose shared library is missing."""
    missing: list[str] = []
    for kind in required_plugins(panels):
        if not (plugin_directory / library_name(kind)).exists():
            missing.append(kind)
    return missing


def install_hint(plugins: Sequence[str]) -> str:
    """Return a shell hint that installs the packages the plugins need."""
    packages: list[str] = []
    for kind in plugins:
        package = PLUGIN_PACKAGES.get(kind)
        if package and package not in packages:
            packages.append(package)
    if not packages:
        return "install the missing Xfce panel plugin packages with pkg"
    return "pkg install " + " ".join(packages)
