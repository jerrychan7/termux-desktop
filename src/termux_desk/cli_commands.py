"""Command handlers for desk.

Handlers receive the resolved Paths, the parsed arguments, and the output
stream. They return the exit status and raise DeskError for reported failures.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import TextIO

from . import (
    __version__,
    app,
    container,
    doctor,
    fonts,
    install,
    menu,
    pkg,
    session,
    setup,
    upgrade,
    vnc,
    webproxy,
)
from .config import DISPLAY_NAME, DeskError, Paths
from .state import read_log_tail, read_session_state

SESSION_LOG_MARKERS = re.compile(
    r"(?P<error>\b(?:error|failed|failure|fatal|traceback)\b)"
    r"|(?P<error_phrase>\b(?:not\s+found|exited\s+before|did\s+not\s+appear|could\s+not)\b)"
    r"|(?P<status>\b(?:finished|exited)\b with status\s+(?P<status_number>[+-]?\d+))"
    r"|(?P<warning>\b(?:warning|warn|interrupted)\b)",
    re.IGNORECASE,
)
DESK_LOG_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2}T\S+)(?: (.*))?$")
DESK_LOG_MARKERS = re.compile(
    r"(?P<failed>\bfailed\b)"
    r"|(?P<status>(?:finished|exited) with status\s+(?P<status_number>[+-]?\d+))"
    r"|(?P<interrupted>\binterrupted\b)"
    r"|(?P<warning>\bwarn(?:ing)?\b)",
    re.IGNORECASE,
)


def _session_message_color(match: re.Match[str]) -> str | None:
    """Return the color for one marked word in a session log line."""
    if match.group("error") or match.group("error_phrase"):
        return doctor.ANSI_RED
    if match.group("status"):
        if int(match.group("status_number")) != 0:
            return doctor.ANSI_RED
        return None
    return doctor.ANSI_YELLOW


def _highlight_session_message(message: str) -> str:
    """Color only the marked words and phrases in one session log message."""
    pieces: list[str] = []
    last = 0
    for match in SESSION_LOG_MARKERS.finditer(message):
        color = _session_message_color(match)
        if color is None:
            continue
        pieces.append(message[last:match.start()])
        pieces.append(f"{color}{match.group(0)}{doctor.ANSI_RESET}")
        last = match.end()
    if not pieces:
        return message
    pieces.append(message[last:])
    return "".join(pieces)


def _format_session_log_line(line: str) -> str:
    """Color a session log line: marker, marked words, or a whole separator."""
    newline = ""
    content = line
    if content.endswith("\n"):
        content = content[:-1]
        newline = "\n"
    header = content.lower().strip()
    if header.startswith("=== desk") and header.endswith("==="):
        return f"{doctor.ANSI_SEPARATOR}{content}{doctor.ANSI_RESET}{newline}"
    marker = ""
    body = content
    if content.lower().startswith("[desk]"):
        marker = f"{doctor.ANSI_CYAN}{content[:6]}{doctor.ANSI_RESET}"
        body = content[6:]
    return f"{marker}{_highlight_session_message(body)}{newline}"


def _desk_message_color(match: re.Match[str]) -> str | None:
    """Return the color for one marked word in a command log message."""
    if match.group("failed"):
        return doctor.ANSI_RED
    if match.group("status"):
        if int(match.group("status_number")) != 0:
            return doctor.ANSI_RED
        return None
    return doctor.ANSI_YELLOW


def _highlight_desk_message(message: str) -> str:
    """Color only the marked words in one command log message."""
    pieces: list[str] = []
    last = 0
    for match in DESK_LOG_MARKERS.finditer(message):
        color = _desk_message_color(match)
        if color is None:
            continue
        pieces.append(message[last:match.start()])
        pieces.append(f"{color}{match.group(0)}{doctor.ANSI_RESET}")
        last = match.end()
    if not pieces:
        return message
    pieces.append(message[last:])
    return "".join(pieces)


def _format_desk_log_line(line: str) -> str:
    """Color a command log line: gray timestamp and marked message words."""
    newline = ""
    content = line
    if content.endswith("\n"):
        content = content[:-1]
        newline = "\n"
    match = DESK_LOG_ENTRY.match(content)
    if match is None:
        return f"{_highlight_desk_message(content)}{newline}"
    stamp = match.group(1)
    message = match.group(2)
    colored_stamp = f"{doctor.ANSI_GRAY}{stamp}{doctor.ANSI_RESET}"
    if message is None:
        return f"{colored_stamp}{newline}"
    return f"{colored_stamp} {_highlight_desk_message(message)}{newline}"


def _paint(text: str, color: str, enabled: bool) -> str:
    """Wrap text in ANSI color when enabled, otherwise return it unchanged."""
    if not enabled:
        return text
    return f"{color}{text}{doctor.ANSI_RESET}"


def _handle_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    setup.run_setup(paths, out=output)
    if arguments.vnc:
        vnc.run_vnc_setup(paths, out=output)
    return 0


def _handle_upgrade(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    upgrade.run_upgrade(paths, out=output)
    return 0


def _handle_font_status(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return fonts.run_font_status(paths, out=output)


def _handle_font_remove(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    fonts.remove_preference(paths, out=output)
    return 0


def _handle_container_list(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return container.run_container_list(paths, out=output)


def _handle_container_status(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    return container.run_container_status(paths, arguments.name, out=output)


def _handle_container_init(
    paths: Paths, arguments: argparse.Namespace, output: TextIO
) -> int:
    container.run_container_init(
        paths, arguments.name, image=arguments.image, out=output
    )
    return 0


def _handle_app_launch(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    extra = list(arguments.arguments)
    if extra and extra[0] == "--":
        extra = extra[1:]
    app.run_app_launch(paths, arguments.target, out=output, arguments=extra)
    return 0


def _handle_app_refresh(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    menu.run_refresh(paths, out=output)
    return 0


def _handle_app_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return menu.run_app_doctor(
        paths,
        container=arguments.container,
        out=output,
    )


def _handle_pkg(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return pkg.run_pkg(
        paths,
        arguments.container,
        list(arguments.arguments),
        out=output,
    )


def _handle_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return doctor.run_doctor(
        paths,
        out=output,
        color=arguments.color,
        vnc_only=arguments.vnc,
        container_only=arguments.container,
        font_only=arguments.font,
    )


def _handle_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    menu.refresh_for_session(paths, out=output)
    session.start_session(paths, out=output, color=use_color)
    return 0


def _handle_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.display:
        targets = (paths.with_display(arguments.display),)
    else:
        targets = tuple(paths.with_display(display) for display in session.session_displays(paths))
    if not targets:
        targets = (paths,)
    running = False
    first = True
    for target in targets:
        report = session.session_status(target)
        if not first:
            print(file=output)
        first = False
        for line in report.lines:
            print(line, file=output)
        running = running or report.running
    return 0 if running else 1


def _stop_display(paths: Paths, display: str, output: TextIO) -> None:
    """Stop the web proxy, the :0 mirror, and the session of one display."""
    failures: list[str] = []
    try:
        if webproxy.read_web_state(paths, display) is not None:
            webproxy.run_web_stop(
                paths, display=display, out=output, session_stopped=True
            )
    except DeskError as error:
        failures.append(str(error))
    if display == vnc.MIRROR_DISPLAY:
        try:
            if vnc.read_mirror_state(paths, display) is not None:
                vnc.run_mirror_stop(paths, display=display, out=output)
        except DeskError as error:
            failures.append(str(error))
    try:
        session.stop_session(paths.with_display(display), out=output)
    except DeskError as error:
        failures.append(str(error))
    if failures:
        raise session.StopError("; ".join(failures))


def _handle_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.all:
        web_displays = webproxy.web_displays(paths)
        mirror_displays = vnc.mirror_displays(paths)
        session_displays = session.session_displays(paths)
        if not web_displays and not mirror_displays and not session_displays:
            print("No desktop session is recorded.", file=output)
            return 0
        failures: list[str] = []
        for display in web_displays:
            try:
                webproxy.run_web_stop(
                    paths, display=display, out=output, session_stopped=True
                )
            except DeskError as error:
                failures.append(f"{display}: {error}")
        for display in mirror_displays:
            try:
                vnc.run_mirror_stop(paths, display=display, out=output)
            except DeskError as error:
                failures.append(f"{display}: {error}")
        for display in session_displays:
            try:
                session.stop_session(paths.with_display(display), out=output)
            except DeskError as error:
                failures.append(f"{display}: {error}")
        if failures:
            raise session.StopError("; ".join(failures))
        return 0
    _stop_display(paths, arguments.display or DISPLAY_NAME, output)
    return 0


def _handle_vnc_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    vnc.run_vnc_setup(paths, out=output)
    if arguments.web:
        webproxy.run_web_setup(paths, out=output)
    return 0


def _handle_vnc_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    if arguments.mirror:
        if arguments.display != vnc.MIRROR_DISPLAY:
            raise vnc.VncError(
                f"--mirror needs --display {vnc.MIRROR_DISPLAY}; "
                f"display {arguments.display} is for a separate VNC session"
            )
        if arguments.geometry != vnc.VNC_GEOMETRY or arguments.password_file:
            raise vnc.VncError(
                "--geometry and --password-file are not used with --mirror: "
                "the mirror follows the running desktop"
            )
        vnc.run_mirror_start(
            paths,
            display=arguments.display,
            out=output,
            view_only=arguments.view_only,
        )
        return 0
    if arguments.view_only:
        raise vnc.VncError("--view-only is only used with --mirror")
    if arguments.display == vnc.MIRROR_DISPLAY:
        raise vnc.VncError(
            f"{vnc.MIRROR_DISPLAY} already runs the termux-x11 desktop; "
            "add --mirror to view it, or choose another display for a new session"
        )
    password_file = Path(arguments.password_file) if arguments.password_file else None
    vnc.run_vnc_start(
        paths,
        display=arguments.display,
        geometry=arguments.geometry,
        password_file=password_file,
        out=output,
        color=use_color,
    )
    return 0


def _handle_vnc_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    vnc.run_vnc_stop(paths, display=arguments.display, out=output)
    return 0


def _vnc_displays(paths: Paths, display: str | None) -> tuple[str, ...]:
    """Return the displays that belong to the VNC domain."""
    if display is not None:
        return (display,)
    displays: set[str] = set()
    for candidate in session.session_displays(paths):
        record = read_session_state(paths.with_display(candidate).session_file)
        if record is not None and record.backend == session.BACKEND_VNC:
            displays.add(candidate)
    displays.update(vnc.mirror_displays(paths))
    displays.update(webproxy.web_displays(paths))
    return tuple(sorted(displays, key=lambda value: int(value[1:])))


def _handle_vnc_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    displays = _vnc_displays(paths, arguments.display)
    if not displays:
        print("No VNC display is recorded.", file=output)
        return 1
    any_running = False
    for index, display in enumerate(displays):
        if index:
            print(file=output)
        if arguments.display is None:
            print(f"Display {display}:", file=output)
        report = vnc.run_vnc_status(paths, display=display)
        for line in report.lines:
            print(line, file=output)
        running = report.running
        if webproxy.read_web_state(paths, display) is not None:
            running = webproxy.run_web_status(paths, display=display, out=output) or running
        else:
            print(f"The web proxy for {display} is not running.", file=output)
        any_running = any_running or running
    return 0 if any_running else 1


def _vnc_log_path(paths: Paths, display: str, *, web: bool) -> Path:
    if web:
        return webproxy.log_path(paths, display)
    if display == vnc.MIRROR_DISPLAY:
        return vnc.mirror_log_path(paths, display)
    return paths.with_display(display).session_log


def _handle_vnc_version(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    vnc.run_vnc_version(paths, out=output)
    return 0


def _handle_vnc_doctor(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return vnc.run_vnc_doctor(paths, out=output, color=arguments.color)


def _handle_vnc_logs(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.lines == 0:
        return 0
    if arguments.display is not None:
        displays = (arguments.display,)
    elif arguments.web:
        displays = webproxy.web_displays(paths)
    else:
        displays = _vnc_displays(paths, None)
    if not displays:
        print("No VNC display is recorded.", file=output)
        return 1
    for display in displays:
        if len(displays) > 1:
            kind = "web" if arguments.web else (
                "mirror" if display == vnc.MIRROR_DISPLAY else "session"
            )
            print(f"== {display} {kind} log ==", file=output)
        log_path = _vnc_log_path(paths, display, web=arguments.web)
        lines = read_log_tail(log_path, arguments.lines)
        if lines is None:
            print(f"No log file at {log_path}.", file=output)
            continue
        if not lines:
            print(f"The log file {log_path} is empty.", file=output)
            continue
        text = "".join(lines)
        output.write(text)
        if not text.endswith("\n"):
            output.write("\n")
    return 0


def _handle_vnc_web_setup(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    webproxy.run_web_setup(paths, proxy=arguments.proxy, force=arguments.force, out=output)
    return 0


def _handle_vnc_web_start(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    webproxy.run_web_start(
        paths,
        display=arguments.display,
        proxy=arguments.proxy,
        host=arguments.host,
        port=arguments.port,
        color=use_color,
        out=output,
        view_only=arguments.view_only,
    )
    return 0


def _handle_vnc_web_stop(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    webproxy.run_web_stop(paths, display=arguments.display, out=output)
    return 0


def _handle_vnc_web_status(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    return 0 if webproxy.run_web_status(paths, display=arguments.display, out=output) else 1


def _handle_logs(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.lines == 0:
        return 0
    target = paths.with_display(arguments.display)
    log_path = target.desk_log if arguments.desk else target.session_log
    lines = read_log_tail(log_path, arguments.lines)
    if lines is None:
        print(f"No log file at {log_path}.", file=output)
        return 0
    if not lines:
        print(f"The log file {log_path} is empty.", file=output)
        return 0
    use_color = doctor.color_enabled(arguments.color, output, os.environ)
    if use_color:
        if arguments.desk:
            text = "".join(_format_desk_log_line(line) for line in lines)
        else:
            text = "".join(_format_session_log_line(line) for line in lines)
    else:
        text = "".join(lines)
    output.write(text)
    if not lines[-1].endswith("\n"):
        output.write("\n")
    return 0


def _handle_install(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_install(
        paths,
        Path(arguments.source),
        force=arguments.force,
        release=arguments.release,
        out=output,
    )
    return 0


def _handle_uninstall(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_uninstall(paths, purge=arguments.purge, out=output)
    return 0


def _handle_update(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    install.run_update(
        paths,
        version=arguments.version,
        check=arguments.check,
        force=arguments.force,
        out=output,
    )
    return 0


def _handle_version(paths: Paths, arguments: argparse.Namespace, output: TextIO) -> int:
    if arguments.vnc:
        vnc.run_vnc_version(paths, out=output)
        return 0
    installed = install.read_installed(paths)
    print(f"desk {__version__ if installed is None else installed.version}", file=output)
    if installed is None:
        print("not installed (running from a checkout)", file=output)
    else:
        print(f"source: {installed.source}", file=output)
        print(f"installed in {paths.install_root}", file=output)
    print("", file=output)
    if vnc.vnc_components_installed(paths):
        for line in vnc.version_lines(paths):
            print(line, file=output)
    else:
        print("VNC: not installed (optional).", file=output)
    return 0
