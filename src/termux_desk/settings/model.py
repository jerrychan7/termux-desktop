"""Configuration model, paths and small shared helpers."""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..config import APPLICATION_NAME, DeskError, Paths

CONFIG_VERSION = 1
CONFIG_FILE_NAME = "config.yaml"
DEFAULT_CONFIG_FILE_NAME = "default_config.yaml"
CONFIG_DIRECTORY_NAME = APPLICATION_NAME
PROFILES_DIRECTORY_NAME = "profiles"
PROFILE_ENVIRONMENT = "DESK_PROFILE"
DEFAULT_PROFILE = "default"
PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
THEME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

ALLOWED_TOP_LEVEL = ("version", "apps", "vnc", "ime", "fonts", "skin")
# 0.8.0 用这两个键选主题与布局；0.8.1 起改成 skin，仍接受以便迁移
LEGACY_TOP_LEVEL = ("theme", "layout")
ALLOWED_APP_KEYS = ("program", "args_prefix", "environment")
ALLOWED_VNC_KEYS = ("display", "geometry", "depth", "host", "port", "proxy", "view_only")
ALLOWED_IME_KEYS = ("enabled",)
ALLOWED_FONTS_KEYS = ("language", "sans_serif", "serif", "monospace")

DISPLAY_PATTERN = re.compile(r"^:[0-9]+$")
GEOMETRY_PATTERN = re.compile(r"^[1-9][0-9]*x[1-9][0-9]*$")
VNC_DEPTHS = (16, 24, 32)
VNC_PROXIES = ("rs", "py")
IME_MODES = ("auto", "true", "false")
FONT_LANGUAGES = ("zh-Hans", "zh-Hant", "zh-Hant-HK", "ja", "ko")

PYYAML_HINT = "run 'desk setup' to install it"
MAX_CONFIG_BYTES = 64 * 1024


class SettingsError(DeskError):
    """The configuration file cannot be read or does not validate."""


@dataclass(frozen=True)

class AppSettings:
    """The launch settings of one container application."""

    program: str | None = None
    args_prefix: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)

class VncSettings:
    """Defaults for the VNC commands."""

    display: str = ":1"
    geometry: str = "1280x720"
    depth: int = 24
    host: str = "127.0.0.1"
    port: int = 6080
    proxy: str = "rs"
    view_only: bool = False


@dataclass(frozen=True)

class ImeSettings:
    """Whether sessions enable the installed input method."""

    enabled: str = "auto"


@dataclass(frozen=True)

class FontsSettings:
    """Language and per-family overrides for the font preference."""

    language: str = "auto"
    sans_serif: str | None = None
    serif: str | None = None
    monospace: str | None = None


@dataclass(frozen=True)

class Settings:
    """The loaded configuration file."""

    version: int = CONFIG_VERSION
    apps: Mapping[str, AppSettings] = field(default_factory=dict)
    vnc: VncSettings = field(default_factory=VncSettings)
    ime: ImeSettings = field(default_factory=ImeSettings)
    fonts: FontsSettings = field(default_factory=FontsSettings)
    skin: str = ""
    legacy_theme: str = ""
    legacy_layout: str = ""

def config_directory(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the directory that holds the configuration and profiles."""
    environment = os.environ if environ is None else environ
    base = environment.get("XDG_CONFIG_HOME", "").strip()
    if base:
        if "\0" in base or _has_control(base):
            raise SettingsError("XDG_CONFIG_HOME contains control characters.")
        if not Path(base).is_absolute():
            raise SettingsError("XDG_CONFIG_HOME must be an absolute path.")
    root = Path(base) if base else paths.home / ".config"
    return root / CONFIG_DIRECTORY_NAME


def profile_name(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the profile selected by DESK_PROFILE, or None for the default."""
    environment = os.environ if environ is None else environ
    raw = environment.get(PROFILE_ENVIRONMENT, "").strip()
    if not raw or raw == DEFAULT_PROFILE:
        return None
    if not PROFILE_NAME_PATTERN.fullmatch(raw):
        raise SettingsError(
            f"{PROFILE_ENVIRONMENT} must be a profile name "
            "(letters, digits, dot, dash, underscore); "
            f"got {_printable(raw)!r}."
        )
    return raw


def profiles_directory(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the directory that holds named profiles."""
    return config_directory(paths, environ) / PROFILES_DIRECTORY_NAME


def config_path(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
    *,
    profile: str | None = None,
) -> Path:
    """Return the configuration file path for the current user."""
    environment = os.environ if environ is None else environ
    selected = profile if profile is not None else profile_name(environment)
    if selected is not None and not PROFILE_NAME_PATTERN.fullmatch(selected):
        raise SettingsError(
            f"{_printable(selected)!r} is not a valid profile name."
        )
    directory = config_directory(paths, environment)
    if selected is None:
        return directory / CONFIG_FILE_NAME
    return directory / PROFILES_DIRECTORY_NAME / f"{selected}.yaml"


def require_profile(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail when a named profile is selected but has no configuration file."""
    environment = os.environ if environ is None else environ
    selected = profile_name(environment)
    if selected is None:
        return
    path = config_path(paths, environment)
    if not path.is_file():
        raise SettingsError(
            f"the profile {_printable(selected)!r} has no configuration file at "
            f"{path}; run 'desk --profile {selected} config init' to create it."
        )


def list_profiles(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return the names of the profiles that have a configuration file."""
    directory = profiles_directory(paths, environ)
    try:
        entries = tuple(directory.iterdir())
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise SettingsError(
            f"could not read the profiles in {directory}: {error}"
        ) from error
    names: list[str] = []
    for entry in entries:
        if entry.is_symlink():
            continue
        if not entry.is_file() or entry.suffix != ".yaml":
            continue
        name = entry.stem
        if name == DEFAULT_PROFILE:
            continue
        if PROFILE_NAME_PATTERN.fullmatch(name):
            names.append(name)
    return tuple(sorted(names))

def default_config_path() -> Path:
    """Return the built-in configuration that ships with the package."""
    return Path(__file__).resolve().parent.parent / DEFAULT_CONFIG_FILE_NAME

def setting_for(settings: Settings, spec: str) -> AppSettings | None:
    """Return the settings of one container/app pair, if it has any."""
    return settings.apps.get(spec)

def _printable(value: str) -> str:
    """Escape control characters before printing untrusted configuration text."""
    pieces: list[str] = []
    for character in value:
        if character == "\n":
            pieces.append("\\n")
        elif character == "\t":
            pieces.append("\\t")
        elif character == "\r":
            pieces.append("\\r")
        elif (
            ord(character) < 32
            or 0x7F <= ord(character) <= 0x9F
            or unicodedata.category(character) in ("Cf", "Cs")
        ):
            code = ord(character)
            if code <= 0xFF:
                pieces.append(f"\\x{code:02x}")
            else:
                pieces.append(f"\\u{code:04x}")
        else:
            pieces.append(character)
    return "".join(pieces)

def _has_control(value: str) -> bool:
    return any(
        ord(character) < 32
        or 0x7F <= ord(character) <= 0x9F
        or unicodedata.category(character) in ("Cf", "Cs")
        for character in value
    )

def _has_surrogate(value: str) -> bool:
    return any(unicodedata.category(character) == "Cs" for character in value)
