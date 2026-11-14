"""Read-only checks for the Termux environment and the desk session.

desk doctor does not install packages, does not start or stop anything, and
does not remove files. It reports facts and the next step for each problem.
"""

from __future__ import annotations

import os
import platform
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, TextIO

from .config import (
    DISPLAY_NAME,
    TERMUX_X11_ANDROID_PACKAGE,
    TERMUX_X11_DOWNLOAD_URL,
    DeskError,
    Paths,
    resolve_program,
)
from .runner import CommandRunner
from .session import (
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    SessionRuntime,
    Verification,
    pid_alive,
    unix_socket_accepts_connection,
    verify_session,
)
from .state import SessionState, StateError, read_session_state

OK = "ok"
WARNING = "warn"
FAILED = "fail"

COLOR_AUTO = "auto"
COLOR_ALWAYS = "always"
COLOR_NEVER = "never"
COLOR_CHOICES = (COLOR_AUTO, COLOR_ALWAYS, COLOR_NEVER)

ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"
ANSI_GRAY = "\033[90m"
ANSI_SEPARATOR = "\033[1;3;4;97m"
ANSI_RESET = "\033[0m"
STATUS_COLORS = {OK: ANSI_GREEN, WARNING: ANSI_YELLOW, FAILED: ANSI_RED}
STATUS_TAG_WIDTH = max(len(f"[{status}]") for status in (OK, WARNING, FAILED))
# One space separates the padded tag from the check name.
NAME_START = STATUS_TAG_WIDTH + 1

