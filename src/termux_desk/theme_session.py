"""The xfconf side of a theme: sessions, snapshots and property writes."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from . import fonts as font_tools
from .config import Paths
from .theme_model import BUILTIN_GTK_THEMES, BUILTIN_XFWM_THEMES, Theme, ThemeError

XSETTINGS = "xsettings"
XFWM = "xfwm4"
DESKTOP = "xfce4-desktop"
PROPERTIES = (
    (XSETTINGS, "/Net/ThemeName"),
    (XSETTINGS, "/Net/IconThemeName"),
    (XSETTINGS, "/Net/CursorThemeName"),
    (XSETTINGS, "/Gtk/FontName"),
    (XSETTINGS, "/Gtk/MonospaceFontName"),
    (XFWM, "/general/theme"),
)

Runner = Callable[[Sequence[str], Mapping[str, str]], "subprocess.CompletedProcess[str]"]

def _default_runner(command: Sequence[str], env: Mapping[str, str]) -> "subprocess.CompletedProcess[str]":
    """Run xfconf-query and friends without a shell."""
    try:
        return subprocess.run(
            list(command),
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise ThemeError(
            f"{command[0]} was not found; run this while the desktop is running (desk start)"
        ) from error
    except OSError as error:
        raise ThemeError(f"could not run {command[0]}: {error}") from error


def _session_environment(display: str, *, proc_root: Path = Path("/proc")) -> dict[str, str]:
    """Return a session environment that reaches the display's D-Bus."""
    for directory in proc_root.iterdir() if proc_root.is_dir() else ():
        if not directory.name.isdigit():
            continue
        try:
            raw = (directory / "environ").read_bytes()
        except (OSError, PermissionError):
            continue
        pairs = [item.split(b"=", 1) for item in raw.split(b"\0") if b"=" in item]
        values = {key.decode("utf-8", "replace"): value.decode("utf-8", "replace") for key, value in pairs}
        if values.get("DISPLAY") != display:
            continue
        bus = values.get("DBUS_SESSION_BUS_ADDRESS")
        if not bus:
            continue
        environment = dict(os.environ)
        environment.update({"DISPLAY": display, "DBUS_SESSION_BUS_ADDRESS": bus})
        return environment
    raise ThemeError(
        f"there is no running session on {display}; start the desktop with 'desk start' before applying a theme"
    )


def _xfconf(
    arguments: Sequence[str], env: Mapping[str, str], runner: Runner
) -> "subprocess.CompletedProcess[str]":
    """Run xfconf-query with one argument list."""
    return runner(("xfconf-query", *arguments), env)


_ARRAY_HEADER = re.compile(r"^Value is an array with \d+ items:\s*$")


def _array_items(value: str) -> tuple[str, ...] | None:
    """Return the items of `xfconf-query` array output, or None for a scalar."""
    header, _, body = value.partition("\n")
    if not _ARRAY_HEADER.match(header):
        return None
    return tuple(line for line in body.split("\n") if line)


def _get_property(channel: str, name: str, env: Mapping[str, str], runner: Runner) -> str | None:
    """Return one string property, or None when it does not exist."""
    result = _xfconf(("-c", channel, "-p", name), env, runner)
    if result.returncode != 0:
        return None
    # 空字符串也是合法值：不能和“属性不存在”混为一谈
    return result.stdout.rstrip("\n")


_INTEGER = re.compile(r"^-?[0-9]+$")


def _property_type(value: str) -> str:
    """Guess the xfconf type of a value so a new property keeps its kind."""
    lowered = value.strip().lower()
    if lowered in ("true", "false"):
        return "bool"
    if _INTEGER.match(lowered):
        return "int"
    return "string"


