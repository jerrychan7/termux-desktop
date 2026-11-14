"""Argument parser for desk.

The parser only describes the command line; the handlers it points at live in
cli_commands and the entry point lives in cli.
"""

from __future__ import annotations

import argparse

from . import __version__, container, doctor, vnc
from .cli_commands import (
    _handle_app_doctor,
    _handle_app_launch,
    _handle_app_refresh,
    _handle_container_init,
    _handle_container_list,
    _handle_container_status,
    _handle_doctor,
    _handle_font_remove,
    _handle_font_status,
    _handle_install,
    _handle_logs,
    _handle_pkg,
    _handle_setup,
    _handle_start,
    _handle_status,
    _handle_stop,
    _handle_uninstall,
    _handle_update,
    _handle_upgrade,
    _handle_version,
    _handle_vnc_doctor,
    _handle_vnc_logs,
    _handle_vnc_setup,
    _handle_vnc_start,
    _handle_vnc_status,
    _handle_vnc_stop,
    _handle_vnc_version,
)
from .cli_web_parser import add_vnc_web_commands
from .config import DISPLAY_NAME

DEFAULT_LOG_LINES = 100


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the M0 commands and help."""
    parser = argparse.ArgumentParser(
        prog="desk",
        description="Manage one XFCE desktop session on Termux.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"desk {__version__}",
        help="print the desk version and exit",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    _add_setup_commands(subparsers)
    _add_container_commands(subparsers)
    _add_app_commands(subparsers)
    _add_pkg_command(subparsers)
    _add_doctor_command(subparsers)
    _add_session_commands(subparsers)
    _add_vnc_commands(subparsers)
    _add_install_commands(subparsers)
    return parser


def _add_setup_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the setup commands."""
    setup_parser = subparsers.add_parser(
        "setup",
        help="install the fixed Termux packages that the desktop needs",
        description=(
            "Install x11-repo, termux-x11-nightly, xfce4, dbus, and termux-am. "
            "Only missing packages are installed and nothing is upgraded. "
            "After the packages, desk setup writes a font preference file that "
            "names the CJK faces installed on the device for its language, and "
            "language rules for the other CJK languages."
        ),
    )
    setup_parser.add_argument(
        "--vnc",
        action="store_true",
        help="also install the packages desk vnc needs",
    )
    setup_parser.set_defaults(handler=_handle_setup)

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="refresh the package index and upgrade the installed X11 packages",
        description=(
            "Refresh the package index with pkg update, then upgrade only the "
            "installed packages that the X11 repository provides. Packages "
            "from termux-main are not upgraded directly, but apt may upgrade "
            "one when an X11 package needs it."
        ),
    )
    upgrade_parser.set_defaults(handler=_handle_upgrade)

    font_parser = subparsers.add_parser(
        "font",
        help="manage the font preference that desk setup writes",
        description=(
            "desk setup writes a fontconfig file that names the CJK faces "
            "installed on the device for its language. desk font remove "
            "deletes that file. A file that desk setup did not write is left "
            "alone."
        ),
    )
    font_subparsers = font_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    font_remove_parser = font_subparsers.add_parser(
        "remove",
        help="remove the font preference file written by desk setup",
        description=(
            "Delete $PREFIX/etc/fonts/conf.d/63-cjk-prefer.conf when it "
            "carries the marker that desk setup writes."
        ),
    )
    font_remove_parser.set_defaults(handler=_handle_font_remove)

    font_status_parser = font_subparsers.add_parser(
        "status",
        help="report the detected language and the preferred font families",
        description=(
            "Print the Android language desk setup detected, the preference "
            "file it would write or has written, and the families that are "
            "actually installed on this device."
        ),
    )
    font_status_parser.set_defaults(handler=_handle_font_status)



