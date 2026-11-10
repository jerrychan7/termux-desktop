"""Run packages through the package manager that the container already has.

desk pkg is a thin alias: the arguments after the container name go to the
package manager unchanged, so its defaults, its prompts, its output, and its
exit status are the ones the user sees. desk only picks the program, maps the
two short aliases add and rm, and refreshes the menu entries afterwards,
because a package may have added or removed a .desktop file.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Mapping, Sequence, TextIO

from . import menu
from .config import DeskError, Paths, resolve_program
from .container import (
    NAME_PATTERN,
    PROFILES,
    PROOT_DISTRO_PROGRAM,
    container_command,
    family_for,
    list_containers,
    parse_os_release,
)
from .runner import CommandResult, CommandRunner

# Short spellings that do not belong to every package manager. apt calls the
# two operations install and remove; desk accepts the shorter words too.
ALIASES = {"add": "install", "rm": "remove"}


class PkgError(DeskError):
    """The container package manager could not be used."""


def run_pkg(
    paths: Paths,
    container: str,
    arguments: Sequence[str],
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    """Run the container package manager and refresh the menu entries.

    The arguments are forwarded unchanged, so desk does not add flags, does
    not update the package index first, and does not answer prompts. The menu
    is refreshed after every run, whether the package manager succeeded or
    not, because a failed install can still have written or removed files.
    The exit status of the package manager is the return value.
    """
    output = sys.stdout if out is None else out
    commands = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if not NAME_PATTERN.match(container):
        raise PkgError(
            f"'{container}' is not a valid container name. "
            "Use letters, digits, '.', '_' or '-', and do not start with '-'."
        )
    if not arguments:
        raise PkgError(
            "no package manager arguments were given. "
            f"For example: desk pkg {container} install gimp"
        )

    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, paths.prefix, environment.get("PATH")
    )
    if proot_distro is None:
        raise PkgError(
            "the program 'proot-distro' was not found. "
            "Install it with 'pkg install proot-distro' first."
        )

    try:
        existing = list_containers(proot_distro, commands)
    except OSError as error:
        raise PkgError(f"could not list the containers: {error}") from error
    if container not in existing:
        raise PkgError(
            f"the container {container} does not exist. "
            f"Create it with 'desk container init {container}' first."
        )

    release = _run_container_command(
        commands,
        container_command(proot_distro, container, "cat", "/etc/os-release"),
        capture=True,
    )
    facts = parse_os_release(release.stdout)
    family = family_for(facts)
    profile = PROFILES.get(family) if family is not None else None
    if profile is None:
        raise PkgError(
            f"desk pkg does not know the package manager of "
            f"'{facts.get('ID', 'this container')}'. Supported families: "
            f"{', '.join(sorted(PROFILES))}."
        )

    forwarded = _forwarded_arguments(arguments)
    print(
        f"Running in {container}: {profile.package_manager} "
        f"{shlex.join(forwarded)}",
        file=output,
        flush=True,
    )
    result = _run_container_command(
        commands,
        container_command(
            proot_distro, container, profile.package_manager, *forwarded
        ),
    )

    try:
        report = menu.run_refresh(
            paths, runner=commands, environ=environment, out=output
        )
    except menu.MenuError as error:
        print(f"the menu was not refreshed: {error}", file=output)
    else:
        print(
            f"Menu entries for {container}: {report.count_for(container)}",
            file=output,
        )
    return result.returncode


def _forwarded_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
    """Return the arguments with the two short aliases replaced."""
    first = ALIASES.get(arguments[0], arguments[0])
    return (first, *arguments[1:])


def _run_container_command(
    commands: CommandRunner,
    command: Sequence[str],
    *,
    capture: bool = False,
) -> CommandResult:
    """Run one command in the container and turn a failure into a PkgError.

    A command that cannot start at all is a desk problem and raises; a
    package manager that exits with a nonzero status is reported through the
    return value, because its status is what desk pkg returns to the caller.
    """
    try:
        result = commands.run(command, capture=capture)
    except FileNotFoundError as error:
        raise PkgError(
            f"the program '{command[0]}' was not found. "
            "desk pkg must run inside Termux."
        ) from error
    except OSError as error:
        raise PkgError(f"could not run {' '.join(command)}: {error}") from error
    if capture and not result.succeeded:
        raise PkgError(
            f"could not read /etc/os-release in the container: the command "
            f"exited with status {result.returncode}: {' '.join(command)}"
        )
    return result
