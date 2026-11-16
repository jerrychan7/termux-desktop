"""Launch one GUI application from a proot container on the desktop display.

desk app launch runs the application in the process group of the calling
process instead of detaching it: the desktop session owns the whole process
group, so desk stop reaches the application through the same signal. The
command replaces the desk process with proot, so no extra process stays
behind and the exit status of the application is the exit status of desk.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, TextIO

from . import fonts, ime, settings
from .config import DESKTOP_LOCALE, DeskError, Paths, resolve_program
from .container import (
    MOUNT_POINTS,
    NAME_PATTERN,
    PROOT_DISTRO_PROGRAM,
    container_command,
)
from .sanitize import printable
from .state import StateError, read_session_state

DEFAULT_EXTERNAL_STORAGE = "/storage/emulated/0"

# Android keeps its fonts here. The directory is mounted read-only, so a bind
# of it cannot change the host, and the mount disappears with the container.
SYSTEM_FONT_DIR = "/system/fonts"
CONTAINER_FONT_POINT = "/usr/share/fonts/truetype/android"

# The font preference file that desk setup writes on the host. The container
# fontconfig reads /etc/fonts/conf.d, so binding this one file gives the
# container the same CJK language preference as the host.
CONTAINER_FONT_PREFERENCE = f"/etc/fonts/conf.d/{fonts.PREFERENCE_FILE_NAME}"

TERMUX_POINT, HOME_POINT, SDCARD_POINT = MOUNT_POINTS


class AppError(DeskError):
    """The application could not be launched."""


@dataclass(frozen=True)
class AppTarget:
    """The container and the program name of one application."""

    container: str
    app: str

    @property
    def spec(self) -> str:
        """Return the container/app form used on the command line."""
        return f"{self.container}/{self.app}"


def parse_target(text: str) -> AppTarget:
    """Split 'container/app' and check both parts."""
    container, separator, app = text.partition("/")
    if not separator or not container or not app:
        raise AppError(
            f"'{text}' is not a 'container/app' pair. "
            "For example: ubuntu-desktop-test/xterm"
        )
    if not NAME_PATTERN.fullmatch(container):
        raise AppError(
            f"'{container}' is not a valid container name. "
            "Use letters, digits, '.', '_' or '-', and do not start with '-'."
        )
    if app.startswith("-"):
        raise AppError(
            f"'{app}' cannot be used as the program name. It would be read as an option."
        )
    return AppTarget(container=container, app=app)


def launch_bindings(
    paths: Paths,
    environ: Mapping[str, str],
    *,
    font_host_dir: str = SYSTEM_FONT_DIR,
) -> tuple[tuple[str, str], ...]:
    """Return the host directories that a launched application can reach.

    The Android system fonts join the list when they exist, so container
    applications can shape CJK text without installing their own fonts. The
    bind is safe to add: /system is mounted read-only, and proot removes the
    mount when the container exits.

    When desk setup wrote the font preference file, it is bound into the
    container fontconfig directory, so container applications pick the CJK
    faces by the same language rules as the host.
    """
    storage = environ.get("EXTERNAL_STORAGE", "").strip() or DEFAULT_EXTERNAL_STORAGE
    bindings = [
        (str(paths.termux_data_dir), TERMUX_POINT),
        (str(paths.home), HOME_POINT),
        (storage, SDCARD_POINT),
    ]
    if os.path.isdir(font_host_dir):
        bindings.append((font_host_dir, CONTAINER_FONT_POINT))
    if paths.prefix is not None:
        preference = fonts.preference_path(paths)
        if preference.is_file():
            bindings.append((str(preference), CONTAINER_FONT_PREFERENCE))
    return tuple(bindings)


def build_launch_command(
    proot_distro: str,
    target: AppTarget,
    display: str,
    binds: tuple[tuple[str, str], ...],
    arguments: tuple[str, ...] = (),
    extra_environment: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    """Build the proot-distro command that starts one application.

    Without arguments the application name doubles as the program name, which
    is what a hand written entry needs. With arguments the arguments are the
    whole command line, because a generated entry passes the Exec value of the
    container's own .desktop file and that name need not match the file name.
    """
    program = tuple(arguments) if arguments else (target.app,)
    environment: dict[str, str] = {
        "DISPLAY": display,
        "LANG": DESKTOP_LOCALE,
        "LC_ALL": DESKTOP_LOCALE,
    }
    # Later values win, so a configuration file can override a default.
    environment.update(dict(extra_environment))
    return container_command(
        proot_distro,
        target.container,
        *program,
        environment=tuple(environment.items()),
        binds=binds,
        shared_tmp=True,
    )


def prepare_app_command(
    paths: Paths,
    target_text: str,
    *,
    environ: Mapping[str, str] | None = None,
    arguments: Sequence[str] = (),
) -> tuple[AppTarget, str, tuple[str, ...]]:
    """Return the target, the display, and the command for one application."""
    environment = os.environ if environ is None else environ
    target = parse_target(target_text)
    application_arguments = tuple(arguments)
    for argument in application_arguments:
        if not argument or "\0" in argument:
            raise AppError(f"'{argument}' is not a usable argument for {target.spec}.")

    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, paths.prefix, environment.get("PATH")
    )
    if proot_distro is None:
        raise AppError(
            "the program 'proot-distro' was not found. "
            "Install it with 'pkg install proot-distro' first."
        )

    try:
        state = read_session_state(paths.session_file)
    except StateError as error:
        raise AppError(str(error)) from error
    if state is None:
        raise AppError(
            "the desktop session is not running. Run 'desk start' first."
        )
    if not paths.x11_socket.is_socket():
        raise AppError(
            f"the X server socket {paths.x11_socket} is missing. "
            "Run 'desk start' first."
        )

    environment = dict(environment)
    if (
        settings.PROFILE_ENVIRONMENT not in environment
        and state.profile != "default"
    ):
        environment[settings.PROFILE_ENVIRONMENT] = state.profile
    binds = launch_bindings(paths, environment)
    config = settings.load_settings(
        paths, environ=environment, allow_missing_pyyaml=True
    )
    override = config.apps.get(target.spec)
    command_line = (
        tuple(application_arguments) if application_arguments else (target.app,)
    )
    if override is not None and override.program is not None:
        # Replace the program and keep the arguments that came with a menu
        # entry, so an override works for a generated Exec too.
        command_line = (override.program, *command_line[1:])
    if override is not None and override.args_prefix:
        command_line = (command_line[0], *override.args_prefix, *command_line[1:])
    extra_environment = ime.container_environment(paths, environment)
    if override is not None:
        extra_environment = (*extra_environment, *override.environment)
    command = build_launch_command(
        proot_distro,
        target,
        state.display,
        binds,
        command_line,
        extra_environment,
    )
    return target, state.display, command


def run_app_launch(
    paths: Paths,
    target_text: str,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    exec_func: Callable[[str, list[str]], None] | None = None,
    arguments: Sequence[str] = (),
) -> None:
    """Build the command for one application and replace this process."""
    output = sys.stdout if out is None else out
    execute = os.execv if exec_func is None else exec_func

    target, display, command = prepare_app_command(
        paths, target_text, environ=environ, arguments=arguments
    )
    print(
        f"Launching {printable(target.spec)} on {printable(display)}.",
        file=output,
        flush=True,
    )
    try:
        execute(command[0], list(command))
    except OSError as error:
        raise AppError(f"could not launch {target.spec}: {error}") from error


PROBE_STDERR_LIMIT = 64 * 1024
PROBE_DETAIL_LINES = 5


@dataclass(frozen=True)
class StartProbe:
    """What happened when desk started one application for a short probe."""

    spec: str
    status: str
    timeout: float
    exit_code: int | None = None
    detail: str = ""
    window: bool | None = None


def _window_appeared(
    before: frozenset[int] | None, after: frozenset[int] | None
) -> bool | None:
    """Return whether a new top-level window exists, or None when unknown."""
    if before is None or after is None:
        return None
    return bool(after - before)


def _root_window_ids(display: str) -> frozenset[int] | None:
    """Return the top-level client windows on one display, or None.

    The probe only needs a set that grows when a new window appears. Reading
    _NET_CLIENT_LIST through libX11 keeps the check dependency-free; when the
    library or the property is unavailable the caller treats the window as
    unknown instead of failing the probe.
    """
    library = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        x11 = ctypes.CDLL(library)
    except OSError:
        return None
    try:
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        x11.XGetWindowProperty.restype = ctypes.c_int
        x11.XFree.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        handle = x11.XOpenDisplay(display.encode())
        if not handle:
            return None
        try:
            root = x11.XDefaultRootWindow(handle)
            atom = x11.XInternAtom(handle, b"_NET_CLIENT_LIST", 1)
            if not atom:
                return None
            actual_type = ctypes.c_ulong()
            actual_format = ctypes.c_int()
            items = ctypes.c_ulong()
            remaining = ctypes.c_ulong()
            data = ctypes.c_void_p()
            status = x11.XGetWindowProperty(
                handle,
                root,
                atom,
                0,
                4096,
                0,
                33,  # XA_WINDOW
                ctypes.byref(actual_type),
                ctypes.byref(actual_format),
                ctypes.byref(items),
                ctypes.byref(remaining),
                ctypes.byref(data),
            )
            if status != 0 or not data.value or actual_format.value != 32:
                return None
            array = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            try:
                return frozenset(int(array[index]) for index in range(items.value))
            finally:
                x11.XFree(data)
        finally:
            x11.XCloseDisplay(handle)
    except (AttributeError, OSError, ValueError):
        return None


def _stop_probe_process(process: subprocess.Popen[bytes]) -> None:
    """Stop the process group that a probe started."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=1.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _probe_detail(stderr_file: tempfile.TemporaryFile) -> str:
    """Return the last lines that the application wrote to stderr."""
    try:
        stderr_file.seek(0, os.SEEK_END)
        size = stderr_file.tell()
        stderr_file.seek(max(0, size - PROBE_STDERR_LIMIT))
        data = stderr_file.read(PROBE_STDERR_LIMIT)
    except OSError:
        return ""
    text = data.decode("utf-8", "replace").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-PROBE_DETAIL_LINES:])[:1000]


