"""Comment-preserving, atomic rewriting of the configuration file."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Hashable, Mapping

from . import model
from .model import MAX_CONFIG_BYTES, PYYAML_HINT, AppSettings, SettingsError, _printable


def _read_text(source: Path) -> str:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, os.O_RDONLY | nofollow)
    except OSError as error:
        raise SettingsError(f"could not read {source}: {error}") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise SettingsError(f"{source} is not a regular file.")
        if details.st_size > MAX_CONFIG_BYTES:
            raise SettingsError(
                f"{source} is too large ({details.st_size} bytes); "
                f"the limit is {MAX_CONFIG_BYTES} bytes."
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_CONFIG_BYTES:
            raise SettingsError(
                f"{source} is too large ({len(data)} bytes); "
                f"the limit is {MAX_CONFIG_BYTES} bytes."
            )
    except OSError as error:
        raise SettingsError(f"could not read {source}: {error}") from error
    finally:
        os.close(descriptor)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SettingsError(f"{source} is not UTF-8; save it as UTF-8.") from error

def _read_config_text(path: Path, environ: Mapping[str, str] | None) -> str:
    source = path if path.is_file() else model.default_config_path()
    if not source.is_file():
        raise SettingsError(
            f"the built-in configuration is missing at {source}; reinstall desk."
        )
    return _read_text(source)

def _replace_section(text: str, key: str, rendered: str, source: Path) -> str:
    """Replace one top-level section and keep every other line."""
    yaml = _import_yaml(source)
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        raise _yaml_error(source, error) from error
    if document is None:
        # A file that only contains comments keeps its text; the section is
        # appended below the comments.
        return _append_section(text, rendered)
    if not isinstance(document, yaml.MappingNode):
        raise SettingsError(f"{source}: the top level must be a mapping.")
    pair = None
    for key_node, value_node in document.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            pair = (key_node, value_node)
            break
    if pair is None:
        return _append_section(text, rendered)
    start = pair[0].start_mark.index
    # PyYAML reports the end of a block mapping at the end of the document,
    # which includes trailing comments. Use the last content leaf instead.
    end = _last_content_end(pair[1])
    tail = text[end:]
    if tail.startswith("\r\n"):
        tail = tail[2:]
    elif tail.startswith("\n"):
        tail = tail[1:]
    if tail and not tail.startswith(("\n", "\r", "#")):
        tail = _newline_style(text) + tail
    return text[:start] + rendered + tail

def _last_content_end(node) -> int:
    """Return the end offset of the last scalar inside a YAML node."""
    value = getattr(node, "value", None)
    if isinstance(value, list) and value:
        last = value[-1]
        if isinstance(last, tuple):
            last = last[1]
        return _last_content_end(last)
    return node.end_mark.index

def _replace_apps_section(text: str, rendered: str, source: Path) -> str:
    """Replace the top-level apps value and keep every other line."""
    return _replace_section(text, "apps", rendered, source)

def _append_section(text: str, rendered: str) -> str:
    newline = _newline_style(text)
    base = text.rstrip("\r\n")
    if not base:
        return rendered
    return base + newline + newline + rendered

def _newline_style(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"

def _render_apps(apps: Mapping[str, AppSettings], newline: str = "\n") -> str:
    if not apps:
        return "apps: {}" + newline
    lines = ["apps:"]
    for spec in sorted(apps):
        lines.append(f"  {_yaml_string(spec)}:")
        entry = apps[spec]
        if entry.program is not None:
            lines.append(f"    program: {_yaml_string(entry.program)}")
        if entry.args_prefix:
            lines.append("    args_prefix:")
            for argument in entry.args_prefix:
                lines.append(f"      - {_yaml_string(argument)}")
        if entry.environment:
            lines.append("    environment:")
            for name, value in entry.environment:
                lines.append(
                    f"      {_yaml_string(name)}: {_yaml_string(value)}"
                )
    return newline.join(lines) + newline

def _yaml_string(value: str) -> str:
    """Render a string as a double-quoted YAML scalar that round-trips.

    JSON string syntax is valid YAML; C1 controls and format characters are
    escaped as well because YAML readers treat some of them as line breaks.
    """
    rendered = json.dumps(value, ensure_ascii=False)
    pieces: list[str] = []
    for character in rendered:
        code = ord(character)
        category = unicodedata.category(character)
        if (
            0x7F <= code <= 0x9F
            or character in "\u2028\u2029"
            or category in ("Cf", "Cs")
        ):
            if code <= 0xFFFF:
                pieces.append(f"\\u{code:04x}")
            else:
                pieces.append(f"\\U{code:08x}")
        else:
            pieces.append(character)
    return "".join(pieces)

def _write_text_atomic(path: Path, text: str) -> None:
    if path.is_symlink():
        raise SettingsError(f"{path} is a symbolic link; desk will not replace it.")
    if path.exists() and not path.is_file():
        raise SettingsError(f"{path} is not a regular file.")
    _clean_stale_temporaries(path)
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
    _fsync_directory(path.parent)

def _clean_stale_temporaries(path: Path) -> None:
    """Remove temporary files left by an earlier interrupted write."""
    pattern = f".{path.name}.*.tmp"
    try:
        candidates = list(path.parent.glob(pattern))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            os.unlink(candidate)
        except OSError:
            continue

def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)

def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")

def _backup_file(path: Path) -> None:
    backup = _backup_path(path)
    if backup.is_symlink() or (backup.exists() and not backup.is_file()):
        raise SettingsError(f"cannot back up {path}: {backup} is not a regular file.")
    try:
        if backup.exists() and os.lstat(backup).st_nlink > 1:
            raise SettingsError(
                f"cannot back up {path}: {backup} has multiple hard links."
            )
    except OSError as error:
        raise SettingsError(f"could not back up {path}: {error}") from error
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


def _import_yaml(path: Path):
    """Import PyYAML on demand, with a hint when it is missing."""
    try:
        import yaml
    except ImportError as error:
        raise SettingsError(
            f"desk needs PyYAML to read the configuration; {PYYAML_HINT}."
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

def _render_scalar(key: str, value: object, newline: str) -> str:
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, int):
        rendered = str(value)
    else:
        rendered = _yaml_string(str(value))
    return f"  {key}: {rendered}{newline}"


def _render_vnc(value, newline: str = "\n") -> str:
    lines = ["vnc:"]
    lines.append(_render_scalar("display", value.display, ""))
    lines.append(_render_scalar("geometry", value.geometry, ""))
    lines.append(_render_scalar("depth", value.depth, ""))
    lines.append(_render_scalar("host", value.host, ""))
    lines.append(_render_scalar("port", value.port, ""))
    lines.append(_render_scalar("proxy", value.proxy, ""))
    lines.append(_render_scalar("view_only", value.view_only, ""))
    return newline.join(lines) + newline


def _render_ime(value, newline: str = "\n") -> str:
    return "ime:" + newline + _render_scalar("enabled", value.enabled, "") + newline


def _render_fonts(value, newline: str = "\n") -> str:
    lines = ["fonts:", _render_scalar("language", value.language, "")]
    for key in ("sans_serif", "serif", "monospace"):
        family = getattr(value, key)
        if family is not None:
            lines.append(_render_scalar(key, family, ""))
    return newline.join(lines) + newline
