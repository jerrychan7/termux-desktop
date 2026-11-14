"""Values that stay fixed while desk runs.

The module holds the timeouts, the programs a session needs, the backend
names, the process markers that identify an X server or XFCE, and the paths
of the kernel and X server interfaces that session code reads.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import DESKTOP_LOCALE

SOCKET_WAIT_SECONDS = 20
DESKTOP_READY_TIMEOUT_SECONDS = 60.0
TERMINATE_TIMEOUT_SECONDS = 10.0
KILL_TIMEOUT_SECONDS = 5.0
LOCK_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.2

REQUIRED_PROGRAMS = ("termux-x11", "startxfce4", "dbus-launch", "am", "bash")
VNC_REQUIRED_PROGRAMS = ("Xvnc", "startxfce4", "dbus-launch", "bash")
BACKEND_TERMUX_X11 = "termux-x11"
BACKEND_VNC = "vnc"
BACKENDS = (BACKEND_TERMUX_X11, BACKEND_VNC)
SESSION_ID_ENVIRONMENT = "DESK_SESSION_ID"

# Android libc provides only the C and C.UTF-8 locales. The shared constant
# lives in config because container launches use the same locale.
SESSION_LOCALE = DESKTOP_LOCALE

ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_RESET = "\033[0m"

TERMUX_X11_PROCESS_MARKER = "termux-x11"
# Any of these programs counts as the X server of a session: termux-x11 for
# the Android-backed desktop, Xvnc for a VNC session.
X_SERVER_PROCESS_MARKERS = (TERMUX_X11_PROCESS_MARKER, "Xvnc", "Xtigervnc")
# Any of these programs counts as an XFCE process. xfce4-session is the
# session manager that starts the others.
XFCE_PROCESS_MARKERS = (
    "xfce4-session",
    "xfwm4",
    "xfce4-panel",
    "xfdesktop",
    "xfsettingsd",
)

PROC_ROOT = Path("/proc")

BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")

TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"@[A-Z][A-Z0-9_]*@")
