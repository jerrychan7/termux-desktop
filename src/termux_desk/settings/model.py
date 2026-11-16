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

ALLOWED_TOP_LEVEL = ("version", "apps", "vnc", "ime", "fonts")
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

def config_path(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the configuration file path for the current user."""
    environment = os.environ if environ is None else environ
    base = environment.get("XDG_CONFIG_HOME", "").strip()
    if base:
        if "\0" in base or _has_control(base):
            raise SettingsError("XDG_CONFIG_HOME contains control characters.")
        if not Path(base).is_absolute():
            raise SettingsError("XDG_CONFIG_HOME must be an absolute path.")
    directory = Path(base) if base else paths.home / ".config"
    return directory / CONFIG_DIRECTORY_NAME / CONFIG_FILE_NAME

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
