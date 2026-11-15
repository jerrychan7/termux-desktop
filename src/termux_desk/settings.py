"""YAML configuration for desk.

The user file belongs to the user: desk reads it, and the ``config`` commands
that write it only touch the ``apps`` section. When the file does not exist,
desk reads the commented built-in copy (``default_config.yaml``) instead; the
defaults therefore live in one YAML document rather than in Python code.
PyYAML is a normal dependency of the configuration layer and is installed by
``desk setup``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Hashable, Mapping, TextIO

from .config import APPLICATION_NAME, DeskError, Paths
from .container import NAME_PATTERN

CONFIG_VERSION = 1
CONFIG_FILE_NAME = "config.yaml"
DEFAULT_CONFIG_FILE_NAME = "default_config.yaml"
CONFIG_DIRECTORY_NAME = APPLICATION_NAME

ALLOWED_TOP_LEVEL = ("version", "apps")
ALLOWED_APP_KEYS = ("program", "args_prefix", "environment")

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
    if base and not Path(base).is_absolute():
        raise SettingsError("XDG_CONFIG_HOME must be an absolute path.")
    directory = Path(base) if base else paths.home / ".config"
    return directory / CONFIG_DIRECTORY_NAME / CONFIG_FILE_NAME


def default_config_path() -> Path:
    """Return the built-in configuration that ships with the package."""
    return Path(__file__).resolve().with_name(DEFAULT_CONFIG_FILE_NAME)


def load_settings(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    allow_missing_pyyaml: bool = False,
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

    if allow_missing_pyyaml and not path.is_file():
        try:
            _import_yaml(source)
        except SettingsError:
            # A machine that has not run 'desk setup' yet can still start a
            # session with the empty defaults. An existing user file stays
            # strict because desk cannot honor overrides without PyYAML.
            return Settings()

    return _load_text(_read_text(source), source)


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


def run_config_init(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    force: bool = False,
    out: TextIO,
) -> None:
    """Copy the built-in configuration to the user configuration path."""
    path = config_path(paths, environ)
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    if path.exists() and not force:
        raise SettingsError(
            f"{path} already exists; use 'desk config init --force' to replace it."
        )
    source = default_config_path()
    if not source.is_file():
        raise SettingsError(
            f"the built-in configuration is missing at {source}; reinstall desk."
        )
    text = _read_text(source)
    _load_text(text, source)
    if path.is_file():
        _backup_file(path)
        print(f"Backed up the previous file to {_backup_path(path)}.", file=out)
    _write_text_atomic(path, text)
    print(f"Wrote {path}.", file=out)
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
    path = config_path(paths, environ)
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
            if not argument or "\0" in argument:
                raise SettingsError("every args-prefix argument must be non-empty.")
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
        if "\0" in value:
            raise SettingsError("an environment value cannot contain NUL.")
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
    _write_apps(paths, path, apps, environ, out, f"{verb} {detail} in {path}.")


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
    path = config_path(paths, environ)
    _check_spec(spec, path)
    settings = load_settings(paths, environ=environ)
    if spec not in settings.apps:
        print("Nothing changed. The application has no override.", file=out)
        return
    entry = settings.apps[spec]
    apps = dict(settings.apps)
    if field_name == "program":
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
        paths, path, apps, environ, out, f"Removed {detail} from {path}."
    )


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
    print(f"config: {_printable(str(path))} ({state})", file=out)
    print(f"version: {settings.version}", file=out)
    if not settings.apps:
        print("apps: (none)", file=out)
        return
    print("apps:", file=out)
    for spec in sorted(settings.apps):
        entry = settings.apps[spec]
        print(f"  {spec}:", file=out)
        if entry.program:
            print(f"    program: {_printable(entry.program)}", file=out)
        if entry.args_prefix:
            print(
                f"    args_prefix: {' '.join(_printable(item) for item in entry.args_prefix)}",
                file=out,
            )
        for name, value in entry.environment:
            print(f"    environment: {_printable(name)}={_printable(value)}", file=out)


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
    environ: Mapping[str, str] | None,
    out: TextIO,
    message: str,
) -> None:
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    text = _read_config_text(path, environ)
    updated = _replace_apps_section(text, _render_apps(apps), path)
    if updated == text:
        print("Nothing changed. The configuration file was not rewritten.", file=out)
        return
    # Never write text that desk itself would refuse to load.
    _load_text(updated, path)
    if path.is_file():
        _backup_file(path)
    _write_text_atomic(path, updated)
    print(message, file=out)


def _checked_program(value: str, path: Path) -> str:
    program = value.strip()
    if not program:
        raise SettingsError(f"{path}: program must not be empty.")
    if program.startswith("-"):
        raise SettingsError(f"{path}: program cannot start with '-'.")
    if any(ord(character) < 32 or ord(character) == 127 for character in program):
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
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise SettingsError(f"{path}: environment names cannot contain control characters.")
    return name


def _read_config_text(path: Path, environ: Mapping[str, str] | None) -> str:
    source = path if path.is_file() else default_config_path()
    if not source.is_file():
        raise SettingsError(
            f"the built-in configuration is missing at {source}; reinstall desk."
        )
    return _read_text(source)


def _read_text(source: Path) -> str:
    try:
        size = source.stat().st_size
    except OSError as error:
        raise SettingsError(f"could not read {source}: {error}") from error
    if size > MAX_CONFIG_BYTES:
        raise SettingsError(
            f"{source} is too large ({size} bytes); the limit is {MAX_CONFIG_BYTES} bytes."
        )
    try:
        return source.read_text(encoding="utf-8")
    except OSError as error:
        raise SettingsError(f"could not read {source}: {error}") from error


def _replace_apps_section(text: str, rendered: str, source: Path) -> str:
    """Replace the top-level apps value and keep every other line."""
    yaml = _import_yaml(source)
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise _yaml_error(source, error) from error
    if document is None:
        return rendered
    if not isinstance(document, yaml.MappingNode):
        raise SettingsError(f"{source}: the top level must be a mapping.")
    pair = None
    for key_node, value_node in document.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "apps":
            pair = (key_node, value_node)
            break
    if pair is None:
        if text and not text.endswith("\n"):
            text += "\n"
        return text + ("\n" if text else "") + rendered
    start = pair[0].start_mark.index
    end = pair[1].end_mark.index
    return text[:start] + rendered + text[end:]


def _render_apps(apps: Mapping[str, AppSettings]) -> str:
    if not apps:
        return "apps: {}\n"
    lines = ["apps:"]
    for spec in sorted(apps):
        lines.append(f"  {json.dumps(spec, ensure_ascii=False)}:")
        entry = apps[spec]
        if entry.program is not None:
            lines.append(f"    program: {json.dumps(entry.program, ensure_ascii=False)}")
        if entry.args_prefix:
            lines.append("    args_prefix:")
            for argument in entry.args_prefix:
                lines.append(f"      - {json.dumps(argument, ensure_ascii=False)}")
        if entry.environment:
            lines.append("    environment:")
            for name, value in entry.environment:
                lines.append(
                    f"      {json.dumps(name, ensure_ascii=False)}: "
                    f"{json.dumps(value, ensure_ascii=False)}"
                )
    return "\n".join(lines) + "\n"


def _write_text_atomic(path: Path, text: str) -> None:
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
    except OSError as error:
        raise SettingsError(f"could not write {path}: {error}") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_file():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise SettingsError(f"could not write {path}: {error}") from error


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _backup_file(path: Path) -> None:
    backup = _backup_path(path)
    if backup.is_symlink() or (backup.exists() and not backup.is_file()):
        raise SettingsError(f"cannot back up {path}: {backup} is not a regular file.")
    try:
        data = path.read_bytes()
        descriptor = os.open(
            backup,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(backup, 0o600)
    except OSError as error:
        raise SettingsError(f"could not back up {path}: {error}") from error


def _load_text(text: str, source: Path) -> Settings:
    yaml = _import_yaml(source)
    loader = _unique_key_loader(yaml)
    try:
        data = yaml.load(text, Loader=loader)
    except RecursionError as error:
        raise SettingsError(f"{source}: the YAML document is nested too deeply.") from error
    except yaml.YAMLError as error:
        raise _yaml_error(source, error) from error
    return _validate(data, source)


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
            seen: set[Hashable] = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "YAML merge keys are not supported; write the fields explicitly",
                        key_node.start_mark,
                    )
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, Hashable):
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "mapping keys must be scalars",
                        key_node.start_mark,
                    )
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


def _yaml_error(source: Path, error) -> SettingsError:
    message = str(error)
    if "merge keys are not supported" in message:
        return SettingsError(
            f"{source}: YAML merge keys are not supported; write the fields explicitly."
        )
    return SettingsError(f"{source} is not valid YAML: {_printable(message)}")


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
        elif ord(character) < 32 or ord(character) == 127:
            pieces.append(f"\\x{ord(character):02x}")
        else:
            pieces.append(character)
    return "".join(pieces)


def _validate(data: object, path: Path) -> Settings:
    """Check the loaded document against the v1 schema."""
    if data is None:
        return Settings()
    document = _mapping(data, path, "the top level")
    if any(not isinstance(key, str) for key in document):
        raise SettingsError(f"{path}: every top-level key must be a string.")
    unknown = sorted(set(document) - set(ALLOWED_TOP_LEVEL), key=str)
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
    if any(not isinstance(key, str) for key in entry):
        raise SettingsError(f"{path}: every apps[{spec!r}] key must be a string.")
    unknown = sorted(set(entry) - set(ALLOWED_APP_KEYS), key=str)
    if unknown:
        raise SettingsError(
            f"{path}: unknown key(s) in apps[{spec!r}]: {', '.join(unknown)}."
        )

    program = entry.get("program")
    if program is not None:
        if not isinstance(program, str) or not program.strip():
            raise SettingsError(f"{path}: apps[{spec!r}].program must be a non-empty string.")
        program = program.strip()
        if "\0" in program or program.startswith("-"):
            raise SettingsError(
                f"{path}: apps[{spec!r}].program cannot start with '-' or contain NUL."
            )

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
