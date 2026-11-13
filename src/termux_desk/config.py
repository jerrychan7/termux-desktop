"""Paths, fixed values, and errors used by the desk command.

Termux does not use the file system layout of a normal Linux distribution, so
every path is derived from the PREFIX and HOME environment variables instead
of being written into the code.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

APPLICATION_NAME = "termux-desktop"

# M0 has one fixed configuration: Termux itself, Termux:X11 on :0, and XFCE.
DISPLAY_NAME = ":0"
X_DISPLAY_NUMBER = DISPLAY_NAME[1:]
_DISPLAY_NAME_PATTERN = re.compile(r"^:[0-9]+$")

# The desktop session and the applications launched on it use this locale.
# Android libc provides only the C and C.UTF-8 locales. Other UTF-8 names
# make Xlib fall back to ASCII, which breaks window titles and clipboard
# text that contain anything outside ASCII.
DESKTOP_LOCALE = "C.UTF-8"

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
    """All file system locations used by one desk installation.

    ``display`` selects the X display (:0, :1, ...). The default keeps the
    file names of earlier versions; other displays get a ``-<n>`` suffix so a
    termux-x11 session and VNC sessions do not share state, logs, or scripts.
    """

    home: Path
    prefix: Path | None
    state_dir: Path
    desk_log: Path
    template_file: Path
    display: str = DISPLAY_NAME

    @property
    def _display_number(self) -> int:
        """Return the number part of the display name, e.g. 1 for :1."""
        number = self.display[1:]
        if not number.isdigit():
            raise ConfigError(f"invalid display name {self.display!r}; expected :<number>")
        return int(number)

    @property
    def _display_suffix(self) -> str:
        """Return the suffix that separates files of different displays."""
        if self.display == DISPLAY_NAME:
            return ""
        return f"-{self._display_number}"

    @property
    def session_file(self) -> Path:
        """Return the state file of the session on this display."""
        return self.state_dir / f"session{self._display_suffix}.json"

    @property
    def session_log(self) -> Path:
        """Return the log file of the session on this display."""
        return self.state_dir / f"session{self._display_suffix}.log"

    @property
    def session_script(self) -> Path:
        """Return the generated script of the session on this display."""
        return self.state_dir / f"session{self._display_suffix}.sh"

    def with_display(self, display: str) -> "Paths":
        """Return this path set for another X display."""
        if not isinstance(display, str) or not _DISPLAY_NAME_PATTERN.fullmatch(display):
            raise ConfigError(f"invalid display name {display!r}; expected :<number>")
        return replace(self, display=display)

    @property
    def prefix_tmp(self) -> Path:
        """Return $PREFIX/tmp, the directory Termux uses for temporary files."""
        return self._require_prefix() / "tmp"

    @property
    def install_root(self) -> Path:
        """Return $PREFIX/lib/termux-desktop, where an install copies the package."""
        return self._require_prefix() / "lib" / APPLICATION_NAME

    @property
    def install_previous(self) -> Path:
        """Return the copy that the last successful install replaced."""
        return self.install_root.with_name(self.install_root.name + ".previous")

    @property
    def launcher(self) -> Path:
        """Return $PREFIX/bin/desk, the entry file of an installed desk."""
        return self._require_prefix() / "bin" / "desk"

    @property
    def version_file(self) -> Path:
        """Return the VERSION file that records the installed copy."""
        return self.install_root / "VERSION"

    @property
    def xdg_data_home(self) -> Path:
        """Return ~/.local/share, the per-user data directory."""
        return self.home / ".local" / "share"

    @property
    def icon_root(self) -> Path:
        """Return ~/.local/share/icons/termux-desktop, where copied container icons go."""
        return self.xdg_data_home / "icons" / APPLICATION_NAME

    @property
    def menu_dir(self) -> Path:
        """Return the per-user directory that desktop environments read menu entries from."""
        return self.xdg_data_home / "applications"

    @property
    def termux_data_dir(self) -> Path:
        """Return the Termux application directory that contains $PREFIX.

        $PREFIX is $DATA_DIR/files/usr, so the directory the containers bind
        as /termux is two levels up.
        """
        return self._require_prefix().parents[1]

    @property
    def runtime_dir(self) -> Path:
        """Return the XDG runtime directory passed to the desktop session."""
        return self.prefix_tmp / f"{RUNTIME_DIR_NAME}{self._display_suffix}"

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
        """Return the Unix socket path of the X server for this display."""
        return self.x11_socket_dir / f"X{self._display_number}"

    @property
    def x11_lock(self) -> Path:
        """Return the X server lock file path for this display."""
        return self.prefix_tmp / f".X{self._display_number}-lock"

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
        desk_log=state_dir / DESK_LOG_NAME,
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
