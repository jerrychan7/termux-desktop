"""Remember the desktop settings from before a skin changed them.

A skin owns the settings it declares. When another skin is applied, the keys the
previous skin changed but the new one does not declare must go back to what they
were before skins started editing them; otherwise a font or wallpaper outlives
the skin that installed it.

The first apply captures the current value of every key it is about to write and
stores it in ``skin-baseline.json``. Later applies restore the stored value of
keys the new skin does not write, so switching skins never leaves a leftover
behind. ``desk skin reset`` throws that file away.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from . import layout as layout_tools
from . import theme_model
from . import theme_session as theme_tools
from .config import DISPLAY_NAME, DeskError, Paths

MAX_BASELINE_BYTES = 64 * 1024


@dataclass(frozen=True)
class Baseline:
    """The values a display had before the current skin changed them."""

    display: str
    skin: str | None
    values: Mapping[str, Mapping[str, str | None]]


def path_for(paths: Paths) -> Path:
    """Return the baseline file of this display."""
    suffix = "" if paths.display == DISPLAY_NAME else f"-{paths.display[1:]}"
    return paths.state_dir / f"skin-baseline{suffix}.json"


def _read_raw(path: Path) -> dict[str, object] | None:
    """Read the baseline document, or None when it is absent."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise DeskError(f"could not read {path}: {error}") from error
    if len(data) > MAX_BASELINE_BYTES:
        raise DeskError(f"{path} is larger than {MAX_BASELINE_BYTES} bytes")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeskError(f"could not parse {path}: {error}") from error
    if not isinstance(document, dict):
        raise DeskError(f"{path} does not hold an object")
    return document


def load(paths: Paths) -> Baseline | None:
    """Return the saved baseline of this display, if any."""
    path = path_for(paths)
    document = _read_raw(path)
    if document is None:
        return None
    display = document.get("display")
    skin = document.get("skin")
    values = document.get("values")
    if not isinstance(display, str) or not isinstance(values, dict):
        raise DeskError(f"{path} is not a skin baseline")
    if skin is not None and not isinstance(skin, str):
        raise DeskError(f"{path} has an invalid skin name")
    cleaned: dict[str, dict[str, str | None]] = {}
    for channel, properties in values.items():
        if not isinstance(channel, str) or not isinstance(properties, dict):
            raise DeskError(f"{path} has an invalid channel")
        cleaned[channel] = {
            str(name): None if value is None else str(value)
            for name, value in properties.items()
        }
    return Baseline(display=display, skin=skin, values=cleaned)


def save(paths: Paths, baseline: Baseline) -> None:
    """Write the baseline atomically."""
    path = path_for(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "display": baseline.display,
        "skin": baseline.skin,
        "values": baseline.values,
    }
    text = json.dumps(document, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".skin-baseline-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise DeskError(f"could not write {path}: {error}") from error


def forget(paths: Paths) -> None:
    """Delete the baseline so the next apply starts from the current desktop."""
    try:
        path_for(paths).unlink()
    except FileNotFoundError:
        return
    except OSError as error:
        raise DeskError(f"could not remove {path_for(paths)}: {error}") from error


def keys_for(
    theme: theme_model.Theme,
    layout: layout_tools.Layout | None,
    env: Mapping[str, str],
    runner: theme_tools.Runner,
) -> tuple[tuple[str, str], ...]:
    """Return the xfconf properties this skin is going to write."""
    keys: list[tuple[str, str]] = []
    if "gtk" in theme.components:
        keys.append((theme_tools.XSETTINGS, "/Net/ThemeName"))
    if "icons" in theme.components:
        keys.append((theme_tools.XSETTINGS, "/Net/IconThemeName"))
    if "cursors" in theme.components:
        keys.append((theme_tools.XSETTINGS, "/Gtk/CursorThemeName"))
    if "xfwm" in theme.components:
        keys.append((theme_tools.XFWM, "/general/theme"))
    if "default" in theme.fonts:
        keys.append((theme_tools.XSETTINGS, "/Gtk/FontName"))
    if "monospace" in theme.fonts:
        keys.append((theme_tools.XSETTINGS, "/Gtk/MonospaceFontName"))
    if "wallpaper" in theme.components:
        keys.extend(
            (theme_tools.DESKTOP, name)
            for name in theme_tools._wallpaper_properties(env, runner)
        )
    if layout is not None:
        keys.extend((setting.channel, setting.property) for setting in layout.settings)
    return tuple(keys)


def _entries(baseline: Baseline) -> list[dict[str, str | None]]:
    """Flatten the baseline into the shape the theme session restores."""
    return [
        {"channel": channel, "property": name, "value": value}
        for channel, properties in baseline.values.items()
        for name, value in properties.items()
    ]


def prepare(
    paths: Paths,
    display: str,
    keys: Sequence[tuple[str, str]],
    env: Mapping[str, str],
    runner: theme_tools.Runner,
    out: TextIO,
) -> Baseline:
    """Restore the keys this apply will not write, then capture the new ones."""
    stored = load(paths)
    if stored is not None and stored.display != display:
        stored = None
    values: dict[str, dict[str, str | None]] = {}
    if stored is not None:
        values = {channel: dict(properties) for channel, properties in stored.values.items()}
    wanted = set(keys)
    stale = [
        (channel, name, value)
        for channel, properties in values.items()
        for name, value in properties.items()
        if (channel, name) not in wanted
    ]
    if stale:
        theme_tools._restore_snapshot(
            tuple(
                {"channel": channel, "property": name, "value": value}
                for channel, name, value in stale
            ),
            env,
            runner,
        )
        for channel, name, value in stale:
            print(
                f"restored {channel}{name} to {'its previous value' if value is not None else 'nothing'}",
                file=out,
            )
            values[channel].pop(name, None)
        values = {channel: properties for channel, properties in values.items() if properties}
    for channel, name in keys:
        properties = values.setdefault(channel, {})
        if name in properties:
            continue
        properties[name] = theme_tools._get_property(channel, name, env, runner)
    return Baseline(display=display, skin=stored.skin if stored else None, values=values)


def record(paths: Paths, baseline: Baseline, skin: str) -> Baseline:
    """Store the baseline together with the skin that just claimed the keys."""
    updated = Baseline(display=baseline.display, skin=skin, values=baseline.values)
    save(paths, updated)
    return updated


def restore(paths: Paths, env: Mapping[str, str], runner: theme_tools.Runner) -> int:
    """Put every remembered value back and forget the baseline."""
    stored = load(paths)
    if stored is None:
        return 0
    theme_tools._restore_snapshot(tuple(_entries(stored)), env, runner)
    forget(paths)
    return len(_entries(stored))