def _add_container_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the container commands."""
    container_parser = subparsers.add_parser(
        "container",
        help="create and prepare proot containers for GUI applications",
        description=(
            "Create a container with proot-distro and prepare it so its GUI "
            "applications can display on the desktop."
        ),
    )
    container_subparsers = container_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    init_parser = container_subparsers.add_parser(
        "init",
        help="create a container if needed and prepare it for GUI applications",
        description=(
            "Create the container when it does not exist, then create the mount "
            "points and install an X client and fonts inside it. Running it "
            "again only repeats the missing steps."
        ),
    )
    init_parser.add_argument("name", metavar="NAME", help="the container name")
    init_parser.add_argument(
        "--image",
        default=container.DEFAULT_IMAGE,
        metavar="IMAGE",
        help=f"the image to create the container from (default: {container.DEFAULT_IMAGE})",
    )
    init_parser.set_defaults(handler=_handle_container_init)

    list_parser = container_subparsers.add_parser(
        "list",
        help="list the installed containers",
        description=(
            "Print the installed proot-distro containers with their distribution, "
            "architecture, desktop entry count, and rootfs path."
        ),
    )
    list_parser.set_defaults(handler=_handle_container_list)

    status_parser = container_subparsers.add_parser(
        "status",
        help="report one container without changing it",
        description=(
            "Print the distribution, rootfs, desktop entry count, and mount "
            "points of one container."
        ),
    )
    status_parser.add_argument("name", metavar="NAME", help="the container name")
    status_parser.set_defaults(handler=_handle_container_status)



def _add_app_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the app commands."""
    app_parser = subparsers.add_parser(
        "app",
        help="launch GUI applications from proot containers",
        description="Run a container application on the display of the desk session.",
    )
    app_subparsers = app_parser.add_subparsers(
        dest="action", required=True, metavar="action"
    )
    launch_parser = app_subparsers.add_parser(
        "launch",
        help="launch one application from a container",
        description=(
            "Run the application inside the container on the display of the "
            "running desk session. The application stays in the process group "
            "of the caller, so desk stop reaches it too."
        ),
    )
    launch_parser.add_argument(
        "target",
        metavar="CONTAINER/APP",
        help="the container name and the program name, for example ubuntu-desktop-test/xterm",
    )
    launch_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        metavar="-- ARG",
        help="arguments passed to the program after '--'",
    )
    launch_parser.set_defaults(handler=_handle_app_launch)

    refresh_parser = app_subparsers.add_parser(
        "refresh",
        help="scan the containers and rewrite the generated menu entries",
        description=(
            "Read /usr/share/applications in every installed container and write "
            "one menu entry per application into the per-user applications "
            "directory. Entries written by an earlier refresh are removed."
        ),
    )
    refresh_parser.set_defaults(handler=_handle_app_refresh)

    app_doctor_parser = app_subparsers.add_parser(
        "doctor",
        help="check that the Exec programs of the menu entries resolve",
        description=(
            "Scan the containers like app refresh does and report the "
            "applications whose command is not found. Nothing is written."
        ),
    )
    app_doctor_parser.add_argument(
        "--container",
        default=None,
        metavar="NAME",
        help="check only this container (default: every container)",
    )
    app_doctor_parser.set_defaults(handler=_handle_app_doctor)



def _add_pkg_command(subparsers: argparse._SubParsersAction) -> None:
    """Add the pkg commands."""
    pkg_parser = subparsers.add_parser(
        "pkg",
        help="run the package manager of a container",
        description=(
            "Forward the arguments to the package manager of the container, "
            "then refresh the menu entries. desk adds no flags: the package "
            "manager keeps its own defaults, prompts, output, and exit status. "
            "Use 'add' for install and 'rm' for remove."
        ),
    )
    pkg_parser.add_argument("container", metavar="CONTAINER", help="the container name")
    pkg_parser.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        metavar="ARGS",
        help="arguments for the container package manager, for example install gimp",
    )
    pkg_parser.set_defaults(handler=_handle_pkg)



def _add_doctor_command(subparsers: argparse._SubParsersAction) -> None:
    """Add the doctor commands."""
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the environment and the recorded session without changing anything",
        description=(
            "Report missing programs, display state, and session state. The "
            "proot container and CJK font domains are aggregated by default; "
            "--vnc, --container, and --font run only that domain."
        ),
    )
    doctor_parser.add_argument(
        "--vnc",
        action="store_true",
        help="run only the VNC checks; same as 'desk vnc doctor'",
    )
    doctor_parser.add_argument(
        "--container",
        action="store_true",
        help="run only the proot container checks",
    )
    doctor_parser.add_argument(
        "--font",
        action="store_true",
        help="run only the CJK font preference checks",
    )
    doctor_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the status tags and the summary line "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    doctor_parser.set_defaults(handler=_handle_doctor)



