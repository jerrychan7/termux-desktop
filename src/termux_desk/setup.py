"""Install the fixed Termux packages that the desktop needs.

desk setup is the only command that changes installed packages. It installs
only the fixed list, only when a package is missing, and never upgrades the
other packages on the device.

After the packages, desk setup writes a fontconfig preference that names the
CJK faces installed on the device for the device language, plus rules for the
other CJK languages. When the device has no face for its language, or
fontconfig is not available, the file is skipped with a warning; setup does
not fail.

desk start does not call any code in this module.
"""

from __future__ import annotations

import os
import sys
from typing import Mapping, Sequence, TextIO

from . import fonts
from . import ime as ime_support
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

PYTHON_PROGRAM = "python3"
PIP_MODULE = "pip"
PYYAML_PACKAGE = "pyyaml"
PYYAML_VERSION = "6.0.3"
YAML_IMPORT_CHECK = "import yaml"

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
    fixed: Sequence[str] = FIXED_PACKAGES,
) -> tuple[tuple[str, ...], ...]:
    """Return the package manager commands for the missing packages.

    The order is fixed: install x11-repo when it is missing, refresh the
    package index, then install the remaining missing packages. When nothing
    is missing, no command is returned and no package index is refreshed.
    Names that are not in the fixed list are ignored; desk setup does not
    install other packages.
    """
    missing_names = set(missing)
    known_missing = tuple(name for name in fixed if name in missing_names)
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


def _print_ime_note(with_ime: bool, output: TextIO) -> None:
    """Tell the user how to load the input method."""
    if not with_ime:
        return
    print(
        "The ibus input method is installed. Restart the session with 'desk start' "
        "to load it.",
        file=output,
    )


def pyyaml_importable(runner: CommandRunner, python: str) -> bool:
    """Return True when the interpreter can import PyYAML."""
    result = runner.run([python, "-P", "-c", YAML_IMPORT_CHECK], capture=True)
    return result.succeeded


def ensure_pyyaml(
    paths: Paths,
    runner: CommandRunner,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Install PyYAML in the user site when the interpreter cannot import it.

    The configuration layer always parses a YAML document, so PyYAML is a
    normal dependency. The installation stays in the user site so it does not
    touch the system packages, and an existing installation is never upgraded.
    """
    output = sys.stdout if out is None else out
    environment = os.environ if environ is None else environ
    python = resolve_setup_program(PYTHON_PROGRAM, paths, environment)

    if pyyaml_importable(runner, python):
        print("PyYAML is already installed.", file=output)
        return

    try:
        pip = runner.run([python, "-m", PIP_MODULE, "--version"], capture=True)
    except OSError as error:
        raise SetupError(f"could not run {python} -m pip: {error}") from error
    if not pip.succeeded:
        raise SetupError(
            "pip is not available. Install it with 'pkg install python-pip', "
            "then run 'desk setup' again."
        )

    command = (
        python,
        "-m",
        PIP_MODULE,
        "install",
        "--user",
        f"{PYYAML_PACKAGE}=={PYYAML_VERSION}",
    )
    print(f"Running: {' '.join(command)}", file=output, flush=True)
    try:
        result = runner.run(command)
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
            f"{result.returncode}: {' '.join(command)}."
        )
    if not pyyaml_importable(runner, python):
        raise SetupError(
            "PyYAML was installed but Python cannot import it. "
            "Check the pip output above."
        )
    print("PyYAML is installed.", file=output)


def _finish_setup(
    paths: Paths,
    runner: CommandRunner,
    environment: Mapping[str, str],
    output: TextIO,
    *,
    with_ime: bool,
) -> None:
    """Run the steps that follow the package installation."""
    fonts.run_font_preference(paths, out=output)
    _print_ime_note(with_ime, output)
    try:
        ensure_pyyaml(paths, runner, environ=environment, out=output)
    except SetupError as error:
        # The rest of setup is still useful without PyYAML: the fonts, the
        # input method and the VNC packages are independent of the
        # configuration layer. The session keeps running on the empty
        # defaults until desk setup succeeds again.
        print(f"warning: {error}", file=output)
        print(
            "The configuration layer needs PyYAML; run 'desk setup' again "
            "when the device is online.",
            file=output,
        )
    print(TERMUX_X11_APP_NOTE, file=output)


def _find_missing_packages(
    runner: CommandRunner, dpkg_query: str, names: Sequence[str]
) -> tuple[str, ...]:
    """Read the installed packages and turn read errors into SetupError."""
    try:
        return find_missing_packages(runner, names, dpkg_query=dpkg_query)
    except OSError as error:
        raise SetupError(f"could not read the installed packages: {error}") from error


def run_setup(
    paths: Paths,
    runner: CommandRunner | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    with_ime: bool = False,
) -> None:
    """Install the missing fixed packages and verify the result.

    ``with_ime`` adds the optional ibus input method packages to the fixed
    list. The session enables ibus afterwards only when it is installed.
    PyYAML is always installed for the configuration layer; it is a user-site
    install and an existing install is kept.
    """
    output = sys.stdout if out is None else out
    commands_runner = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if paths.prefix is None or not paths.prefix.is_dir():
        raise SetupError(
            "PREFIX is not set to an existing directory. desk setup must run inside Termux."
        )

    package_manager = resolve_setup_program(PACKAGE_MANAGER, paths, environment)
    dpkg_query = resolve_setup_program(DPKG_QUERY, paths, environment)

    fixed = FIXED_PACKAGES + (ime_support.IME_PACKAGES if with_ime else ())
    print(f"Fixed packages: {', '.join(fixed)}", file=output)
    missing = _find_missing_packages(commands_runner, dpkg_query, fixed)
    if not missing:
        print("All fixed packages are already installed.", file=output)
        _finish_setup(
            paths,
            commands_runner,
            environment,
            output,
            with_ime=with_ime,
        )
        return

    print(f"Missing packages: {', '.join(missing)}", file=output)
    for command in build_setup_commands(
        missing, package_manager=package_manager, fixed=fixed
    ):
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

    still_missing = _find_missing_packages(commands_runner, dpkg_query, fixed)
    if still_missing:
        raise SetupError(
            "packages are still missing after setup: "
            f"{', '.join(still_missing)}. Check the package manager output above."
        )

    print("All fixed packages are installed.", file=output)
    _finish_setup(
        paths,
        commands_runner,
        environment,
        output,
        with_ime=with_ime,
    )
