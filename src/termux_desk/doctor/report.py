"""Report rendering for desk doctor."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Mapping, TextIO

from ..config import Paths
from ..runner import CommandRunner
from ..session import SessionState, Verification, unix_socket_accepts_connection
from .base import (
    COLOR_AUTO,
    FAILED,
    NAME_START,
    OK,
    WARNING,
    PlatformFacts,
    collect_checks,
    color_enabled,
    default_session_verifier,
    format_status_tag,
    format_summary,
    read_platform_facts,
)
from .containers import collect_container_checks
from .fonts import collect_font_checks
from .vnc import collect_vnc_checks


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
