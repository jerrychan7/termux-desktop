"""Theme model: manifests, paths, state and the read-only commands."""

from __future__ import annotations

import json
import os
import posixpath
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

from . import sanitize
from .config import APPLICATION_NAME, DeskError, Paths
from .settings import model as settings_model

THEME_VERSION = 1
THEME_FILE_SUFFIX = ".yaml"
THEME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")
ASSET_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
COMPONENT_KEYS = ("gtk", "xfwm", "icons", "cursors", "wallpaper")
FONT_KEYS = ("default", "monospace", "default_size", "monospace_size")
ASSET_KINDS = ("theme", "icons", "cursors", "font", "wallpaper", "config")
# Only these application configuration files may be replaced by a skin.
CONFIG_TARGETS = (
    "gtk-3.0/bookmarks",
    "Thunar/uca.xml",
    "Thunar/accels.scm",
    "xfce4/terminal/terminalrc",
    "Kvantum/kvantum.kvconfig",
)
BUILTIN_GTK_THEMES = ("Adwaita", "Adwaita-dark", "HighContrast", "HighContrastInverse")
BUILTIN_XFWM_THEMES = ("Default",)
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
TOFU_WARNING = (
    "the asset has no sha256; desk cannot verify the download. "
    "Pass --allow-unverified to download it once and record its hash"
)

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


class ThemeError(DeskError):
    """The theme cannot be read, installed or applied."""


@dataclass(frozen=True)
class ThemeAsset:
    """One downloadable or local file that a theme needs."""

    kind: str
    target: str
    source: str
    sha256: str | None = None
    is_url: bool = False
    pick: str | None = None


@dataclass(frozen=True)
class Theme:
    """One parsed theme manifest."""

    name: str
    path: Path
    description: str
    components: Mapping[str, str]
    fonts: Mapping[str, str]
    assets: tuple[ThemeAsset, ...]
    packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThemeState:
    """The applied theme and the values that reset has to restore."""

    active: str | None = None
    snapshot: tuple[Mapping[str, str | None], ...] = ()
    installed: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)
    display: str | None = None
    configs: tuple[str, ...] = ()

    def to_record(self) -> dict[str, object]:
        """Return the JSON form of this state."""
        return {
            "version": THEME_VERSION,
            "active": self.active,
            "snapshot": [dict(item) for item in self.snapshot],
            "installed": list(self.installed),
            "hashes": dict(self.hashes),
            "display": self.display,
            "configs": list(self.configs),
        }

    @classmethod
    def from_record(cls, record: object, source: Path) -> "ThemeState":
        """Validate a saved record and return the state it describes."""
        if not isinstance(record, dict):
            raise ThemeError(f"{source} does not contain a theme state")
        if record.get("version") != THEME_VERSION:
            raise ThemeError(f"{source} does not use theme state version {THEME_VERSION}")
        active = record.get("active")
        if active is not None and not isinstance(active, str):
            raise ThemeError(f"{source} has an invalid active theme")
        display = record.get("display")
        if display is not None and not isinstance(display, str):
            raise ThemeError(f"{source} has an invalid display")
        snapshot = record.get("snapshot", [])
        if not isinstance(snapshot, list):
            raise ThemeError(f"{source} has an invalid snapshot")
        installed = record.get("installed", [])
        if not isinstance(installed, list) or any(not isinstance(path, str) for path in installed):
            raise ThemeError(f"{source} has an invalid installed list")
        hashes = record.get("hashes", {})
        if not isinstance(hashes, dict):
            raise ThemeError(f"{source} has an invalid hash list")
        configs = record.get("configs", [])
        if not isinstance(configs, list) or any(not isinstance(item, str) for item in configs):
            raise ThemeError(f"{source} has an invalid config list")
        items: list[Mapping[str, str | None]] = []
        for item in snapshot:
            if not isinstance(item, dict):
                raise ThemeError(f"{source} has an invalid snapshot entry")
            if not isinstance(item.get("channel"), str) or not isinstance(item.get("property"), str):
                raise ThemeError(f"{source} has an invalid snapshot entry")
            value = item.get("value")
            if value is not None and not isinstance(value, str):
                raise ThemeError(f"{source} has an invalid snapshot value")
            items.append({"channel": item["channel"], "property": item["property"], "value": value})
        return cls(
            active=active,
            snapshot=tuple(items),
            installed=tuple(installed),
            hashes={str(key): str(value) for key, value in hashes.items()},
            display=display,
            configs=tuple(configs),
        )