ANDROID_PACKAGE_QUERY_COMMANDS = (
    ("pm", "list", "packages", TERMUX_X11_ANDROID_PACKAGE),
    ("/system/bin/pm", "list", "packages", TERMUX_X11_ANDROID_PACKAGE),
    ("/system/bin/cmd", "package", "list", "packages", TERMUX_X11_ANDROID_PACKAGE),
)


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one doctor check."""

    name: str
    status: str
    message: str
    hint: str | None = None


@dataclass(frozen=True)
class PlatformFacts:
    """The system facts that show whether desk runs on Android or Termux.

    platform.system(), sys.platform, and os.uname().release describe the
    system. TERMUX_VERSION and PREFIX describe Termux itself. On Termux,
    platform.uname().release reports the Android version (for example "16")
    instead of the kernel release, so the kernel release is read from
    os.uname().
    """

    system: str
    python_platform: str
    kernel_release: str
    termux_version: str = ""
    prefix: str = ""


def read_platform_facts(environ: Mapping[str, str] | None = None) -> PlatformFacts:
    """Read the system name, the Python platform, and the Termux facts."""
    environment = os.environ if environ is None else environ
    return PlatformFacts(
        system=platform.system(),
        python_platform=sys.platform,
        kernel_release=os.uname().release,
        termux_version=environment.get("TERMUX_VERSION", ""),
        prefix=environment.get("PREFIX", ""),
    )


def check_android_system(facts: PlatformFacts) -> CheckResult:
    """Report whether the Python platform facts show an Android system.

    This check is advisory. Some Android kernels do not put "android" in the
    kernel release, and Python 3.10 on Termux reports sys.platform "linux".
    Termux itself is identified by check_termux_environment instead.
    """
    evidence: list[str] = []
    if facts.system == "Android":
        evidence.append("platform.system()=Android")
    if facts.python_platform == "android":
        evidence.append("sys.platform=android")
    if "android" in facts.kernel_release.lower():
        evidence.append(f"kernel release {facts.kernel_release}")

    facts_text = (
        f"platform.system()={facts.system}, sys.platform={facts.python_platform}, "
        f"kernel release {facts.kernel_release}"
    )
    if evidence:
        return CheckResult(
            name="android system",
            status=OK,
            message=f"Android detected ({', '.join(evidence)}).",
        )
    return CheckResult(
        name="android system",
        status=WARNING,
        message=(
            f"Android was not detected from the Python platform facts ({facts_text}). "
            "Termux is identified by TERMUX_VERSION and the Termux prefix."
        ),
    )


def check_termux_environment(facts: PlatformFacts) -> CheckResult:
    """Report whether desk runs inside Termux.

    TERMUX_VERSION and a PREFIX below com.termux/files/usr are the direct
    Termux facts. They work on Python 3.10, where sys.platform is still
    "linux" and the kernel release may not mention Android.
    """
    evidence: list[str] = []
    if facts.termux_version:
        evidence.append(f"TERMUX_VERSION={facts.termux_version}")
    if "com.termux/files/usr" in facts.prefix:
        evidence.append(f"PREFIX={facts.prefix}")

    if evidence:
        return CheckResult(
            name="termux environment",
            status=OK,
            message=f"Termux detected ({', '.join(evidence)}).",
        )
    return CheckResult(
        name="termux environment",
        status=FAILED,
        message=(
            "Termux was not detected: TERMUX_VERSION is not set and "
            f"PREFIX={facts.prefix!r} does not contain 'com.termux/files/usr'. "
            "desk must run in Termux on Android."
        ),
    )


def check_prefix(environ: Mapping[str, str]) -> CheckResult:
    """Report whether PREFIX is set and points to a directory."""
    prefix_text = environ.get("PREFIX", "").strip()
    if not prefix_text:
        return CheckResult(
            name="PREFIX",
            status=FAILED,
            message="PREFIX is not set. Start desk from a Termux shell.",
        )
    prefix = Path(prefix_text)
    if not prefix.is_dir():
        return CheckResult(
            name="PREFIX",
            status=FAILED,
            message=f"PREFIX is {prefix}, which is not a directory.",
            hint="Start desk from a Termux shell where PREFIX points to the Termux prefix.",
        )
    return CheckResult(
        name="PREFIX",
        status=OK,
        message=str(prefix),
    )


def check_program(
    name: str,
    prefix: Path | None,
    environ: Mapping[str, str],
    *,
    label: str | None = None,
    hint: str | None = None,
) -> CheckResult:
    """Report whether one program can be found in the prefix or on PATH."""
    display_name = label or name
    found = resolve_program(name, prefix, environ.get("PATH"))
    if found is not None:
        return CheckResult(
            name=display_name,
            status=OK,
            message=found,
        )
    return CheckResult(
        name=display_name,
        status=FAILED,
        message=f"the program '{name}' was not found.",
        hint=hint or "Run 'desk setup'.",
    )


def check_am_program(prefix: Path | None, environ: Mapping[str, str]) -> CheckResult:
    """Report whether the Android activity manager wrapper can be found."""
    found = resolve_program("am", prefix, environ.get("PATH"))
    if found is not None:
        return CheckResult(
            name="am",
            status=OK,
            message=found,
        )
    return CheckResult(
        name="am",
        status=FAILED,
        message="the program 'am' was not found.",
        hint="Run 'desk setup'; it installs termux-am, which provides 'am'.",
    )


def check_proot_distro(prefix: Path | None, environ: Mapping[str, str]) -> CheckResult:
    """Report whether proot-distro can be found.

    Only 'desk container init' needs it, so a missing program is a warning
    and not a failure: the desktop session runs without containers.
    """
    found = resolve_program("proot-distro", prefix, environ.get("PATH"))
    if found is not None:
        return CheckResult(
            name="proot-distro",
            status=OK,
            message=found,
        )
    return CheckResult(
        name="proot-distro",
        status=WARNING,
        message="the program 'proot-distro' was not found.",
        hint=(
            "Install it with 'pkg install proot-distro' to use "
            "'desk container init'."
        ),
    )


def package_is_listed(output: str, package: str) -> bool:
    """Return True when pm output contains the exact package line."""
    marker = f"package:{package}"
    return any(line.strip() == marker for line in output.splitlines())


def check_termux_x11_app(runner: CommandRunner) -> CheckResult:
    """Report whether the Termux:X11 Android app is visible to pm.

    Android 11 and later limit which packages an app can see. A normal
    Termux:X11 build does not share the Termux UID, so pm can hide it even
    when it is installed. A missing entry is therefore a warning, not a
    failure, and desk never installs the app.
    """
    attempts: list[str] = []
    for command in ANDROID_PACKAGE_QUERY_COMMANDS:
        try:
            result = runner.run(command, capture=True)
        except OSError as error:
            attempts.append(f"{' '.join(command)}: {error}")
            continue
        if not result.succeeded:
            attempts.append(f"{' '.join(command)}: exit status {result.returncode}")
            continue
        if package_is_listed(result.stdout, TERMUX_X11_ANDROID_PACKAGE):
            return CheckResult(
                name="Termux:X11 Android app",
                status=OK,
                message=f"{TERMUX_X11_ANDROID_PACKAGE} is visible in the Android package list.",
            )
        return CheckResult(
            name="Termux:X11 Android app",
            status=WARNING,
            message=(
                f"{TERMUX_X11_ANDROID_PACKAGE} is not visible in the Android package list. "
                "Android package visibility can hide an installed app, so this is not proof "
                "that the app is missing."
            ),
            hint=(
                "Open the Termux:X11 app once. If it is not installed, download it from "
                f"{TERMUX_X11_DOWNLOAD_URL}."
            ),
        )
    return CheckResult(
        name="Termux:X11 Android app",
        status=WARNING,
        message=(
            "desk could not ask Android which packages are installed. "
            "This is not proof that the app is missing."
        ),
        hint=(
            "Open the Termux:X11 app once. desk setup does not install it; "
            f"download it from {TERMUX_X11_DOWNLOAD_URL}."
        ),
    )


def check_x11_socket_directory(paths: Paths) -> CheckResult:
    """Report whether $PREFIX/tmp/.X11-unix exists."""
    if paths.prefix is None:
        return CheckResult(
            name="X11 socket directory",
            status=FAILED,
            message="cannot check the directory because PREFIX is not set.",
        )
    directory = paths.x11_socket_dir
    if directory.is_dir():
        return CheckResult(
            name="X11 socket directory",
            status=OK,
            message=str(directory),
        )
    return CheckResult(
        name="X11 socket directory",
        status=WARNING,
        message=f"{directory} does not exist.",
        hint="desk start creates this directory before it starts termux-x11.",
    )


def check_runtime_directory(paths: Paths) -> CheckResult:
    """Report on the private XDG runtime directory that desk start prepares."""
    if paths.prefix is None:
        return CheckResult(
            name="XDG runtime directory",
            status=FAILED,
            message="cannot check the directory because PREFIX is not set.",
        )
    directory = paths.runtime_dir
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return CheckResult(
            name="XDG runtime directory",
            status=OK,
            message=(
                f"{directory} does not exist; desk start creates it with mode 0700."
            ),
        )
    except OSError as error:
        return CheckResult(
            name="XDG runtime directory",
            status=WARNING,
            message=f"could not inspect {directory}: {error}.",
            hint="Check the path and its parent permissions, then run desk doctor again.",
        )

    if stat.S_ISLNK(info.st_mode):
        return CheckResult(
            name="XDG runtime directory",
            status=WARNING,
            message=f"{directory} is a symbolic link; desk start will not use it.",
            hint="Remove the symbolic link and run 'desk start' to create a real directory.",
        )
    if not stat.S_ISDIR(info.st_mode):
        return CheckResult(
            name="XDG runtime directory",
            status=WARNING,
            message=f"{directory} is not a directory.",
            hint="Remove the file and run 'desk start' to create the directory.",
        )

    current_uid = os.getuid()
    if info.st_uid != current_uid:
        return CheckResult(
            name="XDG runtime directory",
            status=WARNING,
            message=(
                f"{directory} is owned by UID {info.st_uid}, "
                f"not the current UID {current_uid}."
            ),
            hint="Change the owner or remove the directory; desk doctor does not modify files.",
        )

    mode = stat.S_IMODE(info.st_mode)
    if mode != 0o700:
        return CheckResult(
            name="XDG runtime directory",
            status=WARNING,
            message=f"{directory} has permissions {mode:04o}, not 0700.",
            hint=(
                "Run 'chmod 700 <directory>' or remove the directory; "
                "desk doctor does not modify files."
            ),
        )
    return CheckResult(
        name="XDG runtime directory",
        status=OK,
        message=f"{directory} (mode 0700).",
    )


def check_display(
    paths: Paths,
    socket_connect: Callable[[Path], bool],
) -> CheckResult:
    """Report on the files and the listener for display :0."""
    if paths.prefix is None:
        return CheckResult(
            name="display :0",
            status=FAILED,
            message="cannot check the display because PREFIX is not set.",
        )
    socket_path = paths.x11_socket
    lock_path = paths.x11_lock
    socket_exists = socket_path.exists()
    lock_exists = lock_path.exists()

    if socket_exists and socket_connect(socket_path):
        return CheckResult(
            name="display :0",
            status=WARNING,
            message=(
                f"an X server already accepts connections at {socket_path}. "
                "desk start refuses to start a second one for the same display."
            ),
            hint="Stop the running X server before you start the desk session.",
        )
    if socket_exists or lock_exists:
        existing = ", ".join(
            str(path) for path in (socket_path, lock_path) if path.exists()
        )
        return CheckResult(
            name="display :0",
            status=WARNING,
            message=f"leftover files for an X server exist: {existing}.",
            hint=(
                "desk start removes these files after it proves that no X server "
                "owns them. desk doctor does not remove files."
            ),
        )
    return CheckResult(
        name="display :0",
        status=OK,
        message=f"no X server files exist for {DISPLAY_NAME}.",
    )


def check_session_state(
    paths: Paths,
    verifier: Callable[[SessionState], Verification],
) -> CheckResult:
    """Report what the state file says and whether it verifies."""
    try:
        state = read_session_state(paths.session_file)
    except StateError as error:
        return CheckResult(
            name="desk session",
            status=WARNING,
            message=str(error),
            hint="Remove the state file if no desk session is running.",
        )
    if state is None:
        return CheckResult(
            name="desk session",
            status=OK,
            message=f"no desk session is recorded ({paths.session_file} does not exist).",
        )

    try:
        verification = verifier(state)
    except DeskError as error:
        return CheckResult(
            name="desk session",
            status=WARNING,
            message=f"desk could not verify the recorded session: {error}",
            hint=(
                "desk will not signal a process group it cannot verify. Check the "
                "process group yourself before you remove the state file."
            ),
        )
    if verification.state == VERIFICATION_RUNNING:
        return CheckResult(
            name="desk session",
            status=OK,
            message=(
                f"a desk session is running: pid {state.pid}, pgid {state.pgid}, "
                f"session {state.session_id}."
            ),
        )
    if verification.state == VERIFICATION_STALE:
        return CheckResult(
            name="desk session",
            status=WARNING,
            message=f"the recorded session is stale: {verification.detail}.",
            hint="Run 'desk stop' to remove the stale state file.",
        )
    return CheckResult(
        name="desk session",
        status=WARNING,
        message=(
            "desk cannot verify the recorded session: "
            f"{verification.detail}."
        ),
        hint=(
            "desk will not signal a process group it cannot verify. Check the "
            "process group yourself before you remove the state file."
        ),
    )


VNC_WEB_DIR_NAME = "vnc-web"


def _vnc_program_check(
    name: str,
    paths: Paths,
    search_path: str | None,
    *,
    required: bool,
    hint: str,
) -> CheckResult:
    """Report one VNC program, failing only when VNC was asked for."""
    executable = resolve_program(name, paths.prefix, search_path)
    if executable is not None:
        return CheckResult(name=f"VNC {name}", status=OK, message=str(executable))
    if required:
        return CheckResult(
            name=f"VNC {name}",
            status=FAILED,
            message="not found.",
            hint=hint,
        )
    return CheckResult(
        name=f"VNC {name}",
        status=OK,
        message="not installed (optional).",
        hint=hint,
    )


def _vnc_asset_check(
    name: str,
    path: Path | None,
    *,
    required: bool,
    hint: str,
) -> CheckResult:
    """Report a downloaded VNC asset; it is optional unless VNC was asked for."""
    if path is not None and path.exists():
        return CheckResult(name=f"VNC {name}", status=OK, message=str(path))
    if required:
        return CheckResult(name=f"VNC {name}", status=WARNING, message="not installed.", hint=hint)
    return CheckResult(
        name=f"VNC {name}",
        status=OK,
        message="not installed (optional).",
        hint=hint,
    )


def collect_vnc_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the VNC domain.

    required is False when desk doctor aggregates them: missing optional
    pieces stay OK, so a machine without VNC is not reported as unhealthy.
    It is True for desk doctor --vnc and desk vnc doctor: the essential
    programs then fail and the downloaded assets warn.
    """
    from . import vnc, webproxy

    prefix = paths.prefix
    web_root = (
        prefix / "share" / "termux-desktop" / VNC_WEB_DIR_NAME
        if prefix is not None
        else None
    )
    page = None if web_root is None else web_root / "novnc" / "vnc.html"
    rs_proxy = None if web_root is None else web_root / "websockify_rs"
    py_proxy = None if web_root is None else web_root / "websockify"

    search_path = environ.get("PATH")
    checks = [
        _vnc_program_check(
            "Xvnc", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "x0vncserver", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "vncpasswd", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "darkhttpd", paths, search_path, required=False,
            hint="Run 'desk vnc setup' to install darkhttpd for the rs proxy.",
        ),
        _vnc_asset_check(
            "noVNC page", page, required=required,
            hint="Run 'desk vnc web setup' to download noVNC.",
        ),
    ]

    installed_proxies = [
        label
        for label, path in (("rs", rs_proxy), ("py", py_proxy))
        if path is not None and path.exists()
    ]
    if installed_proxies:
        checks.append(
            CheckResult(
                name="VNC websocket proxies",
                status=OK,
                message="installed: " + ", ".join(installed_proxies) + ".",
            )
        )
    else:
        checks.append(
            _vnc_asset_check(
                "websocket proxies", None, required=required,
                hint="Run 'desk vnc web setup' to download a proxy.",
            )
        )

    try:
        mirror_state = vnc.read_mirror_state(paths, vnc.MIRROR_DISPLAY)
    except vnc.VncError as error:
        checks.append(
            CheckResult(
                name="VNC mirror state",
                status=WARNING,
                message=str(error),
                hint="Remove the mirror state file if no mirror is running.",
            )
        )
    else:
        if mirror_state is None:
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=OK,
                    message="no mirror is recorded.",
                )
            )
        elif pid_alive(mirror_state.pid):
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=OK,
                    message=f"mirroring :0 with pid {mirror_state.pid}.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=WARNING,
                    message=f"the recorded mirror is stale (pid {mirror_state.pid}).",
                    hint="Run 'desk vnc stop --display :0' to remove the stale state.",
                )
            )

    stale_web: list[str] = []
    try:
        displays = webproxy.web_displays(paths)
    except (DeskError, OSError) as error:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=WARNING,
                message=str(error),
            )
        )
        return tuple(checks)
    for display in displays:
        try:
            state = webproxy.read_web_state(paths, display)
        except (DeskError, OSError) as error:
            stale_web.append(f"{display} ({error})")
            continue
        if state is None or pid_alive(state.web_pid):
            continue
        stale_web.append(display)
    if stale_web:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=WARNING,
                message="stale state for " + ", ".join(stale_web) + ".",
                hint="Run 'desk vnc stop --all' to remove stale web state.",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=OK,
                message="no stale web state is recorded.",
            )
        )

    return tuple(checks)




