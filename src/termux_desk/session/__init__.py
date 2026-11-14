"""Start, stop, and inspect one XFCE desktop session.

Python decides what to do and records the result. The generated session.sh
performs the actual launches inside one new process group: termux-x11, the
Termux:X11 Android app, and XFCE. desk start waits for the result and then
exits; desk does not stay running as a supervisor.

Every recorded session carries a boot id, the start time of its leader, and a
unique session id. desk verifies those facts before it signals or reports a
process group, so a reused process group id is never mistaken for the session.

This package is the public face of the session code. The modules behind it are:

constants  timeouts, required programs, backend names, process markers, paths
errors     SessionError, StartError, StopError, ProcessScanError, StartInterrupted
process    reading /proc and deciding which processes belong to a session
display    the X socket and lock file of a display
runtime    SessionRuntime, the process and signal operations it records
script     rendering session.sh and building the environment it runs with
verify     deciding whether a recorded session still owns a live group
start      starting one session
stop       stopping a verified session
status     reporting recorded sessions
"""

from __future__ import annotations

from ..state import SessionState
from .constants import (
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_RESET,
    BACKEND_TERMUX_X11,
    BACKEND_VNC,
    BACKENDS,
    BOOT_ID_PATH,
    DESKTOP_READY_TIMEOUT_SECONDS,
    KILL_TIMEOUT_SECONDS,
    LOCK_TIMEOUT_SECONDS,
    POLL_INTERVAL_SECONDS,
    PROC_ROOT,
    REQUIRED_PROGRAMS,
    SESSION_ID_ENVIRONMENT,
    SESSION_LOCALE,
    SOCKET_WAIT_SECONDS,
    TEMPLATE_PLACEHOLDER_PATTERN,
    TERMINATE_TIMEOUT_SECONDS,
    TERMUX_X11_PROCESS_MARKER,
    VNC_REQUIRED_PROGRAMS,
    X_SERVER_PROCESS_MARKERS,
    XFCE_PROCESS_MARKERS,
)
from .display import (
    _clear_stale_display_files_after_stop,
    clear_stale_display_files,
    display_in_use,
    display_socket_ready,
    read_lock_pid,
    unix_socket_accepts_connection,
    x11_socket_ready,
)
from .errors import (
    ProcessScanError,
    SessionError,
    StartError,
    StartInterrupted,
    StopError,
)
from .process import (
    ProcessStat,
    StartedProcess,
    _process_owned_by_another_user,
    has_termux_x11_process,
    has_x_server_process,
    has_xfce_process,
    is_x_server_process,
    parse_process_group,
    parse_process_stat,
    pid_alive,
    process_group_members,
    process_group_of,
    process_start_time,
    read_boot_id,
    read_process_arguments,
    read_process_environment,
    read_process_stat,
    session_in_group,
)
from .runtime import (
    START_INTERRUPT_SIGNALS,
    Pollable,
    SessionRuntime,
    _block_start_signals,
    _child_signal_mask_restorer,
    _kill_group_quietly,
    _restore_signal_mask,
    current_time,
    spawn_session_script,
    start_termux_x11_app,
)
from .script import (
    missing_programs,
    read_template,
    render_session_script,
    session_environment,
    write_session_script,
)
from .start import (
    _cleanup_failed_start,
    _ensure_runtime_directory,
    _keep_failed_state,
    _print_session_summary,
    _raise_start_interrupted,
    _show_display_app,
    _start_signal_guard,
    _StartSignalGuard,
    _wait_for_desktop,
    _write_state,
    start_session,
)
from .status import (
    SESSION_STATE_PATTERN,
    StatusReport,
    session_displays,
    session_status,
)
from .stop import (
    _refuse_unsafe_group,
    _signal_group,
    _terminate_group,
    _wait_for_group_exit,
    stop_session,
)
from .verify import (
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    VERIFICATION_UNVERIFIABLE,
    Verification,
    verify_session,
)

__all__ = [
    "ANSI_CYAN",
    "ANSI_GREEN",
    "ANSI_RESET",
    "BACKENDS",
    "BACKEND_TERMUX_X11",
    "BACKEND_VNC",
    "BOOT_ID_PATH",
    "DESKTOP_READY_TIMEOUT_SECONDS",
    "KILL_TIMEOUT_SECONDS",
    "LOCK_TIMEOUT_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "PROC_ROOT",
    "REQUIRED_PROGRAMS",
    "SESSION_ID_ENVIRONMENT",
    "SESSION_LOCALE",
    "SESSION_STATE_PATTERN",
    "SOCKET_WAIT_SECONDS",
    "START_INTERRUPT_SIGNALS",
    "TEMPLATE_PLACEHOLDER_PATTERN",
    "TERMINATE_TIMEOUT_SECONDS",
    "TERMUX_X11_PROCESS_MARKER",
    "VERIFICATION_RUNNING",
    "VERIFICATION_STALE",
    "VERIFICATION_UNVERIFIABLE",
    "VNC_REQUIRED_PROGRAMS",
    "XFCE_PROCESS_MARKERS",
    "X_SERVER_PROCESS_MARKERS",
    "Pollable",
    "ProcessScanError",
    "ProcessStat",
    "SessionError",
    "SessionRuntime",
    "SessionState",
    "StartError",
    "StartInterrupted",
    "StartedProcess",
    "StatusReport",
    "StopError",
    "Verification",
    "_StartSignalGuard",
    "_block_start_signals",
    "_child_signal_mask_restorer",
    "_cleanup_failed_start",
    "_clear_stale_display_files_after_stop",
    "_ensure_runtime_directory",
    "_keep_failed_state",
    "_kill_group_quietly",
    "_print_session_summary",
    "_process_owned_by_another_user",
    "_raise_start_interrupted",
    "_refuse_unsafe_group",
    "_restore_signal_mask",
    "_show_display_app",
    "_signal_group",
    "_start_signal_guard",
    "_terminate_group",
    "_wait_for_desktop",
    "_wait_for_group_exit",
    "_write_state",
    "clear_stale_display_files",
    "current_time",
    "display_in_use",
    "display_socket_ready",
    "has_termux_x11_process",
    "has_x_server_process",
    "has_xfce_process",
    "is_x_server_process",
    "missing_programs",
    "parse_process_group",
    "parse_process_stat",
    "pid_alive",
    "process_group_members",
    "process_group_of",
    "process_start_time",
    "read_boot_id",
    "read_lock_pid",
    "read_process_arguments",
    "read_process_environment",
    "read_process_stat",
    "read_template",
    "render_session_script",
    "session_displays",
    "session_environment",
    "session_in_group",
    "session_status",
    "spawn_session_script",
    "start_session",
    "start_termux_x11_app",
    "stop_session",
    "unix_socket_accepts_connection",
    "verify_session",
    "write_session_script",
    "x11_socket_ready",
]
