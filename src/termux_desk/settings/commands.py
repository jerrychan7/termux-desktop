"""The user-facing config commands."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, TextIO

from ..config import Paths
from . import loading, model, writing
from .loading import _check_spec, load_settings
from .model import (
    FONT_LANGUAGES,
    IME_MODES,
    MAX_CONFIG_BYTES,
    THEME_NAME_PATTERN,
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


def run_config_path(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Print the configuration file path."""
    print(_printable(str(model.config_path(paths, environ))), file=out)

def run_config_profiles(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Print the active profile and the profiles that have a file."""
    selected = model.profile_name(environ)
    path = model.config_path(paths, environ)
    if selected is None:
        print("Active profile: default", file=out)
    else:
        print(
            f"Active profile: {_printable(selected)} "
            f"({_printable(str(path))})",
            file=out,
        )
    if selected is not None and not path.is_file():
        print(
            f"The active profile {_printable(selected)} has no configuration file yet; "
            f"run 'desk --profile {selected} config init' to create it.",
            file=out,
        )
    profiles = model.list_profiles(paths, environ)
    if not profiles:
        print("No named profiles are configured.", file=out)
        print(
            f"Create one with 'desk --profile NAME config init' "
            f"in {_printable(str(model.profiles_directory(paths, environ)))}.",
            file=out,
        )
        return
    print("Profiles:", file=out)
    for name in profiles:
        marker = "*" if name == selected else " "
        print(f" {marker} {_printable(name)}", file=out)

def run_config_init(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    force: bool = False,
    out: TextIO,
) -> None:
    """Copy the built-in configuration to the user configuration path."""
    path = model.config_path(paths, environ)
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    if path.exists() and not force:
        raise SettingsError(
            f"{path} already exists; use 'desk config init --force' to replace it."
        )
    source = model.default_config_path()
    if not source.is_file():
        raise SettingsError(
            f"the built-in configuration is missing at {source}; reinstall desk."
        )
    text = writing._read_text(source)
    loading._load_text(text, source)
    if path.is_file():
        writing._backup_file(path)
        print(
            f"Backed up the previous file to {_printable(str(writing._backup_path(path)))}.",
            file=out,
        )
    writing._write_text_atomic(path, text)
    print(f"Wrote {_printable(str(path))}.", file=out)
    print("Edit it, then run 'desk config check'.", file=out)

def run_config_set_app(
    paths: Paths,
    *,
    spec: str,
    field_name: str,
    value: str | None = None,
    arguments: tuple[str, ...] = (),
    environment_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Set one launch override in the user configuration file."""
    path = model.config_path(paths, environ)
    _check_spec(spec, path)
    if field_name == "program":
        assert value is not None
        program = _checked_program(value, path)
        settings = load_settings(paths, environ=environ)
        existed = spec in settings.apps
        entry = settings.apps.get(spec, AppSettings())
        apps = dict(settings.apps)
        apps[spec] = AppSettings(
            program=program,
            args_prefix=entry.args_prefix,
            environment=entry.environment,
        )
        detail = f"apps.{spec}.program"
    elif field_name == "args-prefix":
        prefix = tuple(arguments)
        if prefix and prefix[0] == "--":
            prefix = prefix[1:]
        if not prefix:
            raise SettingsError(
                "args-prefix needs at least one argument; "
                "use 'desk config unset app CONTAINER/APP args-prefix' to clear it."
            )
        for argument in prefix:
            if not argument or "\0" in argument or _has_surrogate(argument):
                raise SettingsError(
                    "every args-prefix argument must be non-empty and valid Unicode."
                )
        settings = load_settings(paths, environ=environ)
        existed = spec in settings.apps
        entry = settings.apps.get(spec, AppSettings())
        apps = dict(settings.apps)
        apps[spec] = AppSettings(
            program=entry.program,
            args_prefix=prefix,
            environment=entry.environment,
        )
        detail = f"apps.{spec}.args_prefix"
    elif field_name == "environment":
        assert environment_name is not None and value is not None
        name = _checked_environment_name(environment_name, path)
        if "\0" in value or _has_surrogate(value):
            raise SettingsError(
                "an environment value cannot contain NUL or invalid Unicode."
            )
        settings = load_settings(paths, environ=environ)
        existed = spec in settings.apps
        entry = settings.apps.get(spec, AppSettings())
        environment = dict(entry.environment)
        environment[name] = value
        apps = dict(settings.apps)
        apps[spec] = AppSettings(
            program=entry.program,
            args_prefix=entry.args_prefix,
            environment=tuple(sorted(environment.items())),
        )
        detail = f"apps.{spec}.environment.{name}"
    else:
        raise SettingsError(f"unknown setting: {field_name}")

    verb = "Updated" if existed else "Added"
    _write_apps(
        paths, path, apps, settings, environ, out, f"{verb} {detail} in {path}."
    )

def run_config_unset_app(
    paths: Paths,
    *,
    spec: str,
    field_name: str,
    environment_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Remove one launch override, leaving the built-in defaults for it."""
    path = model.config_path(paths, environ)
    _check_spec(spec, path)
    settings = load_settings(paths, environ=environ)
    if spec not in settings.apps:
        print("Nothing changed. The application has no override.", file=out)
        return
    entry = settings.apps[spec]
    apps = dict(settings.apps)
    if field_name == "program":
        if entry.program is None:
            print("Nothing changed. The application has no program override.", file=out)
            return
        updated = AppSettings(
            program=None,
            args_prefix=entry.args_prefix,
            environment=entry.environment,
        )
        detail = f"apps.{spec}.program"
    elif field_name == "args-prefix":
        if not entry.args_prefix:
            print("Nothing changed. The application has no argument prefix.", file=out)
            return
        updated = AppSettings(
            program=entry.program,
            args_prefix=(),
            environment=entry.environment,
        )
        detail = f"apps.{spec}.args_prefix"
    elif field_name == "environment":
        if environment_name is None:
            raise SettingsError("environment needs a variable name.")
        name = _checked_environment_name(environment_name, path)
        environment = dict(entry.environment)
        if name not in environment:
            print("Nothing changed. The variable is not overridden.", file=out)
            return
        del environment[name]
        updated = AppSettings(
            program=entry.program,
            args_prefix=entry.args_prefix,
            environment=tuple(sorted(environment.items())),
        )
        detail = f"apps.{spec}.environment.{name}"
    else:
        raise SettingsError(f"unknown setting: {field_name}")

    if updated.program is None and not updated.args_prefix and not updated.environment:
        del apps[spec]
    else:
        apps[spec] = updated
    _write_apps(
        paths, path, apps, settings, environ, out, f"Removed {detail} from {path}."
    )

MIGRATION_HINT = (
    "create a skin with 'desk skin init NAME', select it with "
    "'desk config set skin NAME', then remove these keys with "
    "'desk config unset theme' and 'desk config unset layout'"
)


def run_config_show(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Print the effective configuration and where it came from."""
    path = model.config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    state = "present" if path.is_file() else "not present; using the built-in defaults"
    print(f"config: {_printable(str(path))} ({state})", file=out)
    print(f"version: {settings.version}", file=out)
    print(f"skin: {_printable(settings.skin) if settings.skin else '(none)'}", file=out)
    for key, value in (
        ("theme", settings.legacy_theme),
        ("layout", settings.legacy_layout),
    ):
        if value:
            print(
                f"{key}: {_printable(value)} (0.8.0 key; {MIGRATION_HINT})",
                file=out,
            )
    if settings.apps:
        print("apps:", file=out)
    else:
        print("apps: (none)", file=out)
    for spec in sorted(settings.apps):
        entry = settings.apps[spec]
        print(f"  {_printable(spec)}:", file=out)
        if entry.program:
            print(f"    program: {_printable(entry.program)}", file=out)
        if entry.args_prefix:
            print(
                f"    args_prefix: {' '.join(_printable(item) for item in entry.args_prefix)}",
                file=out,
            )
        if entry.environment:
            assignments = ", ".join(
                f"{_printable(name)}={_printable(value)}"
                for name, value in entry.environment
            )
            print(f"    environment: {assignments}", file=out)
    vnc = settings.vnc
    print("vnc:", file=out)
    print(f"  display: {_printable(vnc.display)}", file=out)
    print(f"  geometry: {_printable(vnc.geometry)}", file=out)
    print(f"  depth: {vnc.depth}", file=out)
    print(f"  host: {_printable(vnc.host)}", file=out)
    print(f"  port: {vnc.port}", file=out)
    print(f"  proxy: {_printable(vnc.proxy)}", file=out)
    print(f"  view_only: {str(vnc.view_only).lower()}", file=out)
    print("ime:", file=out)
    print(f"  enabled: {_printable(settings.ime.enabled)}", file=out)
    fonts = settings.fonts
    print("fonts:", file=out)
    print(f"  language: {_printable(fonts.language)}", file=out)
    for key in ("sans_serif", "serif", "monospace"):
        family = getattr(fonts, key)
        if family is not None:
            print(f"  {key}: {_printable(family)}", file=out)

def run_config_check(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> int:
    """Validate the configuration file and report the result."""
    path = model.config_path(paths, environ)
    if not path.is_file():
        load_settings(paths, environ=environ)
        print(
            f"There is no configuration file at {path}; "
            "the built-in defaults are valid. Run 'desk config init' to copy them.",
            file=out,
        )
        return 0
    settings = load_settings(paths, environ=environ)
    print(f"{path} is valid.", file=out)
    if settings.legacy_theme or settings.legacy_layout:
        print(
            f"warning: this file still selects a theme or a layout with the 0.8.0 keys; "
            f"{MIGRATION_HINT}",
            file=out,
        )
    return 0

def _write_apps(
    paths: Paths,
    path: Path,
    apps: Mapping[str, AppSettings],
    current: Settings,
    environ: Mapping[str, str] | None,
    out: TextIO,
    message: str,
) -> None:
    if _canonical(apps) == _canonical(current.apps):
        print("Nothing changed. The configuration file was not rewritten.", file=out)
        return
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    text = writing._read_config_text(path, environ)
    newline = writing._newline_style(text)
    updated = writing._replace_apps_section(text, writing._render_apps(apps, newline), path)
    encoded = updated.encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise SettingsError(
            f"{path}: the new configuration would be {len(encoded)} bytes; "
            f"the limit is {MAX_CONFIG_BYTES} bytes."
        )
    # Never write text that desk itself would refuse to load.
    loading._load_text(updated, path)
    if path.is_file():
        writing._backup_file(path)
    writing._write_text_atomic(path, updated)
    print(_printable(message), file=out)

def _checked_program(value: str, path: Path) -> str:
    program = value.strip()
    if not program:
        raise SettingsError(f"{path}: program must not be empty.")
    if program.startswith("-"):
        raise SettingsError(f"{path}: program cannot start with '-'.")
    if _has_control(program):
        raise SettingsError(f"{path}: program cannot contain control characters.")
    return program

def _checked_environment_name(name: str, path: Path) -> str:
    if not name or name.startswith("-") or "=" in name or "\0" in name:
        raise SettingsError(
            f"{path}: environment names must be non-empty and cannot start with '-', "
            "or contain '=' or NUL."
        )
    if any(character.isspace() for character in name):
        raise SettingsError(f"{path}: environment names cannot contain whitespace.")
    if _has_control(name):
        raise SettingsError(f"{path}: environment names cannot contain control characters.")
    return name

def _canonical(apps: Mapping[str, AppSettings]) -> tuple:
    return tuple(
        (
            spec,
            entry.program,
            tuple(entry.args_prefix),
            tuple(sorted(entry.environment)),
        )
        for spec, entry in sorted(apps.items())
    )

_SECTION_RENDERERS = {
    "vnc": writing._render_vnc,
    "ime": writing._render_ime,
    "fonts": writing._render_fonts,
}

SECTION_DEFAULTS = {
    "vnc": VncSettings(),
    "ime": ImeSettings(),
    "fonts": FontsSettings(),
}


def _parse_vnc_value(key: str, value: str):
    if key in ("display", "geometry", "host", "proxy"):
        return value.strip()
    if key in ("depth", "port"):
        try:
            return int(value, 10)
        except ValueError as error:
            raise SettingsError(f"{key} must be an integer, not {value!r}.") from error
    if key == "view_only":
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise SettingsError("view_only must be true or false.")
    raise SettingsError(f"unknown vnc setting: {key}")


def _parse_section_value(section: str, key: str, value: str):
    if section == "vnc":
        return _parse_vnc_value(key, value)
    if section == "ime":
        if key != "enabled":
            raise SettingsError(f"unknown ime setting: {key}")
        if value.strip().lower() not in IME_MODES:
            raise SettingsError("ime.enabled must be auto, true, or false.")
        return value.strip().lower()
    if section == "fonts":
        if key == "language":
            if value.strip() not in ("auto", *FONT_LANGUAGES):
                raise SettingsError(
                    f"fonts.language must be auto or one of {', '.join(FONT_LANGUAGES)}."
                )
            return value.strip()
        if key in ("sans_serif", "serif", "monospace"):
            family = value.strip()
            if not family or _has_control(family) or "\0" in family:
                raise SettingsError(f"fonts.{key} must be a non-empty font family.")
            return family
        raise SettingsError(f"unknown fonts setting: {key}")
    raise SettingsError(f"unknown section: {section}")


def _section_mapping(section: str, current) -> dict[str, object]:
    if section == "vnc":
        return {
            "display": current.display,
            "geometry": current.geometry,
            "depth": current.depth,
            "host": current.host,
            "port": current.port,
            "proxy": current.proxy,
            "view_only": current.view_only,
        }
    if section == "ime":
        return {"enabled": current.enabled}
    return {
        "language": current.language,
        "sans_serif": current.sans_serif,
        "serif": current.serif,
        "monospace": current.monospace,
    }


def _write_section(
    paths: Paths,
    path: Path,
    section: str,
    value,
    current,
    environ: Mapping[str, str] | None,
    out: TextIO,
    message: str,
) -> None:
    if value == current:
        print("Nothing changed. The configuration file was not rewritten.", file=out)
        return
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    text = writing._read_config_text(path, environ)
    newline = writing._newline_style(text)
    rendered = _SECTION_RENDERERS[section](value, newline)
    updated = writing._replace_section(text, section, rendered, path)
    encoded = updated.encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise SettingsError(
            f"{path}: the new configuration would be {len(encoded)} bytes; "
            f"the limit is {MAX_CONFIG_BYTES} bytes."
        )
    loading._load_text(updated, path)
    if path.is_file():
        writing._backup_file(path)
    writing._write_text_atomic(path, updated)
    print(_printable(message), file=out)


def run_config_set_section(
    paths: Paths,
    *,
    section: str,
    key: str,
    value: str,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Set one scalar option in the vnc, ime, or fonts section."""
    path = model.config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    current = getattr(settings, section)
    parsed = _parse_section_value(section, key, value)
    mapping = _section_mapping(section, current)
    mapping[key] = parsed
    updated = replace(current, **mapping)
    _write_section(
        paths,
        path,
        section,
        updated,
        current,
        environ,
        out,
        f"Updated {section}.{key} in {path}.",
    )


def run_config_unset_section(
    paths: Paths,
    *,
    section: str,
    key: str,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Reset one scalar option to its built-in default."""
    path = model.config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    current = getattr(settings, section)
    default = getattr(SECTION_DEFAULTS[section], key)
    mapping = _section_mapping(section, current)
    mapping[key] = default
    updated = replace(current, **mapping)
    _write_section(
        paths,
        path,
        section,
        updated,
        current,
        environ,
        out,
        f"Reset {section}.{key} to its default in {path}.",
    )

def _checked_name(value: str, key: str) -> str:
    """Validate a skin name from the command line."""
    if not THEME_NAME_PATTERN.fullmatch(value):
        raise SettingsError(
            f"{value!r} is not a valid {key} name; use letters, digits, '.', '_', '+' or '-'."
        )
    return value


def _write_name(
    paths: Paths,
    path: Path,
    key: str,
    value: str,
    environ: Mapping[str, str] | None,
    out: TextIO,
    message: str,
) -> None:
    """Set or remove the top-level skin scalar."""
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    text = writing._read_config_text(path, environ)
    rendered = f"{key}: {writing._yaml_string(value)}\n" if value else ""
    updated = writing._replace_section(text, key, rendered, path)
    encoded = updated.encode("utf-8")
    if len(encoded) > MAX_CONFIG_BYTES:
        raise SettingsError(
            f"{path}: the new configuration would be {len(encoded)} bytes; "
            f"the limit is {MAX_CONFIG_BYTES} bytes."
        )
    loading._load_text(updated, path)
    if path.is_file():
        writing._backup_file(path)
    writing._write_text_atomic(path, updated)
    print(_printable(message), file=out)


def run_config_set_name(
    paths: Paths,
    *,
    key: str,
    value: str,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Select the skin that a profile names."""
    if key != "skin":
        raise SettingsError(f"cannot set {key!r}; expected skin")
    value = _checked_name(value, key)
    path = model.config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    current = getattr(settings, key)
    if value == current:
        print("Nothing changed. The configuration file was not rewritten.", file=out)
        return
    _write_name(
        paths,
        path,
        key,
        value,
        environ,
        out,
        f"Selected skin {value!r} in {path}.",
    )


def run_config_unset_name(
    paths: Paths,
    *,
    key: str,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Reset the selected skin, or drop one 0.8.0 theme/layout key."""
    if key not in ("skin", "theme", "layout"):
        raise SettingsError(f"cannot unset {key!r}; expected skin, theme or layout")
    path = model.config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    attribute = {"skin": "skin", "theme": "legacy_theme", "layout": "legacy_layout"}[key]
    if not getattr(settings, attribute):
        print("Nothing changed. The configuration file was not rewritten.", file=out)
        return
    message = (
        f"Reset skin to its default in {path}."
        if key == "skin"
        else f"Removed the 0.8.0 {key} key from {path}."
    )
    _write_name(paths, path, key, "", environ, out, message)

