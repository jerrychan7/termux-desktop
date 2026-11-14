"""Render the session script from the template and build its environment."""

from __future__ import annotations

import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from ..config import TERMUX_X11_ACTIVITY, Paths, resolve_program
from .constants import (
    BACKEND_TERMUX_X11,
    BACKEND_VNC,
    REQUIRED_PROGRAMS,
    SESSION_ID_ENVIRONMENT,
    SESSION_LOCALE,
    SOCKET_WAIT_SECONDS,
    TEMPLATE_PLACEHOLDER_PATTERN,
    VNC_REQUIRED_PROGRAMS,
)
from .errors import SessionError


def session_environment(
    paths: Paths,
    session_id: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the environment that the session script and its children get."""
    if paths.prefix is None:
        raise SessionError("PREFIX is not set. desk must run inside Termux.")
    environment = dict(os.environ if environ is None else environ)
    environment["PREFIX"] = str(paths.prefix)
    environment["HOME"] = str(paths.home)
    environment["DISPLAY"] = paths.display
    environment["XDG_RUNTIME_DIR"] = str(paths.runtime_dir)
    environment["XDG_CONFIG_DIRS"] = str(paths.system_config_dir)
    environment["XDG_DATA_DIRS"] = str(paths.prefix / "share")
    environment["LANG"] = SESSION_LOCALE
    environment["LC_ALL"] = SESSION_LOCALE
    environment[SESSION_ID_ENVIRONMENT] = session_id
    return environment


def read_template(paths: Paths) -> str:
    """Read the session script template from the repository."""
    try:
        return paths.template_file.read_text(encoding="utf-8")
    except OSError as error:
        raise SessionError(
            f"could not read the session script template {paths.template_file}: {error}"
        ) from error


def render_session_script(
    paths: Paths,
    template_text: str | None = None,
    *,
    backend: str = "termux-x11",
    vnc_arguments: Sequence[str] = (),
) -> str:
    """Fill the template with the paths of this Termux installation.

    ``backend`` selects the X server that the script starts: termux-x11 or
    Xvnc. ``vnc_arguments`` are the Xvnc command line options; they are only
    used when ``backend`` is ``vnc``.

    The replacement runs in one pass, so an @NAME@ sequence that appears in a
    substituted value is copied literally and is not replaced a second time.
    """
    if paths.prefix is None:
        raise SessionError("PREFIX is not set. desk start must run inside Termux.")
    template = read_template(paths) if template_text is None else template_text
    replacements = {
        "@BASH@": str(paths.prefix / "bin" / "bash"),
        "@PREFIX@": shlex.quote(str(paths.prefix)),
        "@HOME@": shlex.quote(str(paths.home)),
        "@DISPLAY@": shlex.quote(paths.display),
        "@XDG_RUNTIME_DIR@": shlex.quote(str(paths.runtime_dir)),
        "@XDG_CONFIG_DIRS@": shlex.quote(str(paths.system_config_dir)),
        "@XDG_DATA_DIRS@": shlex.quote(str(paths.prefix / "share")),
        "@X11_SOCKET_DIR@": shlex.quote(str(paths.x11_socket_dir)),
        "@X11_SOCKET@": shlex.quote(str(paths.x11_socket)),
        "@SOCKET_WAIT_SECONDS@": str(SOCKET_WAIT_SECONDS),
        "@TERMUX_X11_ACTIVITY@": TERMUX_X11_ACTIVITY,
        "@X_SERVER_MODE@": shlex.quote(backend),
        "@XVNC_ARGS@": " ".join(shlex.quote(argument) for argument in vnc_arguments),
    }

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        if placeholder not in replacements:
            raise SessionError(
                f"the session script template contains a placeholder that desk does not fill: {placeholder}"
            )
        return replacements[placeholder]

    template = TEMPLATE_PLACEHOLDER_PATTERN.sub(replace, template)
    if not template.endswith("\n"):
        template += "\n"
    return template


def write_session_script(
    paths: Paths,
    template_text: str | None = None,
    *,
    backend: str = BACKEND_TERMUX_X11,
    vnc_arguments: Sequence[str] = (),
) -> Path:
    """Write the generated session script into the state directory."""
    text = render_session_script(
        paths, template_text, backend=backend, vnc_arguments=vnc_arguments
    )
    temporary = paths.session_script.with_name(
        f"{paths.session_script.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        paths.state_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, 0o755)
        os.replace(temporary, paths.session_script)
    except OSError as error:
        raise SessionError(
            f"could not write the session script {paths.session_script}: {error}"
        ) from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return paths.session_script


def missing_programs(
    paths: Paths, environ: Mapping[str, str], *, backend: str = BACKEND_TERMUX_X11
) -> tuple[str, ...]:
    """Return the programs that the session needs and cannot find."""
    required = VNC_REQUIRED_PROGRAMS if backend == BACKEND_VNC else REQUIRED_PROGRAMS
    search_path = environ.get("PATH")
    return tuple(
        name
        for name in required
        if resolve_program(name, paths.prefix, search_path) is None
    )
