"""The user-facing config commands."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, TextIO

from ..config import Paths
from . import loading, model, writing
from .loading import _check_spec, load_settings
from .model import (
    MAX_CONFIG_BYTES,
    AppSettings,
    Settings,
    SettingsError,
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
    if not settings.apps:
        print("apps: (none)", file=out)
        return
    print("apps:", file=out)
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
    load_settings(paths, environ=environ)
    print(f"{path} is valid.", file=out)
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
