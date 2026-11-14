"""YAML configuration for desk.

The user file belongs to the user: desk only reads it, so comments and layout
stay as written. When the file does not exist, desk reads the commented
built-in copy (``default_config.yaml``) instead; the defaults therefore live
in one YAML document rather than in Python code. PyYAML is a normal
dependency of the configuration layer and is installed by ``desk setup``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

from .config import APPLICATION_NAME, DeskError, Paths
from .container import NAME_PATTERN

CONFIG_VERSION = 1
CONFIG_FILE_NAME = "config.yaml"
DEFAULT_CONFIG_FILE_NAME = "default_config.yaml"
CONFIG_DIRECTORY_NAME = APPLICATION_NAME

ALLOWED_TOP_LEVEL = ("version", "apps")
ALLOWED_APP_KEYS = ("program", "args_prefix", "environment")

PYYAML_HINT = "run 'desk setup' to install it"


class SettingsError(DeskError):
    """The configuration file cannot be read or does not validate."""


@dataclass(frozen=True)
class AppSettings:
    """The launch settings of one container application."""

    program: str | None = None
    args_prefix: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Settings:
    """The loaded configuration file."""

    version: int = CONFIG_VERSION
    apps: Mapping[str, AppSettings] = field(default_factory=dict)


def config_path(
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the configuration file path for the current user."""
    environment = os.environ if environ is None else environ
    base = environment.get("XDG_CONFIG_HOME", "").strip()
    directory = Path(base) if base else paths.home / ".config"
    return directory / CONFIG_DIRECTORY_NAME / CONFIG_FILE_NAME


def default_config_path() -> Path:
    """Return the built-in configuration that ships with the package."""
    return Path(__file__).resolve().with_name(DEFAULT_CONFIG_FILE_NAME)


def load_settings(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Read and validate the user file, or the built-in defaults."""
    path = config_path(paths, environ)
    source = path
    if not path.is_file():
        source = default_config_path()
        if not source.is_file():
            raise SettingsError(
                f"the built-in configuration is missing at {source}; "
                "reinstall desk."
            )

    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise SettingsError(f"could not read {source}: {error}") from error

    yaml = _import_yaml(source)
    loader = _unique_key_loader(yaml)
    try:
        data = yaml.load(text, Loader=loader)
    except yaml.YAMLError as error:
        raise SettingsError(f"{source} is not valid YAML: {error}") from error
    return _validate(data, source)


def setting_for(settings: Settings, spec: str) -> AppSettings | None:
    """Return the settings of one container/app pair, if it has any."""
    return settings.apps.get(spec)


def run_config_path(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Print the configuration file path."""
    print(config_path(paths, environ), file=out)


def run_config_show(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> None:
    """Print the effective configuration and where it came from."""
    path = config_path(paths, environ)
    settings = load_settings(paths, environ=environ)
    state = "present" if path.is_file() else "not present; using the built-in defaults"
    print(f"config: {path} ({state})", file=out)
    print(f"version: {settings.version}", file=out)
    if not settings.apps:
        print("apps: (none)", file=out)
        return
    print("apps:", file=out)
    for spec in sorted(settings.apps):
        entry = settings.apps[spec]
        print(f"  {spec}:", file=out)
        if entry.program:
            print(f"    program: {entry.program}", file=out)
        if entry.args_prefix:
            print(f"    args_prefix: {' '.join(entry.args_prefix)}", file=out)
        for name, value in entry.environment:
            print(f"    environment: {name}={value}", file=out)


def run_config_check(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO,
) -> int:
    """Validate the configuration file and report the result."""
    path = config_path(paths, environ)
    if not path.is_file():
        load_settings(paths, environ=environ)
        print(
            f"There is no configuration file at {path}; "
            "the built-in defaults are valid.",
            file=out,
        )
        return 0
    load_settings(paths, environ=environ)
    print(f"{path} is valid.", file=out)
    return 0


def _import_yaml(path: Path):
    """Import PyYAML on demand, with a hint when it is missing."""
    try:
        import yaml
    except ImportError as error:
        raise SettingsError(
            f"{path} needs PyYAML; {PYYAML_HINT}."
        ) from error
    return yaml


def _unique_key_loader(yaml):
    """Return a safe loader that rejects duplicate mapping keys."""

    class UniqueKeyLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            if not isinstance(node, yaml.MappingNode):
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"expected a mapping node, but found {node.id}",
                    node.start_mark,
                )
            seen: set[object] = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)
            return super().construct_mapping(node, deep=deep)

    return UniqueKeyLoader