def probe_app_start(
    paths: Paths,
    target_text: str,
    *,
    arguments: Sequence[str] = (),
    timeout: float = 8.0,
    environ: Mapping[str, str] | None = None,
    spawn: Callable[..., subprocess.Popen[bytes]] | None = None,
    window_probe: Callable[[], frozenset[int] | None] | None = None,
) -> StartProbe:
    """Start one application, stop it after a timeout, and report what happened.

    The probe uses the same command as desk app launch. An application that
    is still running after the timeout counts as started; one that exits with
    status 0 counts as started and exited; a non-zero status is a failure and
    the last stderr lines explain why. When a window probe is available, the
    result also records whether a new top-level window appeared.
    """
    target, display, command = prepare_app_command(
        paths, target_text, environ=environ, arguments=arguments
    )
    probe_windows = window_probe
    if probe_windows is None:
        def probe_windows() -> frozenset[int] | None:
            return _root_window_ids(display)
    before = probe_windows()
    start = subprocess.Popen if spawn is None else spawn
    stderr_file = tempfile.TemporaryFile()
    try:
        try:
            process = start(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as error:
            return StartProbe(target.spec, "failed", timeout, detail=str(error))
        try:
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                after = probe_windows()
                window = _window_appeared(before, after)
                _stop_probe_process(process)
                return StartProbe(target.spec, "started", timeout, window=window)
            after = probe_windows()
            window = _window_appeared(before, after)
            detail = _probe_detail(stderr_file)
            if exit_code == 0:
                return StartProbe(target.spec, "exited", timeout, 0, detail, window)
            return StartProbe(target.spec, "failed", timeout, exit_code, detail, window)
        except OSError as error:
            return StartProbe(target.spec, "failed", timeout, detail=str(error))
    finally:
        stderr_file.close()
