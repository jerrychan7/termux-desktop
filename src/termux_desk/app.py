"""Launch one GUI application from a proot container on the desktop display.

desk app launch runs the application in the process group of the calling
process instead of detaching it: the desktop session owns the whole process
group, so desk stop reaches the application through the same signal. The
command replaces the desk process with proot, so no extra process stays
behind and the exit status of the application is the exit status of desk.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence, TextIO

from . import fonts
from .config import DESKTOP_LOCALE, DeskError, Paths, resolve_program
from .container import (
    MOUNT_POINTS,
    NAME_PATTERN,
    PROOT_DISTRO_PROGRAM,
    container_command,
)
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
    if not NAME_PATTERN.match(container):
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
) -> tuple[str, ...]:
    """Build the proot-distro command that starts one application.

    Without arguments the application name doubles as the program name, which
    is what a hand written entry needs. With arguments the arguments are the
    whole command line, because a generated entry passes the Exec value of the
    container's own .desktop file and that name need not match the file name.
    """
    program = tuple(arguments) if arguments else (target.app,)
    return container_command(
        proot_distro,
        target.container,
        *program,
        environment=(
            ("DISPLAY", display),
            ("LANG", DESKTOP_LOCALE),
            ("LC_ALL", DESKTOP_LOCALE),
        ),
        binds=binds,
        shared_tmp=True,
    )


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
    environment = os.environ if environ is None else environ
    execute = os.execv if exec_func is None else exec_func

    target = parse_target(target_text)
    application_arguments = tuple(argument for argument in arguments)
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

    binds = launch_bindings(paths, environment)
    command = build_launch_command(
        proot_distro, target, state.display, binds, application_arguments
    )
    print(
        f"Launching {target.spec} on {state.display}.",
        file=output,
        flush=True,
    )
    try:
        execute(proot_distro, list(command))
    except OSError as error:
        raise AppError(f"could not launch {target.spec}: {error}") from error
