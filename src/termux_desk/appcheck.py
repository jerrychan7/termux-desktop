"""Read-only health checks for container applications."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Mapping, TextIO

from . import app as app_launcher
from .config import DeskError, Paths
from .menu import (
    ContainerApp,
    MenuError,
    _installed_containers,
    _missing_commands,
    _require_proot_distro,
    _scan_container,
)
from .runner import CommandRunner


@dataclass(frozen=True)
class AppCheckReport:
    """The read-only answer of desk app doctor."""

    containers: tuple[str, ...]
    applications: int
    missing: tuple[tuple[str, str], ...]
    apps: tuple[ContainerApp, ...] = ()


def check_apps(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    container: str | None = None,
) -> AppCheckReport:
    """Scan containers and report Exec programs that do not resolve.

    Nothing is written: no menu entry, icon, or state file changes.
    """
    commands = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ
    proot_distro = _require_proot_distro(paths, environment)
    installed = _installed_containers(proot_distro, commands)
    if container is not None:
        if container not in installed:
            raise MenuError(f"there is no container named '{container}'.")
        containers = (container,)
    else:
        containers = installed

    missing: list[tuple[str, str]] = []
    collected: list[ContainerApp] = []
    applications = 0
    for name in containers:
        apps = _scan_container(proot_distro, name, commands)
        applications += len(apps)
        collected.extend(apps)
        missing.extend(_missing_commands(proot_distro, name, apps, commands))
    return AppCheckReport(
        containers=tuple(containers),
        applications=applications,
        missing=tuple(missing),
        apps=tuple(collected),
    )


def run_app_doctor(
    paths: Paths,
    *,
    container: str | None = None,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    start: bool = False,
    timeout: float = 8.0,
) -> int:
    """Report whether the Exec programs resolve, and optionally whether they start."""
    output = sys.stdout if out is None else out
    report = check_apps(
        paths,
        runner=runner,
        environ=environ,
        container=container,
    )
    containers = len(report.containers)
    noun = "container" if containers == 1 else "containers"
    print(
        f"Checked {report.applications} applications in {containers} {noun}.",
        file=output,
    )
    if report.missing:
        for spec, command in report.missing:
            print(
                f"  {spec}: the command {command} was not found in the container",
                file=output,
            )
        count = len(report.missing)
        noun = "application" if count == 1 else "applications"
        pronoun = "it" if count == 1 else "they"
        print(
            f"Found {count} {noun} whose command was not found; "
            f"{pronoun} may fail to start.",
            file=output,
        )
    if not start:
        if report.missing:
            return 1
        print("Every application command was found.", file=output)
        return 0

    missing_specs = {spec for spec, _command in report.missing}
    started = 0
    failed = 0
    for application in report.apps:
        if application.spec in missing_specs or not application.command:
            continue
        try:
            probe = app_launcher.probe_app_start(
                paths,
                application.spec,
                arguments=application.command,
                timeout=timeout,
                environ=environ,
            )
        except DeskError as error:
            failed += 1
            print(
                f"  {application.spec}: could not be started: {error}",
                file=output,
            )
            continue
        if probe.status == "started":
            started += 1
            print(
                f"  {application.spec}: still running after {timeout:g}s.",
                file=output,
            )
        elif probe.status == "exited":
            started += 1
            print(
                f"  {application.spec}: started and exited with code 0.",
                file=output,
            )
        else:
            failed += 1
            if probe.exit_code is None:
                reason = "could not start"
            else:
                reason = f"exit code {probe.exit_code}"
            detail = f": {probe.detail}" if probe.detail else ""
            print(
                f"  {application.spec}: failed to start ({reason}){detail}",
                file=output,
            )
    print(
        f"Started {started} applications; {failed} failed to start.",
        file=output,
    )
    return 1 if report.missing or failed else 0
