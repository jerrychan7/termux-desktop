"""Refresh the package index and upgrade the installed X11 packages.

The desktop is built from the Termux X11 repository. When that repository
rebuilds a package, an installed copy can be older than the repository. A
library soname may also have changed, which makes the installed copy fail to
start with a missing shared library. desk setup never upgrades packages, so
this module is the one place that does.

desk upgrade refreshes the package index, asks apt which installed packages
it would upgrade, and upgrades only the packages whose apt origin is the
termux-x11 repository. Packages from termux-main are not upgraded directly;
apt may still upgrade one when an X11 package needs it.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Mapping, TextIO

from .config import DeskError, Paths, resolve_program
from .runner import CommandResult, CommandRunner

PACKAGE_MANAGER = "pkg"
APT_GET = "apt-get"
X11_ORIGIN = "termux-x11"

# One line of 'apt-get -s upgrade' looks like this:
#
#   Inst libvte [2:0.84.1] (2:0.84.1-1 termux-x11 x11:x11 [aarch64])
#
# The token after the candidate version is the origin of the candidate. The
# line may be followed by more text, so the pattern does not anchor the end.
INST_LINE = re.compile(
    r"^Inst (?P<name>\S+) \[[^\]]*\] "
    r"\((?P<version>\S+) (?P<origin>\S+) (?P<suite>\S+) \[[^\]]*\]\)"
)


class UpgradeError(DeskError):
    """The package index could not be refreshed or an upgrade failed."""


def resolve_upgrade_program(
    name: str,
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Find a program for upgrade, checking $PREFIX/bin before PATH."""
    environment = os.environ if environ is None else environ
    found = resolve_program(name, paths.prefix, environment.get("PATH"))
    if found is None:
        raise UpgradeError(
            f"the program '{name}' was not found in $PREFIX/bin or on PATH. "
            "desk upgrade must run inside Termux."
        )
    return found


def parse_upgradable_x11(text: str) -> tuple[str, ...]:
    """Return the packages that apt would upgrade from the X11 repository.

    The input is the output of 'apt-get -s upgrade'. Only packages whose
    candidate version comes from X11_ORIGIN are returned, in the order apt
    listed them and without duplicates.
    """
    names: list[str] = []
    for line in text.splitlines():
        match = INST_LINE.match(line)
        if match is None or match.group("origin") != X11_ORIGIN:
            continue
        name = match.group("name")
        if name not in names:
            names.append(name)
    return tuple(names)


def _run(
    runner: CommandRunner,
    command: tuple[str, ...],
    *,
    capture: bool = False,
) -> CommandResult:
    try:
        result = runner.run(command, capture=capture)
    except FileNotFoundError as error:
        raise UpgradeError(
            f"the program '{command[0]}' was not found. "
            "desk upgrade must run inside Termux."
        ) from error
    except OSError as error:
        raise UpgradeError(f"could not run {' '.join(command)}: {error}") from error
    if not result.succeeded:
        raise UpgradeError(
            "the command failed with status "
            f"{result.returncode}: {' '.join(command)}. "
            "Upgrade stopped at this step."
        )
    return result


def run_upgrade(
    paths: Paths,
    runner: CommandRunner | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Refresh the package index and upgrade the installed X11 packages."""
    output = sys.stdout if out is None else out
    commands_runner = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if paths.prefix is None or not paths.prefix.is_dir():
        raise UpgradeError(
            "PREFIX is not set to an existing directory. "
            "desk upgrade must run inside Termux."
        )

    package_manager = resolve_upgrade_program(PACKAGE_MANAGER, paths, environment)
    apt_get = resolve_upgrade_program(APT_GET, paths, environment)

    refresh = (package_manager, "update", "-y")
    print(f"Running: {' '.join(refresh)}", file=output, flush=True)
    _run(commands_runner, refresh)

    simulation = (apt_get, "-s", "upgrade")
    print(f"Running: {' '.join(simulation)}", file=output, flush=True)
    result = _run(commands_runner, simulation, capture=True)
    names = parse_upgradable_x11(result.stdout)

    if not names:
        print("The installed X11 packages are up to date.", file=output)
        return

    print(f"X11 packages to upgrade: {', '.join(names)}", file=output)
    install = (package_manager, "install", "-y", *names)
    print(f"Running: {' '.join(install)}", file=output, flush=True)
    _run(commands_runner, install)

    result = _run(commands_runner, simulation, capture=True)
    remaining = parse_upgradable_x11(result.stdout)
    if remaining:
        raise UpgradeError(
            "X11 packages are still upgradable after the install: "
            f"{', '.join(remaining)}. Check the package manager output above."
        )
    print("The installed X11 packages are up to date.", file=output)