def _validate(data: object, path: Path) -> Settings:
    """Check the loaded document against the v1 schema."""
    if data is None:
        return Settings()
    document = _mapping(data, path, "the top level")
    unknown = sorted(set(document) - set(ALLOWED_TOP_LEVEL))
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) at the top level: {', '.join(map(str, unknown))}."
        )

    version = document.get("version", CONFIG_VERSION)
    if isinstance(version, bool) or version != CONFIG_VERSION:
        raise SettingsError(
            f"{path}: version must be {CONFIG_VERSION}, not {version!r}."
        )

    raw_apps = document.get("apps", {})
    apps_document = _mapping(raw_apps, path, "apps")
    apps: dict[str, AppSettings] = {}
    for spec, raw_entry in apps_document.items():
        if not isinstance(spec, str):
            raise SettingsError(f"{path}: every apps key must be a string.")
        _check_spec(spec, path)
        apps[spec] = _app_settings(raw_entry, path, spec)
    return Settings(version=CONFIG_VERSION, apps=apps)


def _mapping(value: object, path: Path, where: str) -> Mapping:
    if not isinstance(value, dict):
        raise SettingsError(f"{path}: {where} must be a mapping.")
    return value


def _check_spec(spec: str, path: Path) -> None:
    container, separator, app = spec.partition("/")
    if not separator or not container or not app:
        raise SettingsError(
            f"{path}: the apps key {spec!r} is not a 'container/app' pair."
        )
    if not NAME_PATTERN.fullmatch(container):
        raise SettingsError(
            f"{path}: {container!r} is not a valid container name in {spec!r}."
        )
    if app.startswith("-"):
        raise SettingsError(
            f"{path}: the program name in {spec!r} cannot start with '-'."
        )


def _app_settings(raw: object, path: Path, spec: str) -> AppSettings:
    entry = _mapping(raw, path, f"apps[{spec!r}]")
    unknown = sorted(set(entry) - set(ALLOWED_APP_KEYS))
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) in apps[{spec!r}]: {', '.join(map(str, unknown))}."
        )

    program = entry.get("program")
    if program is not None:
        if not isinstance(program, str) or not program.strip():
            raise SettingsError(f"{path}: apps[{spec!r}].program must be a non-empty string.")
        if "\0" in program or program.startswith("-"):
            raise SettingsError(
                f"{path}: apps[{spec!r}].program cannot start with '-' or contain NUL."
            )
        program = program.strip()

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

    raw_environment = entry.get("environment", {})
    environment_document = _mapping(
        raw_environment, path, f"apps[{spec!r}].environment"
    )
    environment: list[tuple[str, str]] = []
    for name, value in environment_document.items():
        if not isinstance(name, str) or not name or "=" in name or "\0" in name:
            raise SettingsError(
                f"{path}: environment names in apps[{spec!r}] must be non-empty and "
                "cannot contain '=' or NUL."
            )
        if not isinstance(value, str):
            raise SettingsError(
                f"{path}: apps[{spec!r}].environment[{name!r}] must be a string; "
                "quote the value."
            )
        if "\0" in value:
            raise SettingsError(
                f"{path}: apps[{spec!r}].environment[{name!r}] cannot contain NUL."
            )
        environment.append((name, value))

    return AppSettings(
        program=program,
        args_prefix=tuple(prefix),
        environment=tuple(environment),
    )