def _read_manifest(path: Path) -> str:
    """Read one manifest, refusing links, huge files and non-UTF-8 bytes."""
    try:
        if path.is_symlink():
            raise ThemeError(f"{_printable(str(path))} is a symbolic link; use a regular file")
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except ThemeError:
        raise
    except OSError as error:
        raise ThemeError(f"could not read {_printable(str(path))}: {error}") from error
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise ThemeError(f"{_printable(str(path))} is not a regular file")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise ThemeError(
                f"{_printable(str(path))} is larger than {MAX_MANIFEST_BYTES} bytes; it is not a manifest"
            )
        data = os.read(handle, MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise ThemeError(f"could not read {_printable(str(path))}: {error}") from error
    finally:
        os.close(handle)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ThemeError(
            f"{_printable(str(path))} is larger than {MAX_MANIFEST_BYTES} bytes; it is not a manifest"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ThemeError(f"{_printable(str(path))} is not UTF-8; save it as UTF-8") from error


def _require_yaml() -> object:
    """Import PyYAML or report the one command that installs it."""
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as error:  # pragma: no cover - depends on the device
        raise ThemeError(f"desk needs PyYAML to read themes; {settings_model.PYYAML_HINT}") from error
    return yaml


def _load_yaml(text: str, source: Path) -> object:
    """Read one YAML document and reject duplicate keys."""
    yaml = _require_yaml()

    class Loader(yaml.SafeLoader):  # type: ignore[misc]
        pass

    def construct_mapping(loader, node, deep=False):  # type: ignore[no-untyped-def]
        seen: set[object] = set()
        for key_node, _value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError as error:
                raise ThemeError(f"{source} uses an unhashable mapping key") from error
            if duplicate:
                raise ThemeError(f"{source} repeats the key {key!r}")
            seen.add(key)
        return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)

    Loader.add_constructor("tag:yaml.org,2002:map", construct_mapping)
    try:
        return yaml.load(text, Loader=Loader)
    except ThemeError:
        raise
    except Exception as error:  # yaml.YAMLError, UnicodeDecodeError
        raise ThemeError(f"{source} is not valid YAML: {error}") from error


def _printable(text: str) -> str:
    """Escape controls so a manifest cannot rewrite the terminal."""
    pieces: list[str] = []
    for character in text:
        if character.isprintable() and character not in "\u2028\u2029":
            pieces.append(character)
        else:
            pieces.append(f"\\u{ord(character):04x}")
    return "".join(pieces)


def config_directory(paths: Paths, environ: Mapping[str, str] | None = None) -> Path:
    """Return the desk configuration directory for this user."""
    return settings_model.config_directory(paths, environ)


def themes_directory(paths: Paths, environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory that holds user theme manifests."""
    return config_directory(paths, environ) / "skins" / "appearance"


def presets_directory() -> Path:
    """Return the directory that ships desk's own theme presets."""
    return Path(__file__).resolve().parent / "presets"


def assets_directory(paths: Paths, name: str) -> Path:
    """Return the directory that holds downloaded assets of one theme."""
    return paths.xdg_data_home / APPLICATION_NAME / "assets" / name


def _state_home(paths: Paths) -> Path:
    """Return the per-user state directory for the applied theme."""
    return paths.home / ".local" / "state" / APPLICATION_NAME


def state_path(paths: Paths) -> Path:
    """Return the file that records the applied theme."""
    return _state_home(paths) / "theme.json"


def read_state(paths: Paths) -> ThemeState:
    """Return the recorded theme state, or an empty state."""
    path = state_path(paths)
    if not path.is_file():
        return ThemeState()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ThemeError(f"could not read {path}: {error}") from error
    try:
        record = json.loads(text)
    except json.JSONDecodeError as error:
        raise ThemeError(f"{path} is not valid JSON: {error}") from error
    return ThemeState.from_record(record, path)


def _write_state(paths: Paths, state: ThemeState) -> None:
    """Write the theme state atomically."""
    path = state_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_record(), indent=2, sort_keys=True) + "\n"
    temporary = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".theme.", suffix=".tmp", delete=False
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
        raise ThemeError(f"could not write {path}: {error}") from error


def _manifest_paths(paths: Paths, environ: Mapping[str, str] | None) -> tuple[Path, ...]:
    """Return the manifest files that may define a theme, user first."""
    directories = (themes_directory(paths, environ), presets_directory() / "appearance")
    found: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        found.extend(sorted(directory.glob(f"*{THEME_FILE_SUFFIX}")))
    return tuple(found)


def list_theme_names(paths: Paths, environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Return the available theme names, user themes first."""
    names: list[str] = []
    for path in _manifest_paths(paths, environ):
        name = path.stem
        if name not in names:
            names.append(name)
    return tuple(names)


def load_theme(paths: Paths, name: str, environ: Mapping[str, str] | None = None) -> Theme:
    """Read and validate one theme manifest."""
    if not THEME_NAME_PATTERN.fullmatch(name):
        raise ThemeError(f"{name!r} is not a theme name; use letters, digits, '.', '_', '+' or '-'")
    candidates = [
        themes_directory(paths, environ) / f"{name}{THEME_FILE_SUFFIX}",
        presets_directory() / "appearance" / f"{name}{THEME_FILE_SUFFIX}",
    ]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise ThemeError(
            f"there is no appearance named {name!r}; "
            "run 'desk skin appearance list' to see the manifests and presets"
        )
    text = _read_manifest(path)
    record = _load_yaml(text, path)
    if not isinstance(record, dict):
        raise ThemeError(f"{path} must contain a mapping")
    allowed = {"theme", "description", "components", "fonts", "assets", "packages"}
    unknown = sanitize.unknown_keys(record, allowed)
    if unknown:
        raise ThemeError(f"{path} uses unknown keys: {', '.join(unknown)}")
    declared = record.get("theme", name)
    if not isinstance(declared, str):
        raise ThemeError(f"{path} has a non-string 'theme'")
    if not THEME_NAME_PATTERN.fullmatch(declared):
        raise ThemeError(f"{path} has an invalid theme name {declared!r}")
    if declared != name:
        raise ThemeError(f"{path} declares the theme name {declared!r}; it must match the file name")
    description = record.get("description", "")
    if not isinstance(description, str):
        raise ThemeError(f"{path} has a non-string 'description'")
    components = _components(record.get("components"), path)
    fonts = _fonts(record.get("fonts"), path)
    assets = _assets(record.get("assets"), path)
    packages = _packages(record.get("packages"), path)
    return Theme(
        name=name,
        path=path,
        description=description,
        components=components,
        fonts=fonts,
        assets=assets,
        packages=packages,
    )


def _components(value: object, source: Path) -> Mapping[str, str]:
    """Validate the components mapping of a manifest."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ThemeError(f"{source} has a non-mapping 'components'")
    unknown = sanitize.unknown_keys(value, COMPONENT_KEYS)
    if unknown:
        raise ThemeError(f"{source} uses unknown components: {', '.join(unknown)}")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(item, str) or not item.strip():
            raise ThemeError(f"{source} has an invalid component {key!r}")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in item):
            raise ThemeError(f"{source} has a component with control characters")
        result[str(key)] = item
    return result


def _fonts(value: object, source: Path) -> Mapping[str, str]:
    """Validate the fonts mapping of a manifest."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ThemeError(f"{source} has a non-mapping 'fonts'")
    result: dict[str, str] = {}
    for key, item in value.items():
        if key not in FONT_KEYS:
            raise ThemeError(f"{source} uses an unknown font key {key!r}")
        if key in ("default_size", "monospace_size"):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not item > 0:
                raise ThemeError(f"{source} has an invalid font size {key!r}")
            result[str(key)] = str(item)
            continue
        if not isinstance(item, str) or not item.strip():
            raise ThemeError(f"{source} has an invalid font {key!r}")
        if any(ord(character) < 0x20 for character in item):
            raise ThemeError(f"{source} has a font with control characters")
        result[str(key)] = item
    return result


def _packages(value: object, path: Path) -> tuple[str, ...]:
    """Validate the optional list of Termux packages a theme needs."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ThemeError(f"{path} has a non-list 'packages'")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not PACKAGE_NAME_PATTERN.fullmatch(entry):
            raise ThemeError(f"{path} has an invalid package name {entry!r}")
        if entry not in names:
            names.append(entry)
    return tuple(names)


def _assets(value: object, source: Path) -> tuple[ThemeAsset, ...]:
    """Validate the assets list of a manifest."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ThemeError(f"{source} has a non-list 'assets'")
    result: list[ThemeAsset] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ThemeError(f"{source} has an asset that is not a mapping")
        allowed = {"kind", "target", "url", "path", "sha256", "pick"}
        unknown = sanitize.unknown_keys(entry, allowed)
        if unknown:
            raise ThemeError(f"{source} uses unknown asset keys: {', '.join(unknown)}")
        kind = entry.get("kind")
        if kind not in ASSET_KINDS:
            raise ThemeError(f"{source} uses an unknown asset kind {kind!r}")
        target = entry.get("target")
        if not isinstance(target, str):
            raise ThemeError(f"{source} has an asset without a target")
        if kind == "config":
            if target not in CONFIG_TARGETS:
                raise ThemeError(
                    f"{source} cannot replace {target!r}; a skin may only change "
                    + ", ".join(CONFIG_TARGETS)
                )
        elif not ASSET_TARGET_PATTERN.fullmatch(target):
            raise ThemeError(
                f"{source} has an asset without a usable target; "
                "use letters, digits, '.', '_', '+' or '-'"
            )
        url = entry.get("url")
        path = entry.get("path")
        if url is not None and path is not None:
            raise ThemeError(f"{source} gives both 'url' and 'path' for {target!r}")
        source_path = url if url is not None else path
        if not isinstance(source_path, str) or not source_path.strip():
            raise ThemeError(f"{source} has an asset without 'url' or 'path'")
        pick = entry.get("pick")
        if pick is not None:
            if not isinstance(pick, str) or not pick.strip():
                raise ThemeError(f"{source} has an empty 'pick' for {target!r}")
            pick = posixpath.normpath(pick.strip())
            if pick.startswith("/") or pick == ".." or pick.startswith("../"):
                raise ThemeError(f"{source} has a 'pick' that escapes the archive for {target!r}")
        sha = entry.get("sha256")
        if sha is not None:
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
                raise ThemeError(f"{source} has an invalid sha256 for {target!r}")
            sha = sha.lower()
        result.append(
            ThemeAsset(
                kind=str(kind),
                target=target,
                source=source_path,
                sha256=sha,
                is_url=url is not None,
                pick=pick,
            )
        )
    return tuple(result)


def run_theme_list(
    paths: Paths, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Print the available themes and mark the applied one."""
    state = read_state(paths)
    names = list_theme_names(paths, environ)
    if not names:
        print("No themes are available.", file=out)
        return 1
    invalid = 0
    for name in names:
        try:
            theme = load_theme(paths, name, environ)
        except ThemeError as error:
            invalid += 1
            print(f"{name}: invalid: {_printable(str(error))}", file=out)
            continue
        mark = "*" if state.active == name else " "
        description = f" - {theme.description}" if theme.description else ""
        location = "user" if theme.path.parent == themes_directory(paths, environ) else "preset"
        print(f"{mark} {_printable(name)} ({location}){_printable(description)}", file=out)
    return 1 if invalid else 0


def run_theme_show(
    paths: Paths, name: str, *, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Print one theme manifest."""
    theme = load_theme(paths, name, environ)
    try:
        print(theme.path.read_text(encoding="utf-8").rstrip(), file=out)
    except OSError as error:
        raise ThemeError(f"could not read {theme.path}: {error}") from error
    return 0


INIT_TEMPLATE = """\
# One XFCE appearance set.
theme: {name}
description: ""

components:
  gtk: Adwaita
  xfwm: Default
  icons: Adwaita
  cursors: Adwaita

fonts:
  default: "android:Noto Sans CJK SC"

# assets:
#   - kind: theme          # theme, icons, font or wallpaper
#     target: Nordic-darker
#     url: https://example.invalid/Nordic-darker.tar.gz
#     sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
"""


def run_theme_init(
    paths: Paths, name: str, *, force: bool, out: TextIO, environ: Mapping[str, str] | None = None
) -> int:
    """Create a starter manifest in the user theme directory."""
    if not THEME_NAME_PATTERN.fullmatch(name):
        raise ThemeError(f"{name!r} is not a theme name; use letters, digits, '.', '_', '+' or '-'")
    directory = themes_directory(paths, environ)
    path = directory / f"{name}{THEME_FILE_SUFFIX}"
    if path.exists() and not force:
        raise ThemeError(f"{path} already exists; pass --force to replace it")
    directory.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(INIT_TEMPLATE.format(name=name), encoding="utf-8")
    except OSError as error:
        raise ThemeError(f"could not write {path}: {error}") from error
    print(f"wrote {path}", file=out)
    print("edit the manifest and run 'desk skin appearance apply " + name + "'", file=out)
    return 0