def _set_property(
    channel: str,
    name: str,
    value: str | tuple[str, ...],
    env: Mapping[str, str],
    runner: Runner,
) -> None:
    """Set one property, creating it with the right type when it does not exist yet."""
    if isinstance(value, tuple):
        _set_array_property(channel, name, value, env, runner)
        return
    items = _array_items(value)
    if items is not None:
        _set_array_property(channel, name, items, env, runner)
        return
    result = _xfconf(("-c", channel, "-p", name, "-s", value), env, runner)
    if result.returncode == 0:
        return
    result = _xfconf(
        ("-c", channel, "-p", name, "-n", "-t", _property_type(value), "-s", value), env, runner
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ThemeError(f"could not set {channel}{name}: {detail or 'xfconf-query failed'}")


def _set_array_property(
    channel: str,
    name: str,
    values: Sequence[str],
    env: Mapping[str, str],
    runner: Runner,
) -> None:
    """Write a string array; -a keeps a one-item array from becoming a scalar."""
    arguments = ["-c", channel, "-p", name, "-n", "-a"]
    for value in values:
        arguments.extend(("-t", "string", "-s", value))
    result = _xfconf(tuple(arguments), env, runner)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ThemeError(f"could not set {channel}{name}: {detail or 'xfconf-query failed'}")


def _remove_property(channel: str, name: str, env: Mapping[str, str], runner: Runner) -> None:
    """Remove one property, ignoring a missing one."""
    _xfconf(("-c", channel, "-p", name, "-r"), env, runner)


def _wallpaper_properties(env: Mapping[str, str], runner: Runner) -> tuple[str, ...]:
    """Return the xfce4-desktop properties that select the wallpaper image."""
    result = _xfconf(("-c", DESKTOP, "-l"), env, runner)
    if result.returncode != 0:
        return ()
    return tuple(
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().endswith("/last-image")
    )


def _snapshot(
    env: Mapping[str, str], runner: Runner, properties: Sequence[tuple[str, str]] | None = None
) -> tuple[Mapping[str, str | None], ...]:
    """Remember the properties this apply is about to change."""
    items: list[Mapping[str, str | None]] = []
    for channel, name in properties if properties is not None else PROPERTIES:
        items.append({"channel": channel, "property": name, "value": _get_property(channel, name, env, runner)})
    return tuple(items)


def _needed_properties(
    theme: Theme, components: Mapping[str, str], env: Mapping[str, str], runner: Runner
) -> tuple[tuple[str, str], ...]:
    """Return the properties that applying this theme will touch."""
    properties: list[tuple[str, str]] = []
    mapping = (
        (XSETTINGS, "/Net/ThemeName", "gtk"),
        (XSETTINGS, "/Net/IconThemeName", "icons"),
        (XSETTINGS, "/Net/CursorThemeName", "cursors"),
        (XFWM, "/general/theme", "xfwm"),
    )
    for channel, name, key in mapping:
        if key in components:
            properties.append((channel, name))
    if "default" in theme.fonts:
        properties.append((XSETTINGS, "/Gtk/FontName"))
    if "monospace" in theme.fonts:
        properties.append((XSETTINGS, "/Gtk/MonospaceFontName"))
    if "wallpaper" in components:
        properties.extend((DESKTOP, name) for name in _wallpaper_properties(env, runner))
    return tuple(properties)


def _planned_writes(
    theme: Theme,
    components: Mapping[str, str],
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
) -> tuple[tuple[str, str, str], ...]:
    """Return the writes an apply would perform, for dry runs."""
    writes: list[tuple[str, str, str]] = []
    mapping = (
        (XSETTINGS, "/Net/ThemeName", "gtk"),
        (XSETTINGS, "/Net/IconThemeName", "icons"),
        (XSETTINGS, "/Net/CursorThemeName", "cursors"),
        (XFWM, "/general/theme", "xfwm"),
    )
    for channel, name, key in mapping:
        if key in components:
            writes.append((channel, name, components[key]))
    if "wallpaper" in components:
        properties = (
            _wallpaper_properties(env, runner) if env is not None and runner is not None else ()
        )
        if not properties:
            properties = ("/<monitor>/last-image",)
        writes.extend((DESKTOP, name, components["wallpaper"]) for name in properties)
    if "default" in theme.fonts:
        family = _font_family(theme.fonts["default"])
        writes.append((XSETTINGS, "/Gtk/FontName", f"{family} {theme.fonts.get('default_size', '10')}"))
    if "monospace" in theme.fonts:
        family = _font_family(theme.fonts["monospace"])
        writes.append(
            (XSETTINGS, "/Gtk/MonospaceFontName", f"{family} {theme.fonts.get('monospace_size', '10')}")
        )
    return tuple(writes)


def _restore_snapshot(
    snapshot: Sequence[Mapping[str, str | None]], env: Mapping[str, str], runner: Runner
) -> None:
    """Put the saved property values back."""
    for item in snapshot:
        channel = str(item["channel"])
        name = str(item["property"])
        value = item.get("value")
        if value is None:
            _remove_property(channel, name, env, runner)
        else:
            _set_property(channel, name, str(value), env, runner)


def _reload(env: Mapping[str, str], out: TextIO) -> None:
    """Ask xfsettingsd and xfwm4 to reload; both stay running, so do not wait."""
    for command in (("xfsettingsd", "--replace"), ("xfwm4", "--replace")):
        try:
            subprocess.Popen(
                list(command),
                env=dict(env),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            print(
                f"warning: could not run {' '.join(command)} ({error}); restart the session to see every change",
                file=out,
            )




def _font_family(name: str) -> str:
    """Return the family part of a font setting."""
    if name.startswith("android:"):
        return name.split(":", 1)[1]
    return name


def _font_matcher(paths: Paths, matcher: font_tools.FamilyMatcher | None) -> font_tools.FamilyMatcher | None:
    """Return a fontconfig matcher when the library exists."""
    if matcher is not None:
        return matcher
    if paths.prefix is None:
        return None
    library = paths.prefix / "lib" / "libfontconfig.so"
    if not library.is_file():
        return None
    return font_tools.FontconfigMatcher(library)


def _check_component(
    name: str,
    value: str,
    paths: Paths,
    data_home: Path,
    home: Path,
    matcher: font_tools.FamilyMatcher | None,
) -> bool:
    """Return True when a component value can be resolved on this machine."""
    if name == "gtk":
        if value in BUILTIN_GTK_THEMES:
            return True
        roots = (data_home / "themes", home / ".themes")
        if paths.prefix is not None:
            roots += (paths.prefix / "share" / "themes",)
        roots += (Path("/usr/share/themes"),)
        return any((root / value / "gtk-3.0").is_dir() or (root / value / "gtk-2.0").is_dir() for root in roots)
    if name == "xfwm":
        if value in BUILTIN_XFWM_THEMES:
            return True
        roots = (data_home / "themes", home / ".themes")
        if paths.prefix is not None:
            roots += (paths.prefix / "share" / "themes",)
        roots += (Path("/usr/share/themes"),)
        return any((root / value / "xfwm4").is_dir() for root in roots)
    if name in ("icons", "cursors"):
        roots = (data_home / "icons", home / ".icons")
        if paths.prefix is not None:
            roots += (paths.prefix / "share" / "icons",)
        roots += (Path("/usr/share/icons"),)
        return any((root / value / "index.theme").is_file() for root in roots)
    if name == "wallpaper":
        return Path(value).expanduser().is_file()
    return False




def _check_available_components(
    theme: Theme, paths: Paths, matcher: font_tools.FamilyMatcher | None, out: TextIO
) -> None:
    """Fail before installing assets when a component is not available at all."""
    provided = {asset.target for asset in theme.assets}
    missing: list[str] = []
    for name, value in theme.components.items():
        if name == "wallpaper":
            continue  # 壁纸由资产提供，装好资产之后再检查
        if value in provided:
            continue  # 这个组件由清单里的资产安装，装完再校验
        if not _check_component(name, value, paths, paths.xdg_data_home, paths.home, matcher):
            missing.append(f"{name} {value} (declare an asset that installs it)")
    if missing:
        raise ThemeError(
            "the theme cannot be applied because these components are missing: " + "; ".join(missing)
        )
    del out


def _check_fonts(theme: Theme, matcher: font_tools.FamilyMatcher | None) -> None:
    """Fail before installing assets when an Android font family is unknown."""
    for key in ("default", "monospace"):
        spec = theme.fonts.get(key)
        if not spec or not spec.startswith("android:") or matcher is None:
            continue
        family = _font_family(spec)
        if not matcher.family_exists(family):
            raise ThemeError(f"font family {family!r} does not exist on this device")


def _resolve_components(
    theme: Theme, installed: Mapping[str, str], paths: Paths, matcher: font_tools.FamilyMatcher | None, out: TextIO
) -> Mapping[str, str]:
    """Return component values with asset paths substituted, or explain what is missing."""
    missing: list[str] = []
    resolved: dict[str, str] = {}
    for name, value in theme.components.items():
        if name == "wallpaper":
            path = Path(installed.get(value, value)).expanduser()
            if not path.is_file():
                missing.append(f"wallpaper {value} (declare an asset with kind 'wallpaper')")
            else:
                resolved[name] = str(path)
            continue
        if not _check_component(name, value, paths, paths.xdg_data_home, paths.home, matcher):
            missing.append(f"{name} {value} (declare an asset that installs it)")
            continue
        resolved[name] = value
    if missing:
        raise ThemeError(
            "the theme cannot be applied because these components are missing: " + "; ".join(missing)
        )
    return resolved


def _write_components(
    components: Mapping[str, str], env: Mapping[str, str], runner: Runner
) -> None:
    """Write the resolved component values to xfconf."""
    mapping = (
        (XSETTINGS, "/Net/ThemeName", "gtk"),
        (XSETTINGS, "/Net/IconThemeName", "icons"),
        (XSETTINGS, "/Net/CursorThemeName", "cursors"),
        (XFWM, "/general/theme", "xfwm"),
    )
    for channel, name, key in mapping:
        if key in components:
            _set_property(channel, name, components[key], env, runner)
    if "wallpaper" in components:
        for name in _wallpaper_properties(env, runner):
            _set_property(DESKTOP, name, components["wallpaper"], env, runner)


def _write_fonts(
    theme: Theme, paths: Paths, env: Mapping[str, str], runner: Runner, matcher: font_tools.FamilyMatcher | None
) -> None:
    """Write the configured font names, checking android families when possible."""
    if "default" in theme.fonts:
        family = _font_family(theme.fonts["default"])
        if theme.fonts["default"].startswith("android:") and matcher is not None:
            if not matcher.family_exists(family):
                raise ThemeError(f"font family {family!r} does not exist on this device")
        size = theme.fonts.get("default_size", "10")
        _set_property(XSETTINGS, "/Gtk/FontName", f"{family} {size}", env, runner)
    if "monospace" in theme.fonts:
        family = _font_family(theme.fonts["monospace"])
        if theme.fonts["monospace"].startswith("android:") and matcher is not None:
            if not matcher.family_exists(family):
                raise ThemeError(f"font family {family!r} does not exist on this device")
        size = theme.fonts.get("monospace_size", "10")
        _set_property(XSETTINGS, "/Gtk/MonospaceFontName", f"{family} {size}", env, runner)




def missing_packages(packages: Sequence[str], *, runner: Runner | None = None) -> list[str]:
    """Return the Termux packages that are not installed yet."""
    if not packages:
        return []
    run = runner or _default_runner
    missing: list[str] = []
    for name in packages:
        result = run(("dpkg-query", "-W", "-f=${Status}", name), {})
        status = getattr(result, "stdout", "") or ""
        if getattr(result, "returncode", 0) or "install ok installed" not in status:
            missing.append(name)
    return missing