def _add_session_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the session commands."""
    subparsers.add_parser(
        "help",
        help="show the top-level help message and exit",
        add_help=False,
    )

    start_parser = subparsers.add_parser(
        "start",
        help="start the desktop session",
        description=(
            "Start termux-x11, the Termux:X11 Android app, and XFCE in one new "
            "process group. desk start never installs packages."
        ),
    )
    start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the progress and error output "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    start_parser.set_defaults(handler=_handle_start)

    status_parser = subparsers.add_parser(
        "status",
        help="report whether recorded sessions are running",
        description=(
            "Report every recorded session, or only the session on DISPLAY. "
            "Exit with 0 while at least one session runs and with 1 otherwise."
        ),
    )
    status_parser.add_argument(
        "--display",
        metavar="DISPLAY",
        help="report only this X display, e.g. :0 or :1 (default: every session)",
    )
    status_parser.set_defaults(handler=_handle_status)

    stop_parser = subparsers.add_parser(
        "stop",
        help="stop a display's web proxy, :0 mirror, and session, or every display",
        description=(
            "Stop the web proxy of the display, the :0 mirror, then the session "
            "process group (SIGTERM, then SIGKILL after the timeout). desk does "
            "not search for processes by name."
        ),
    )
    stop_group = stop_parser.add_mutually_exclusive_group()
    stop_group.add_argument(
        "--display",
        metavar="DISPLAY",
        help="stop the web proxy, the :0 mirror, and the session on this display (default: :0)",
    )
    stop_group.add_argument(
        "--all",
        action="store_true",
        help="stop every web proxy, every mirror, and every recorded session",
    )
    stop_parser.set_defaults(handler=_handle_stop)

    logs_parser = subparsers.add_parser(
        "logs",
        help="print the end of the session log",
        description="Print the last 100 lines of the session log by default.",
    )
    logs_parser.add_argument(
        "-n",
        "--lines",
        type=_positive_or_zero,
        default=DEFAULT_LOG_LINES,
        metavar="COUNT",
        help=f"number of lines to print (default: {DEFAULT_LOG_LINES})",
    )
    logs_parser.add_argument(
        "--display",
        default=DISPLAY_NAME,
        metavar="DISPLAY",
        help="read the session log of this X display (default: :0)",
    )
    logs_parser.add_argument(
        "--desk",
        action="store_true",
        help="read the desk command log instead of the session log",
    )
    logs_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the log lines "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    logs_parser.set_defaults(handler=_handle_logs)



def _add_vnc_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the vnc commands."""
    vnc_parser = subparsers.add_parser(
        "vnc",
        help="start and control a VNC desktop session",
        description=(
            "Run XFCE on an Xvnc display next to the termux-x11 desktop. "
            "Xvnc listens on localhost only; connect through an SSH tunnel "
            "with any VNC viewer."
        ),
    )
    vnc_subparsers = vnc_parser.add_subparsers(
        dest="vnc_command", required=True, metavar="COMMAND"
    )
    vnc_setup_parser = vnc_subparsers.add_parser(
        "setup",
        help="install tigervnc and darkhttpd, then verify the VNC programs",
        description=(
            "Install the x11-repo, tigervnc and darkhttpd packages when they are "
            "missing, then check for Xvnc, vncpasswd, x0vncserver and darkhttpd. "
            "With --web, also install the noVNC page and the default WebSocket "
            "proxy."
        ),
    )
    vnc_setup_parser.add_argument(
        "--web",
        action="store_true",
        help="also install noVNC and the default WebSocket proxy",
    )
    vnc_setup_parser.set_defaults(handler=_handle_vnc_setup)

    vnc_start_parser = vnc_subparsers.add_parser(
        "start",
        help="start a VNC session, or mirror the running :0 desktop",
        description=(
            "Start Xvnc and XFCE in one new process group, or with --mirror "
            "attach x0vncserver to the desktop that is already running on :0."
        ),
    )
    vnc_start_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=(
            f"the X display of the VNC session (default: {vnc.VNC_DISPLAY}); "
            f"use {vnc.MIRROR_DISPLAY} with --mirror"
        ),
    )
    vnc_start_parser.add_argument(
        "--mirror",
        action="store_true",
        help=(
            "mirror the desktop already running on :0 instead of starting a new "
            "X server; input reaches the device desktop"
        ),
    )
    vnc_start_parser.add_argument(
        "--view-only",
        action="store_true",
        help=(
            "ignore keyboard, mouse, and clipboard input from viewers; "
            "only used with --mirror"
        ),
    )
    vnc_start_parser.add_argument(
        "--geometry",
        default=vnc.VNC_GEOMETRY,
        metavar="WIDTHxHEIGHT",
        help=f"the size of the VNC desktop (default: {vnc.VNC_GEOMETRY}); not used with --mirror",
    )
    vnc_start_parser.add_argument(
        "--password-file",
        metavar="PATH",
        help="use VncAuth with this password file instead of no authentication; not used with --mirror",
    )
    vnc_start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help="color the progress output (default: auto; auto colors only a terminal)",
    )
    vnc_start_parser.set_defaults(handler=_handle_vnc_start)

    vnc_stop_parser = vnc_subparsers.add_parser(
        "stop",
        help="stop the VNC session, or the :0 mirror, on a display",
        description=(
            "Stop the web proxy, then send SIGTERM to the VNC session process "
            "group (or to the x0vncserver mirror), then SIGKILL."
        ),
    )
    vnc_stop_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    vnc_stop_parser.set_defaults(handler=_handle_vnc_stop)

    vnc_status_parser = vnc_subparsers.add_parser(
        "status",
        help="report the VNC sessions, mirrors, and browser proxies",
        description=(
            "Report every display that belongs to the VNC domain: VNC sessions, "
            "the :0 mirror, and their browser proxies. Exit with 0 while any of "
            "them runs and with 1 otherwise."
        ),
    )
    vnc_status_parser.add_argument(
        "--display",
        default=None,
        metavar="DISPLAY",
        help="report only this X display (default: every VNC display)",
    )
    vnc_status_parser.set_defaults(handler=_handle_vnc_status)

    vnc_logs_parser = vnc_subparsers.add_parser(
        "logs",
        help="print the end of the VNC session, mirror, or web proxy log",
        description=(
            "Print the last lines of the VNC session log, the :0 mirror log, or "
            "the browser proxy log. Without --display every VNC display is shown."
        ),
    )
    vnc_logs_parser.add_argument(
        "-n",
        "--lines",
        type=_positive_or_zero,
        default=DEFAULT_LOG_LINES,
        metavar="COUNT",
        help=f"number of lines to print (default: {DEFAULT_LOG_LINES})",
    )
    vnc_logs_parser.add_argument(
        "--display",
        default=None,
        metavar="DISPLAY",
        help="read only this X display (default: every VNC display)",
    )
    vnc_logs_parser.add_argument(
        "--web",
        action="store_true",
        help="read the browser proxy log instead of the VNC log",
    )
    vnc_logs_parser.set_defaults(handler=_handle_vnc_logs)

    vnc_version_parser = vnc_subparsers.add_parser(
        "version",
        help="print the installed VNC programs and the pinned browser components",
        description=(
            "Print the Xvnc and x0vncserver versions found on PATH, and report "
            "whether the pinned noVNC page and WebSocket proxies are installed."
        ),
    )
    vnc_version_parser.set_defaults(handler=_handle_vnc_version)

    vnc_doctor_parser = vnc_subparsers.add_parser(
        "doctor",
        help="check the VNC environment without changing anything",
        description=(
            "Report missing VNC programs, downloaded page and proxies, and "
            "stale mirror or web state. This is the VNC-only view of desk doctor."
        ),
    )
    vnc_doctor_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help=(
            "color the status tags and the summary line "
            "(default: auto; auto colors only a terminal)"
        ),
    )
    vnc_doctor_parser.set_defaults(handler=_handle_vnc_doctor)

    add_vnc_web_commands(vnc_subparsers)

