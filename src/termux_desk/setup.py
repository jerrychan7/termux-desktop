"""Install the fixed Termux packages that the desktop needs.

desk setup is the only command that changes installed packages. It installs
only the fixed list, only when a package is missing, and never upgrades the
other packages on the device. desk start does not call any code in this
module.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Sequence, TextIO

from .config import (
    FIXED_PACKAGES,
    REPO_PACKAGE,
    TERMUX_X11_DOWNLOAD_URL,
    DeskError,
    Paths,
    resolve_program,
)
from .runner import CommandRunner

PACKAGE_MANAGER = "pkg"
DPKG_QUERY = "dpkg-query"
INSTALLED_STATUS = "install ok installed"
STATUS_FORMAT = "-f=${Status}"

TERMUX_X11_APP_NOTE = (
    "The Termux:X11 Android app is not installed by desk setup. "
    f"Download it from {TERMUX_X11_DOWNLOAD_URL}, install it, then run 'desk doctor'."
)


class SetupError(DeskError):
    """The fixed packages could not be installed."""


def resolve_setup_program(
    name: str,
    paths: Paths,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Find a program for setup, checking $PREFIX/bin before PATH.

    A minimal Termux environment can be started without the usual PATH. The
    absolute path from $PREFIX/bin still works, so setup resolves the commands
    once and then runs them by that path.
    """
    environment = os.environ if environ is None else environ
    found = resolve_program(name, paths.prefix, environment.get("PATH"))
    if found is None:
        raise SetupError(
            f"the program '{name}' was not found in $PREFIX/bin or on PATH. "
            "desk setup must run inside Termux."
        )
    return found


def package_is_installed(
    name: str,
    runner: CommandRunner,
    *,
    dpkg_query: str = DPKG_QUERY,
) -> bool:
    """Return True when dpkg reports the package as installed."""
    try:
        result = runner.run([dpkg_query, "-W", STATUS_FORMAT, name], capture=True)
    except FileNotFoundError as error:
        raise SetupError(
            f"the program '{dpkg_query}' was not found. "
            "desk setup must run inside Termux."
        ) from error
    return result.succeeded and INSTALLED_STATUS in result.stdout


def find_missing_packages(
    runner: CommandRunner,
    names: Sequence[str] = FIXED_PACKAGES,
    *,
    dpkg_query: str = DPKG_QUERY,
) -> tuple[str, ...]:
    """Return the fixed packages that are not installed, in fixed order."""
    return tuple(
        name for name in names if not package_is_installed(name, runner, dpkg_query=dpkg_query)
    )


def build_setup_commands(
    missing: Sequence[str],
    *,
    package_manager: str = PACKAGE_MANAGER,
) -> tuple[tuple[str, ...], ...]:
    """Return the package manager commands for the missing packages.

    The order is fixed: install x11-repo when it is missing, refresh the
    package index, then install the remaining missing packages. When nothing
    is missing, no command is returned and no package index is refreshed.
    Names that are not in the fixed list are ignored; desk setup does not
    install other packages.
    """
    missing_names = set(missing)
    known_missing = tuple(name for name in FIXED_PACKAGES if name in missing_names)
    if not known_missing:
        return ()

    # x11-repo runs a package index refresh in its post-install step, but the
    # fixed order is kept here: install the repository, refresh, then install
    # the remaining packages.
    commands: list[tuple[str, ...]] = []
    if REPO_PACKAGE in known_missing:
        commands.append((package_manager, "install", "-y", REPO_PACKAGE))
    commands.append((package_manager, "update", "-y"))

    remainder = tuple(name for name in known_missing if name != REPO_PACKAGE)
    if remainder:
        commands.append((package_manager, "install", "-y", *remainder))
    return tuple(commands)


def _find_missing_packages(runner: CommandRunner, dpkg_query: str) -> tuple[str, ...]:
    """Read the installed packages and turn read errors into SetupError."""
    try:
        return find_missing_packages(runner, dpkg_query=dpkg_query)
    except OSError as error:
        raise SetupError(f"could not read the installed packages: {error}") from error


def run_setup(
    paths: Paths,
    runner: CommandRunner | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Install the missing fixed packages and verify the result."""
    output = sys.stdout if out is None else out
    commands_runner = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if paths.prefix is None or not paths.prefix.is_dir():
        raise SetupError(
            "PREFIX is not set to an existing directory. desk setup must run inside Termux."
        )

    package_manager = resolve_setup_program(PACKAGE_MANAGER, paths, environment)
    dpkg_query = resolve_setup_program(DPKG_QUERY, paths, environment)

    print(f"Fixed packages: {', '.join(FIXED_PACKAGES)}", file=output)
    missing = _find_missing_packages(commands_runner, dpkg_query)
    if not missing:
        print("All fixed packages are already installed.", file=output)
        print(TERMUX_X11_APP_NOTE, file=output)
        return

    print(f"Missing packages: {', '.join(missing)}", file=output)
    for command in build_setup_commands(missing, package_manager=package_manager):
        print(f"Running: {' '.join(command)}", file=output, flush=True)
        try:
            result = commands_runner.run(command)
        except FileNotFoundError as error:
            raise SetupError(
                f"the program '{command[0]}' was not found. "
                "desk setup must run inside Termux."
            ) from error
        except OSError as error:
            raise SetupError(f"could not run {' '.join(command)}: {error}") from error
        if not result.succeeded:
            raise SetupError(
                "the command failed with status "
                f"{result.returncode}: {' '.join(command)}. "
                "Setup stopped at this step."
            )

    still_missing = _find_missing_packages(commands_runner, dpkg_query)
    if still_missing:
        raise SetupError(
            "packages are still missing after setup: "
            f"{', '.join(still_missing)}. Check the package manager output above."
        )

    print("All fixed packages are installed.", file=output)
    print(TERMUX_X11_APP_NOTE, file=output)