def collect_container_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
    runner: CommandRunner | None = None,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the container domain.

    required is False when desk doctor aggregates them: a machine without
    proot-distro or containers stays healthy. It is True for
    desk doctor --container: a missing proot-distro then fails.
    """
    from . import container as container_module

    prefix = paths.prefix
    if prefix is None:
        return (CheckResult(name="containers", status=FAILED, message="PREFIX is not set."),)
    search_path = environ.get("PATH")
    proot_distro = resolve_program(
        container_module.PROOT_DISTRO_PROGRAM, prefix, search_path
    )
    if proot_distro is None:
        return (
            CheckResult(
                name="containers",
                status=FAILED if required else OK,
                message=(
                    "proot-distro was not found."
                    if required
                    else "not installed (optional)."
                ),
                hint="Run 'pkg install proot-distro' to create containers.",
            ),
        )
    commands = runner if runner is not None else CommandRunner()
    try:
        names = container_module.list_containers(proot_distro, commands)
    except OSError as error:
        return (
            CheckResult(
                name="containers",
                status=FAILED if required else WARNING,
                message=f"could not list the containers: {error}",
            ),
        )
    if not names:
        return (CheckResult(name="containers", status=OK, message="no containers are installed."),)
    results: list[CheckResult] = []
    for name in names:
        rootfs = container_module.rootfs_path(prefix, name)
        release = container_module.read_container_release(prefix, name)
        if release is None:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=FAILED,
                    message="the rootfs has no readable /etc/os-release.",
                    hint="Recreate the container with 'desk container init NAME'.",
                )
            )
            continue
        label = container_module.distro_label(release)
        entries = container_module.desktop_entry_count(rootfs)
        missing = [
            point
            for point in container_module.MOUNT_POINTS
            if not (rootfs / point.lstrip("/")).is_dir()
        ]
        if missing:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=WARNING,
                    message=(
                        f"{label}, {entries} desktop entries; missing mounts: "
                        f"{', '.join(missing)}."
                    ),
                    hint="Run 'desk container init NAME' to create the mount points.",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=OK,
                    message=f"{label}, {entries} desktop entries, mounts ready.",
                )
            )
    return tuple(results)


def collect_font_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
    matcher: object | None = None,
    locale: str | None = None,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the font domain.

    required is False when desk doctor aggregates them: a machine that never
    ran desk setup stays healthy, because the file and the library are only
    written or installed by explicit commands. desk doctor --font fails a
    missing fontconfig library and warns about the missing preference file.
    """
    from . import fonts

    prefix = paths.prefix
    if prefix is None:
        return (CheckResult(name="fonts", status=FAILED, message="PREFIX is not set."),)
    checks: list[CheckResult] = []
    library = prefix / "lib" / "libfontconfig.so"
    if library.is_file():
        checks.append(CheckResult(name="fontconfig", status=OK, message=str(library)))
    else:
        checks.append(
            CheckResult(
                name="fontconfig",
                status=FAILED if required else OK,
                message=(
                    "the fontconfig library was not found."
                    if required
                    else "not installed (optional)."
                ),
                hint="Run 'desk setup' to install the font stack.",
            )
        )

    try:
        preference = fonts.preference_path(paths)
    except DeskError:
        preference = None
    if preference is not None and preference.is_file():
        text = preference.read_text(encoding="utf-8", errors="replace")
        if fonts.GENERATED_MARK in text:
            checks.append(
                CheckResult(
                    name="font preference",
                    status=OK,
                    message=f"{preference} (written by desk setup)",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="font preference",
                    status=WARNING,
                    message=f"{preference} has content that desk setup did not write.",
                    hint="Move it aside to let desk setup write the preference.",
                )
            )
    else:
        checks.append(
            CheckResult(
                name="font preference",
                status=WARNING if required else OK,
                message=(
                    f"not written at {preference}; run 'desk setup'."
                    if required
                    else "not written (optional)."
                ),
                hint="Run 'desk setup' to write the CJK font preference.",
            )
        )

    tag = (
        fonts.language_tag(locale)
        if locale is not None
        else fonts.detected_language(environ=environ)
    )
    language = tag if tag in fonts.SANS_CANDIDATES else fonts.DEFAULT_LANGUAGE
    checks.append(
        CheckResult(
            name="font language",
            status=OK,
            message=f"{tag or 'unknown'} (preference language: {language})",
        )
    )

    if matcher is None and library.is_file():
        try:
            matcher = fonts.FontconfigMatcher(library)
        except OSError as error:
            checks.append(
                CheckResult(
                    name="font families",
                    status=WARNING,
                    message=f"could not probe fontconfig: {error}",
                )
            )
    if matcher is not None:
        sans, serif, mono = fonts.choose_families(matcher, language)
        if sans is None:
            checks.append(
                CheckResult(
                    name="font families",
                    status=WARNING,
                    message=f"no CJK sans-serif face for {language}.",
                    hint="Install a CJK font for the device language.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="font families",
                    status=OK,
                    message=(
                        f"sans-serif {sans}, serif {serif or 'none'}, "
                        f"monospace {mono}"
                    ),
                )
            )
    return tuple(checks)