def _add_install_commands(subparsers: argparse._SubParsersAction) -> None:
    """Add the install commands."""
    install_parser = subparsers.add_parser(
        "install",
        help="install the desk command below the Termux prefix",
        description=(
            "Copy the sources and the session template to "
            "$PREFIX/lib/termux-desktop, copy the entry file to "
            "$PREFIX/bin/desk, and record the installed version. Running it "
            "again replaces the installed copy and keeps the previous one "
            "as lib/termux-desktop.previous."
        ),
    )
    install_parser.add_argument(
        "--source",
        default=".",
        metavar="DIR",
        help="the directory that holds desk, src, and templates (default: the current directory)",
    )
    install_parser.add_argument(
        "--release",
        default=None,
        metavar="TAG",
        help="record this release tag as the source; install.sh passes it after a download",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing $PREFIX/bin/desk that desk did not install",
    )
    install_parser.set_defaults(handler=_handle_install)

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="remove the installed desk command",
        description=(
            "Remove the installed package, the previous copy, and the entry "
            "file. State, logs, generated menu entries, and the font "
            "preference file that desk setup writes stay unless --purge is "
            "given, and a recorded session keeps desk from running with "
            "--purge."
        ),
    )
    uninstall_parser.add_argument(
        "--purge",
        action="store_true",
        help=(
            "also remove the state directory, the logs, the generated menu "
            "entries, and the font preference file"
        ),
    )
    uninstall_parser.set_defaults(handler=_handle_uninstall)

    update_parser = subparsers.add_parser(
        "update",
        help="replace the installed copy with a release",
        description=(
            "Download one release archive from GitHub and install it. The "
            "latest release is used when --version is not given. An update "
            "keeps the previous copy until the next successful update, and "
            "it does not touch the state, the logs, or the menu entries."
        ),
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        help="print the installed and the latest version without changing anything",
    )
    update_parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help="install this version instead of the latest release, for example 0.6.0",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="also replace a copy that was installed from a checkout",
    )
    update_parser.set_defaults(handler=_handle_update)

    version_parser = subparsers.add_parser(
        "version",
        help="print the desk version and where the installed copy came from",
        description=(
            "Print the recorded version, its source, and the install "
            "directory. A checkout that is not installed reports the version "
            "of this file and no install directory."
        ),
    )
    version_parser.add_argument(
        "--vnc",
        action="store_true",
        help="print only the VNC components; same as 'desk vnc version'",
    )
    version_parser.set_defaults(handler=_handle_version)




def _positive_or_zero(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text} is not a whole number") from error
    if value < 0:
        raise argparse.ArgumentTypeError(f"{text} is negative")
    return value
