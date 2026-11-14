"""Argument parser for the noVNC web proxy commands."""

from __future__ import annotations

import argparse

from . import doctor, vnc, webproxy
from .cli_commands import (
    _handle_vnc_web_setup,
    _handle_vnc_web_start,
    _handle_vnc_web_status,
    _handle_vnc_web_stop,
)


def add_vnc_web_commands(vnc_subparsers: argparse._SubParsersAction) -> None:
    """Add the vnc web commands."""
    web_parser = vnc_subparsers.add_parser(
        "web",
        help="serve the VNC display to a web browser with noVNC",
        description=(
            "Download noVNC and a WebSocket proxy, then serve the page from "
            "the loopback interface. Starting the proxy starts the VNC session "
            "when it is not running (or the :0 mirror when display is :0); "
            "stopping the proxy leaves the session."
        ),
    )
    web_subparsers = web_parser.add_subparsers(
        dest="web_command", required=True, metavar="COMMAND"
    )
    web_setup_parser = web_subparsers.add_parser(
        "setup",
        help="download noVNC and the WebSocket proxy",
        description=(
            "Unpack the pinned noVNC release and the chosen proxy; the Rust "
            "proxy is the default and the Python proxy covers other CPUs."
        ),
    )
    web_setup_parser.add_argument(
        "--proxy",
        choices=webproxy.PROXY_CHOICES,
        default=webproxy.PROXY_RS,
        help="which proxy to install (default: rs)",
    )
    web_setup_parser.add_argument(
        "--force",
        action="store_true",
        help="replace files that are already installed",
    )
    web_setup_parser.set_defaults(handler=_handle_vnc_web_setup)

    web_start_parser = web_subparsers.add_parser(
        "start",
        help="start the browser page and its WebSocket proxy",
        description=(
            "Start the VNC session when needed, then the page and the proxy. "
            "The page listens on 127.0.0.1 by default."
        ),
    )
    web_start_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_start_parser.add_argument(
        "--proxy",
        choices=(webproxy.PROXY_PY, webproxy.PROXY_RS),
        default=webproxy.PROXY_RS,
        help="which proxy to run (default: rs)",
    )
    web_start_parser.add_argument(
        "--host",
        default=webproxy.DEFAULT_HOST,
        metavar="ADDRESS",
        help=f"the address the page listens on (default: {webproxy.DEFAULT_HOST})",
    )
    web_start_parser.add_argument(
        "--port",
        type=int,
        default=webproxy.DEFAULT_PORT,
        metavar="PORT",
        help=f"the page port (default: {webproxy.DEFAULT_PORT})",
    )
    web_start_parser.add_argument(
        "--view-only",
        action="store_true",
        help=(
            "start the :0 mirror in view-only mode when it is not running; "
            "ignored when a mirror is already running"
        ),
    )
    web_start_parser.add_argument(
        "--color",
        choices=doctor.COLOR_CHOICES,
        default=doctor.COLOR_AUTO,
        help="color the progress output (default: auto; auto colors only a terminal)",
    )
    web_start_parser.set_defaults(handler=_handle_vnc_web_start)

    web_stop_parser = web_subparsers.add_parser(
        "stop",
        help="stop the WebSocket proxy but leave the VNC session running",
    )
    web_stop_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_stop_parser.set_defaults(handler=_handle_vnc_web_stop)

    web_status_parser = web_subparsers.add_parser(
        "status",
        help="report whether the browser proxy is running",
        description="Exit with 0 while the proxy runs and with 1 otherwise.",
    )
    web_status_parser.add_argument(
        "--display",
        default=vnc.VNC_DISPLAY,
        metavar="DISPLAY",
        help=f"the X display of the VNC session (default: {vnc.VNC_DISPLAY})",
    )
    web_status_parser.set_defaults(handler=_handle_vnc_web_status)
