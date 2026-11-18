"""Reading and validating the YAML configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..config import Paths
from ..container import NAME_PATTERN
from . import model, writing
from .model import (
    ALLOWED_APP_KEYS,
    ALLOWED_FONTS_KEYS,
    ALLOWED_IME_KEYS,
    ALLOWED_TOP_LEVEL,
    ALLOWED_VNC_KEYS,
    CONFIG_VERSION,
    DISPLAY_PATTERN,
    FONT_LANGUAGES,
    GEOMETRY_PATTERN,
    IME_MODES,
    LEGACY_TOP_LEVEL,
    THEME_NAME_PATTERN,
    VNC_DEPTHS,
    VNC_PROXIES,
    AppSettings,
    FontsSettings,
    ImeSettings,
    Settings,
    SettingsError,
    VncSettings,
    _has_control,
    _has_surrogate,
    _printable,
)


def load_settings(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    allow_missing_pyyaml: bool = False,
) -> Settings:
    """Read and validate the user file, or the built-in defaults."""
    selected = model.profile_name(environ)
    path = model.config_path(paths, environ)
    if selected is not None and not path.is_file():
        raise SettingsError(
            f"the profile {selected!r} has no configuration file at {path}; "
            f"run 'desk --profile {selected} config init' to create it."
        )
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    source = path
    if not path.is_file():
        source = model.default_config_path()
        if not source.is_file():
            raise SettingsError(
                f"the built-in configuration is missing at {source}; "
                "reinstall desk."
            )

    if allow_missing_pyyaml and not path.is_file():
        try:
            writing._import_yaml(source)
        except SettingsError:
            # A machine that has not run 'desk setup' yet can still start a
            # session with the empty defaults. An existing user file stays
            # strict because desk cannot honor overrides without PyYAML.
            return Settings()

    return _load_text(writing._read_text(source), source)

def _load_text(text: str, source: Path) -> Settings:
    yaml = writing._import_yaml(source)
    loader = writing._unique_key_loader(yaml)
    try:
        data = yaml.load(text, Loader=loader)
    except RecursionError as error:
        raise SettingsError(f"{source}: the YAML document is nested too deeply.") from error
    except yaml.YAMLError as error:
        raise writing._yaml_error(source, error) from error
    return _validate(data, source)

def _validate(data: object, path: Path) -> Settings:
    """Check the loaded document against the v1 schema."""
    if data is None:
        return Settings()
    document = _mapping(data, path, "the top level")
    if any(not isinstance(key, str) for key in document):
        raise SettingsError(f"{path}: every top-level key must be a string.")
    allowed = (*ALLOWED_TOP_LEVEL, *LEGACY_TOP_LEVEL)
    unknown = sorted(set(document) - set(allowed), key=str)
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) at the top level: {', '.join(unknown)}."
        )

    version = document.get("version", CONFIG_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != CONFIG_VERSION:
        raise SettingsError(
            f"{path}: version must be {CONFIG_VERSION}, not {version!r}."
        )

    raw_apps = document.get("apps", {})
    if raw_apps is None:
        raw_apps = {}
    apps_document = _mapping(raw_apps, path, "apps")
    apps: dict[str, AppSettings] = {}
    for spec, raw_entry in apps_document.items():
        if not isinstance(spec, str):
            raise SettingsError(f"{path}: every apps key must be a string.")
        _check_spec(spec, path)
        apps[spec] = _app_settings(raw_entry, path, spec)
    skin = _optional_name(document, "skin", path)
    legacy_theme = _optional_name(document, "theme", path)
    legacy_layout = _optional_name(document, "layout", path)
    vnc = _vnc_settings(document, path)
    ime = _ime_settings(document, path)
    fonts = _fonts_settings(document, path)
    return Settings(
        version=CONFIG_VERSION,
        apps=apps,
        vnc=vnc,
        ime=ime,
        fonts=fonts,
        skin=skin,
        legacy_theme=legacy_theme,
        legacy_layout=legacy_layout,
    )

def _optional_name(document: Mapping, key: str, path: Path) -> str:
    """Return one optional skin name, or an empty string."""
    value = document.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or not THEME_NAME_PATTERN.fullmatch(value):
        raise SettingsError(
            f"{path}: {key} must be a skin name like 'nord-dark'."
        )
    return value


def _mapping(value: object, path: Path, where: str) -> Mapping:
    if not isinstance(value, dict):
        raise SettingsError(f"{path}: {where} must be a mapping.")
    return value

def _check_spec(spec: str, path: Path) -> None:
    if _has_control(spec):
        raise SettingsError(
            f"{path}: the apps key {_printable(spec)!r} contains control characters."
        )
    container, separator, app = spec.partition("/")
    if not separator or not container or not app:
        raise SettingsError(
            f"{path}: the apps key {_printable(spec)!r} is not a 'container/app' pair."
        )
    if not NAME_PATTERN.fullmatch(container):
        raise SettingsError(
            f"{path}: {_printable(container)!r} is not a valid container name "
            f"in {_printable(spec)!r}."
        )
    if app.startswith("-"):
        raise SettingsError(
            f"{path}: the program name in {_printable(spec)!r} cannot start with '-'."
        )

def _file_program(entry: dict[str, object], path: Path, spec: str) -> str | None:
    program = entry.get("program")
    if program is None:
        return None
    if not isinstance(program, str) or not program.strip():
        raise SettingsError(f"{path}: apps[{spec!r}].program must be a non-empty string.")
    program = program.strip()
    if "\0" in program or program.startswith("-") or _has_control(program):
        raise SettingsError(
            f"{path}: apps[{_printable(spec)!r}].program cannot start with '-', "
            "contain NUL, or contain control characters."
        )
    return program

def _file_args_prefix(
    entry: dict[str, object], path: Path, spec: str
) -> tuple[str, ...]:
    raw_prefix = entry.get("args_prefix", [])
    if not isinstance(raw_prefix, list):
        raise SettingsError(f"{path}: apps[{spec!r}].args_prefix must be a list.")
    prefix: list[str] = []
    for item in raw_prefix:
        if not isinstance(item, str) or not item or "\0" in item:
            raise SettingsError(
                f"{path}: every apps[{spec!r}].args_prefix item must be a non-empty string."
            )
        prefix.append(item)
    return tuple(prefix)

def _file_environment_name(name: object, path: Path, spec: str) -> str:
    if not isinstance(name, str) or not name or "=" in name or "\0" in name:
        raise SettingsError(
            f"{path}: environment names in apps[{_printable(spec)!r}] must be "
            "non-empty and cannot contain '=' or NUL."
        )
    if any(character.isspace() for character in name) or name.startswith("-"):
        raise SettingsError(
            f"{path}: environment names in apps[{_printable(spec)!r}] cannot "
            "start with '-' or contain whitespace."
        )
    if _has_control(name):
        raise SettingsError(
            f"{path}: environment names in apps[{_printable(spec)!r}] cannot "
            "contain control characters."
        )
    return name

def _file_environment(
    entry: dict[str, object], path: Path, spec: str
) -> tuple[tuple[str, str], ...]:
    raw_environment = entry.get("environment", {})
    document = _mapping(raw_environment, path, f"apps[{spec!r}].environment")
    environment: list[tuple[str, str]] = []
    for name, value in document.items():
        name = _file_environment_name(name, path, spec)
        if not isinstance(value, str):
            raise SettingsError(
                f"{path}: apps[{_printable(spec)!r}].environment"
                f"[{_printable(name)!r}] must be a string; quote the value."
            )
        if "\0" in value or _has_surrogate(value):
            raise SettingsError(
                f"{path}: apps[{_printable(spec)!r}].environment"
                f"[{_printable(name)!r}] cannot contain NUL or invalid Unicode."
            )
        environment.append((name, value))
    return tuple(environment)

def _app_settings(raw: object, path: Path, spec: str) -> AppSettings:
    entry = _mapping(raw, path, f"apps[{spec!r}]")
    if any(not isinstance(key, str) for key in entry):
        raise SettingsError(f"{path}: every apps[{spec!r}] key must be a string.")
    unknown = sorted(set(entry) - set(ALLOWED_APP_KEYS), key=str)
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) in apps[{spec!r}]: {', '.join(unknown)}."
        )
    return AppSettings(
        program=_file_program(entry, path, spec),
        args_prefix=_file_args_prefix(entry, path, spec),
        environment=_file_environment(entry, path, spec),
    )

def _section(document: Mapping, key: str, path: Path) -> Mapping:
    raw = document.get(key, {})
    if raw is None:
        return {}
    return _mapping(raw, path, key)


def _unknown_keys(document: Mapping, allowed: tuple[str, ...], path: Path, where: str) -> None:
    if any(not isinstance(key, str) for key in document):
        raise SettingsError(f"{path}: every key in {where} must be a string.")
    unknown = sorted(set(document) - set(allowed), key=str)
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) in {where}: {_printable(', '.join(unknown))}."
        )


def _vnc_settings(document: Mapping, path: Path) -> VncSettings:
    entry = _section(document, "vnc", path)
    _unknown_keys(entry, ALLOWED_VNC_KEYS, path, "vnc")

    display = entry.get("display", ":1")
    if not isinstance(display, str) or not DISPLAY_PATTERN.fullmatch(display):
        raise SettingsError(f"{path}: vnc.display must look like ':1'.")
    geometry = entry.get("geometry", "1280x720")
    if not isinstance(geometry, str) or not GEOMETRY_PATTERN.fullmatch(geometry):
        raise SettingsError(
            f"{path}: vnc.geometry must look like '1280x720'."
        )
    depth = entry.get("depth", 24)
    if isinstance(depth, bool) or not isinstance(depth, int) or depth not in VNC_DEPTHS:
        raise SettingsError(
            f"{path}: vnc.depth must be one of {', '.join(str(item) for item in VNC_DEPTHS)}."
        )
    host = entry.get("host", "127.0.0.1")
    if not isinstance(host, str) or not host or _has_control(host) or "\0" in host:
        raise SettingsError(f"{path}: vnc.host must be a non-empty host name.")
    port = entry.get("port", 6080)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SettingsError(f"{path}: vnc.port must be an integer between 1 and 65535.")
    proxy = entry.get("proxy", "rs")
    if not isinstance(proxy, str) or proxy not in VNC_PROXIES:
        raise SettingsError(f"{path}: vnc.proxy must be 'rs' or 'py'.")
    view_only = entry.get("view_only", False)
    if not isinstance(view_only, bool):
        raise SettingsError(f"{path}: vnc.view_only must be true or false.")
    return VncSettings(
        display=display,
        geometry=geometry,
        depth=depth,
        host=host,
        port=port,
        proxy=proxy,
        view_only=view_only,
    )


def _ime_settings(document: Mapping, path: Path) -> ImeSettings:
    entry = _section(document, "ime", path)
    _unknown_keys(entry, ALLOWED_IME_KEYS, path, "ime")
    enabled = entry.get("enabled", "auto")
    if isinstance(enabled, bool):
        enabled = "true" if enabled else "false"
    if not isinstance(enabled, str) or enabled not in IME_MODES:
        raise SettingsError(
            f"{path}: ime.enabled must be 'auto', 'true', or 'false'."
        )
    return ImeSettings(enabled=enabled)


def _font_family(entry: Mapping, key: str, path: Path) -> str | None:
    family = entry.get(key)
    if family is None:
        return None
    if not isinstance(family, str) or not family.strip():
        raise SettingsError(f"{path}: fonts.{key} must be a non-empty string.")
    family = family.strip()
    if _has_control(family) or "\0" in family:
        raise SettingsError(f"{path}: fonts.{key} cannot contain control characters.")
    return family


def _fonts_settings(document: Mapping, path: Path) -> FontsSettings:
    entry = _section(document, "fonts", path)
    _unknown_keys(entry, ALLOWED_FONTS_KEYS, path, "fonts")
    language = entry.get("language", "auto")
    if not isinstance(language, str) or (
        language != "auto" and language not in FONT_LANGUAGES
    ):
        raise SettingsError(
            f"{path}: fonts.language must be 'auto' or one of "
            f"{', '.join(FONT_LANGUAGES)}."
        )
    return FontsSettings(
        language=language,
        sans_serif=_font_family(entry, "sans_serif", path),
        serif=_font_family(entry, "serif", path),
        monospace=_font_family(entry, "monospace", path),
    )
