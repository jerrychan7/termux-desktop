"""Paths, fixed values, and errors used by the desk command.

Termux does not use the file system layout of a normal Linux distribution, so
every path is derived from the PREFIX and HOME environment variables instead
of being written into the code.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

APPLICATION_NAME = "termux-desktop"

# M0 has one fixed configuration: Termux itself, Termux:X11 on :0, and XFCE.
DISPLAY_NAME = ":0"
X_DISPLAY_NUMBER = DISPLAY_NAME[1:]

# The complete package list that desk setup installs. The tuple order is the
# order in which packages are reported and installed.
REPO_PACKAGE = "x11-repo"
FIXED_PACKAGES = (
    "x11-repo",
    "termux-x11-nightly",
    "xfce4",
    "dbus",
    "termux-am",
)

TERMUX_X11_ANDROID_PACKAGE = "com.termux.x11"
TERMUX_X11_ACTIVITY = "com.termux.x11/com.termux.x11.MainActivity"
TERMUX_X11_DOWNLOAD_URL = "https://github.com/termux/termux-x11/releases"

SESSION_FILE_NAME = "session.json"
DESK_LOG_NAME = "desk.log"
SESSION_LOG_NAME = "session.log"
SESSION_SCRIPT_NAME = "session.sh"
RUNTIME_DIR_NAME = "termux-desktop-runtime"


class DeskError(Exception):
    """An error that desk prints to the user without a traceback."""


class ConfigError(DeskError):
    """The environment does not provide paths that desk needs."""


@dataclass(frozen=True)
class Paths:
    """All file system locations used by one desk installation."""

    home: Path
    prefix: Path | None
    state_dir: Path
    session_file: Path
    desk_log: Path
    session_log: Path
    session_script: Path
    template_file: Path

    @property
    def prefix_tmp(self) -> Path:
        """Return $PREFIX/tmp, the directory Termux uses for temporary files."""
        return self._require_prefix() / "tmp"

    @property
    def runtime_dir(self) -> Path:
        """Return the XDG runtime directory passed to the desktop session."""
        return self.prefix_tmp / RUNTIME_DIR_NAME

    @property
    def system_config_dir(self) -> Path:
        """Return the system XDG configuration directory inside the prefix."""
        return self._require_prefix() / "etc" / "xdg"

    @property
    def x11_socket_dir(self) -> Path:
        """Return the directory that holds X11 Unix sockets."""
        return self.prefix_tmp / ".X11-unix"

    @property
    def x11_socket(self) -> Path:
        """Return the Unix socket path of the X server for :0."""
        return self.x11_socket_dir / f"X{X_DISPLAY_NUMBER}"

    @property
    def x11_lock(self) -> Path:
        """Return the X server lock file path for :0."""
        return self.prefix_tmp / f".X{X_DISPLAY_NUMBER}-lock"

    def _require_prefix(self) -> Path:
        if self.prefix is None:
            raise ConfigError("PREFIX is not set. desk must run inside Termux.")
        return self.prefix


def repository_root() -> Path:
    """Return the repository root that contains this Python package."""
    return Path(__file__).resolve().parents[2]


def load_paths(
    environ: Mapping[str, str] | None = None,
    *,
    template_file: Path | None = None,
) -> Paths:
    """Build the path set from HOME and PREFIX.

    HOME is required because the state file and the logs live in the user
    directory. PREFIX may be missing; commands that need it report a clear
    error instead of falling back to a path that does not exist.
    """
    environment = os.environ if environ is None else environ

    home_text = environment.get("HOME", "").strip()
    if not home_text:
        raise ConfigError("HOME is not set. desk needs HOME to find its state and log files.")
    home = Path(home_text)

    prefix_text = environment.get("PREFIX", "").strip()
    prefix = Path(prefix_text) if prefix_text else None

    state_dir = home / ".local" / "state" / APPLICATION_NAME
    template = template_file if template_file is not None else repository_root() / "templates" / "session.sh"

    return Paths(
        home=home,
        prefix=prefix,
        state_dir=state_dir,
        session_file=state_dir / SESSION_FILE_NAME,
        desk_log=state_dir / DESK_LOG_NAME,
        session_log=state_dir / SESSION_LOG_NAME,
        session_script=state_dir / SESSION_SCRIPT_NAME,
        template_file=template,
    )


def resolve_program(name: str, prefix: Path | None, search_path: str | None) -> str | None:
    """Return the path of an executable, or None when it cannot be found.

    The Termux prefix is checked first because a Termux shell can be started
    without $PREFIX/bin on PATH. The PATH search is the fallback.
    """
    if prefix is not None:
        candidate = prefix / "bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    if search_path:
        found = shutil.which(name, path=search_path)
        if found:
            return found
    return None
