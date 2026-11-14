"""Optional ibus input method support.

``desk setup --ime`` installs the packages; a session enables them only when
they are present. Host applications load the ibus GTK module. Container
applications use XIM instead, because the GTK module of the host cannot be
loaded across the proot boundary.

The input method is optional: nothing in this module requires the packages to
exist, and an installation without them behaves exactly as before.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Mapping

from .config import Paths, resolve_program

IME_PACKAGES = ("ibus", "ibus-libpinyin", "libdbusmenu-gtk3")
IBUS_PROGRAM = "ibus-daemon"
PANEL_NAME = "ibus-ui-gtk3"
XFCE_PROGRAM = "startxfce4"
HOST_ENVIRONMENT = (
    ("GTK_IM_MODULE", "ibus"),
    ("QT_IM_MODULE", "ibus"),
    ("XMODIFIERS", "@im=ibus"),
)
CONTAINER_ENVIRONMENT = (
    ("GTK_IM_MODULE", "xim"),
    ("XMODIFIERS", "@im=ibus"),
)


def panel_program(paths: Paths) -> Path | None:
    """Return the ibus GTK panel program installed for this prefix."""
    if paths.prefix is None:
        return None
    panel = paths.prefix / "libexec" / PANEL_NAME
    return panel if panel.is_file() else None


def ime_available(
    paths: Paths, environ: Mapping[str, str] | None = None
) -> bool:
    """Return True when both the daemon and its panel are installed."""
    if paths.prefix is None:
        return False
    environment = os.environ if environ is None else environ
    daemon = resolve_program(IBUS_PROGRAM, paths.prefix, environment.get("PATH"))
    return daemon is not None and panel_program(paths) is not None


def host_environment(
    paths: Paths, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return the environment variables host applications need."""
    if not ime_available(paths, environ):
        return {}
    return dict(HOST_ENVIRONMENT)


def container_environment(
    paths: Paths, environ: Mapping[str, str] | None = None
) -> tuple[tuple[str, str], ...]:
    """Return the environment variables container applications need.

    XIM is used because the container has its own glibc and GTK; the ibus
    module of the host cannot be loaded there. XIM only carries committed
    text, which is enough for terminals and simple GTK widgets.
    """
    if not ime_available(paths, environ):
        return ()
    return CONTAINER_ENVIRONMENT


def xfce_launch_command(
    paths: Paths, environ: Mapping[str, str] | None = None
) -> str:
    """Return the command that starts XFCE with or without ibus.

    The daemon runs inside the session's D-Bus bus, so every X display gets
    its own daemon. It is started before XFCE and kept optional: when it fails
    the desktop still starts.
    """
    panel = panel_program(paths)
    if panel is None or not ime_available(paths, environ):
        return f"dbus-launch --exit-with-session {XFCE_PROGRAM}"
    inner = (
        f"{IBUS_PROGRAM} -drx --panel={shlex.quote(str(panel))} || true; "
        f"exec {XFCE_PROGRAM}"
    )
    return f"dbus-launch --exit-with-session sh -c {shlex.quote(inner)}"