def collect_checks(
    paths: Paths,
    runner: CommandRunner,
    *,
    environ: Mapping[str, str],
    platform_facts: PlatformFacts,
    socket_connect: Callable[[Path], bool],
    session_verifier: Callable[[SessionState], Verification],
) -> tuple[CheckResult, ...]:
    """Run every doctor check in a fixed order."""
    return (
        check_termux_environment(platform_facts),
        check_android_system(platform_facts),
        check_prefix(environ),
        check_program("termux-x11", paths.prefix, environ),
        check_program("startxfce4", paths.prefix, environ),
        check_program("dbus-launch", paths.prefix, environ),
        check_am_program(paths.prefix, environ),
        check_proot_distro(paths.prefix, environ),
        check_termux_x11_app(runner),
        check_x11_socket_directory(paths),
        check_runtime_directory(paths),
        check_display(paths, socket_connect),
        check_session_state(paths, session_verifier),
    )


def default_session_verifier() -> Callable[[SessionState], Verification]:
    """Build a verifier that reads the current process table."""
    runtime = SessionRuntime()

    def verify(state: SessionState) -> Verification:
        return verify_session(state, runtime)

    return verify


def stream_is_a_terminal(out: TextIO) -> bool:
    """Return True when the stream says that it is a terminal.

    Not every stream has isatty, and isatty can raise, for example on a
    closed stream or on a stream that does not support terminal detection.
    Any of those cases means the stream is not known to be a terminal.
    """
    isatty = getattr(out, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def color_enabled(choice: str, out: TextIO, environ: Mapping[str, str]) -> bool:
    """Return whether command output may contain ANSI color.

    "always" and "never" ignore the stream and the environment. "auto" uses
    color only when the stream is a terminal, TERM is not "dumb", and
    NO_COLOR is unset or empty.
    """
    if choice == COLOR_ALWAYS:
        return True
    if choice == COLOR_NEVER:
        return False
    if not stream_is_a_terminal(out):
        return False
    if environ.get("TERM") == "dumb":
        return False
    if environ.get("NO_COLOR"):
        return False
    return True


def format_status_tag(status: str, colored: bool) -> str:
    """Return the status tag and the spaces that align the check name.

    The tag is colored on its own. The alignment spaces stay plain so that
    the name starts in the same column whatever the tag length is.
    """
    tag = f"[{status}]"
    padding = " " * (NAME_START - len(tag))
    if colored:
        tag = f"{STATUS_COLORS[status]}{tag}{ANSI_RESET}"
    return f"{tag}{padding}"


def format_summary(
    ok_count: int,
    warning_count: int,
    failure_count: int,
    colored: bool,
) -> str:
    """Return the summary line, colored by the most severe status."""
    text = f"Summary: {ok_count} ok, {warning_count} warning, {failure_count} failure."
    if not colored:
        return text
    if failure_count:
        color = STATUS_COLORS[FAILED]
    elif warning_count:
        color = STATUS_COLORS[WARNING]
    else:
        color = STATUS_COLORS[OK]
    return f"{color}{text}{ANSI_RESET}"


def run_doctor(
    paths: Paths,
    runner: CommandRunner | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    platform_facts: PlatformFacts | None = None,
    socket_connect: Callable[[Path], bool] | None = None,
    session_verifier: Callable[[SessionState], Verification] | None = None,
    out: TextIO | None = None,
    color: str = COLOR_AUTO,
    vnc_only: bool = False,
    container_only: bool = False,
    font_only: bool = False,
) -> int:
    """Run every check, print the report, and return the exit status."""
    output = sys.stdout if out is None else out
    environment = os.environ if environ is None else environ
    use_color = color_enabled(color, output, environment)
    commands_runner = runner if runner is not None else CommandRunner()
    facts = platform_facts if platform_facts is not None else read_platform_facts(environment)
    connector = socket_connect if socket_connect is not None else unix_socket_accepts_connection
    verifier = session_verifier if session_verifier is not None else default_session_verifier()

    filtered = vnc_only or container_only or font_only
    if filtered:
        results = ()
    else:
        results = collect_checks(
            paths,
            commands_runner,
            environ=environment,
            platform_facts=facts,
            socket_connect=connector,
            session_verifier=verifier,
        )
    if vnc_only or not filtered:
        results += collect_vnc_checks(paths, environ=environment, required=vnc_only)
    if container_only or not filtered:
        results += collect_container_checks(
            paths,
            environ=environment,
            required=container_only,
            runner=commands_runner,
        )
    if font_only or not filtered:
        results += collect_font_checks(
            paths,
            environ=environment,
            required=font_only,
        )

    for result in results:
        tag = format_status_tag(result.status, use_color)
        print(f"{tag}{result.name}: {result.message}", file=output)
        if result.hint:
            print(f"{' ' * NAME_START}hint: {result.hint}", file=output)

    ok_count = sum(1 for result in results if result.status == OK)
    warning_count = sum(1 for result in results if result.status == WARNING)
    failure_count = sum(1 for result in results if result.status == FAILED)
    print("", file=output)
    print(
        format_summary(ok_count, warning_count, failure_count, use_color),
        file=output,
    )
    return 1 if failure_count else 0
