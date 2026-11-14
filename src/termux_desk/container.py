"""Create a proot container and prepare it for GUI applications.

desk container init is the only command that creates or prepares containers.
It calls proot-distro to create the container, then makes the mount points
that desk app launch will bind the host directories onto, then installs a
small X client package set so that an application can render on :0.

Adding another distribution family means adding one entry to
DISTRO_FAMILIES and one DistroProfile with the commands that family needs.
Nothing else in this module is family specific.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .config import DeskError, Paths, resolve_program
from .runner import CommandRunner

PROOT_DISTRO_PROGRAM = "proot-distro"
DEFAULT_IMAGE = "ubuntu"

# The mount points inside the container, in the order desk creates them. They
# must exist before desk app launch can bind the host directories onto them.
MOUNT_POINTS = ("/termux", "/home/termux", "/sdcard")

# The line proot-distro prints before the container names in `proot-distro
# list`.
INSTALLED_HEADER = "Installed containers:"

# A container name becomes part of a command line and of a directory name, so
# it is restricted to a conservative set and may not start with an option dash.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The check that runs inside the container at the end of the setup. It uses sh
# and command -v because those exist in every family this module may grow to
# support; bash does not.
VERIFY_SCRIPT = " && ".join(
    [f"test -d {point}" for point in MOUNT_POINTS]
    + ["command -v xterm >/dev/null"]
)


class ContainerError(DeskError):
    """The container could not be created, prepared, or verified."""


@dataclass(frozen=True)
class DistroProfile:
    """The commands one distribution family needs to prepare a container."""

    family: str
    package_manager: str
    packages: tuple[str, ...]
    query: tuple[str, ...]
    refresh: tuple[str, ...]
    install: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()


# Map an /etc/os-release ID or an ID_LIKE token to the family that knows how
# to install packages for it. Multiple distributions can share one family.
DISTRO_FAMILIES = {
    "debian": "debian",
    "ubuntu": "debian",
}

PROFILES = {
    "debian": DistroProfile(
        family="debian",
        package_manager="apt",
        packages=("xterm", "x11-utils", "xfonts-base", "fonts-dejavu-core"),
        query=("dpkg-query", "-W", "-f=${Package}=${Status}\\n"),
        refresh=("apt-get", "update", "-qq"),
        install=("apt-get", "install", "-y", "--no-install-recommends"),
        environment=(("DEBIAN_FRONTEND", "noninteractive"),),
    ),
}


CONTAINERS_DIR = Path("var") / "lib" / "proot-distro" / "containers"
APPLICATIONS_PATH = Path("usr") / "share" / "applications"


def rootfs_path(prefix: Path, name: str) -> Path:
    """Return the rootfs directory of a proot-distro container."""
    return prefix / CONTAINERS_DIR / name / "rootfs"


def read_container_release(prefix: Path, name: str) -> dict[str, str] | None:
    """Read /etc/os-release from a container rootfs, or None when it is absent."""
    try:
        text = (rootfs_path(prefix, name) / "etc" / "os-release").read_text(
            encoding="utf-8"
        )
    except OSError:
        return None
    return parse_os_release(text)


def desktop_entry_count(rootfs: Path) -> int:
    """Count the .desktop files a container rootfs exposes to the menu."""
    try:
        return sum(1 for path in (rootfs / APPLICATIONS_PATH).glob("*.desktop") if path.is_file())
    except OSError:
        return 0


def distro_label(release: Mapping[str, str] | None) -> str:
    """Return the human-readable distribution name of a container."""
    if not release:
        return "unknown"
    pretty = release.get("PRETTY_NAME") or release.get("NAME")
    if pretty:
        return pretty
    version = release.get("VERSION_ID")
    return f"{release.get('ID', 'unknown')} {version}".strip()


def describe_container(name: str, prefix: Path) -> tuple[dict[str, str] | None, Path, int]:
    """Return the release, rootfs, and application count of one container."""
    rootfs = rootfs_path(prefix, name)
    return read_container_release(prefix, name), rootfs, desktop_entry_count(rootfs)


def run_container_list(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    """Print one line for every installed container."""
    output = sys.stdout if out is None else out
    prefix = paths.prefix
    if prefix is None:
        raise ContainerError("PREFIX is not set.")
    environment = os.environ if environ is None else environ
    commands = runner if runner is not None else CommandRunner()
    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, prefix, environment.get("PATH")
    )
    if proot_distro is None:
        raise ContainerError(
            "the program 'proot-distro' was not found. "
            "Install it with 'pkg install proot-distro' first."
        )
    try:
        names = list_containers(proot_distro, commands)
    except OSError as error:
        raise ContainerError(f"could not list the containers: {error}") from error
    if not names:
        print("No containers are installed.", file=output)
        return 0
    machine = platform.machine() or "unknown"
    for name in names:
        release, rootfs, count = describe_container(name, prefix)
        entry = "entry" if count == 1 else "entries"
        print(
            f"{name}: {distro_label(release)} ({machine}), "
            f"{count} desktop {entry}, {rootfs}",
            file=output,
        )
    return 0


def run_container_status(
    paths: Paths,
    name: str,
    *,
    out: TextIO | None = None,
) -> int:
    """Print the distribution, rootfs, and mount points of one container."""
    output = sys.stdout if out is None else out
    prefix = paths.prefix
    if prefix is None:
        raise ContainerError("PREFIX is not set.")
    if not NAME_PATTERN.match(name):
        raise ContainerError(
            f"'{name}' is not a valid container name. "
            "Use letters, digits, '.', '_' or '-', and do not start with '-'."
        )
    rootfs = rootfs_path(prefix, name)
    if not rootfs.is_dir():
        raise ContainerError(
            f"there is no container named '{name}'. "
            "Run 'desk container list' to see the installed containers."
        )
    release = read_container_release(prefix, name)
    count = desktop_entry_count(rootfs)
    missing = tuple(point for point in MOUNT_POINTS if not (rootfs / point.lstrip("/")).is_dir())
    print(f"Container {name}:", file=output)
    print(f"  distribution: {distro_label(release)}", file=output)
    print(f"  architecture: {platform.machine() or 'unknown'}", file=output)
    print(f"  rootfs: {rootfs}", file=output)
    print(f"  desktop entries: {count}", file=output)
    if missing:
        print(
            f"  mount points: missing {', '.join(missing)} "
            "(run 'desk container init' to create them)",
            file=output,
        )
    else:
        print(f"  mount points: {' '.join(MOUNT_POINTS)} (ready)", file=output)
    return 0


def parse_container_list(output: str) -> tuple[str, ...]:
    """Return the container names from `proot-distro list`."""
    names: list[str] = []
    in_section = False
    for line in output.splitlines():
        stripped = line.strip()
        if not in_section:
            if stripped == INSTALLED_HEADER:
                in_section = True
            continue
        if not stripped:
            continue
        if stripped.startswith("*"):
            names.append(stripped[1:].strip())
            continue
        break
    return tuple(names)


def list_containers(proot_distro: str, runner: CommandRunner) -> tuple[str, ...]:
    """Return the container names that proot-distro reports as installed.

    proot-distro writes its own messages to standard error, so the names can
    arrive on either stream; both are read.
    """
    result = runner.run((proot_distro, "list"), capture=True)
    return parse_container_list(result.stdout + "\n" + result.stderr)


def parse_os_release(text: str) -> dict[str, str]:
    """Return the key/value pairs of /etc/os-release."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def family_for(release: Mapping[str, str]) -> str | None:
    """Return the package family for an os-release mapping, or None.

    ID is checked first. Then each ID_LIKE token is checked, because a
    derivative usually reports the distribution it derives from there.
    """
    identifiers = [release.get("ID", "")]
    identifiers.extend(release.get("ID_LIKE", "").split())
    for identifier in identifiers:
        family = DISTRO_FAMILIES.get(identifier.strip().lower())
        if family is not None:
            return family
    return None


def parse_installed_packages(output: str) -> frozenset[str]:
    """Return the package names that dpkg-query reported as installed."""
    installed: set[str] = set()
    for line in output.splitlines():
        package, _, status = line.partition("=")
        if package.strip() and "installed" in status:
            installed.add(package.strip())
    return frozenset(installed)


def container_command(
    proot_distro: str,
    name: str,
    *arguments: str,
    environment: Sequence[tuple[str, str]] = (),
    binds: Sequence[tuple[str, str]] = (),
    shared_tmp: bool = False,
) -> tuple[str, ...]:
    """Build one `proot-distro login` command for the container.

    The login uses --isolated so that the only files the container can reach
    are the ones this project asks for; binds adds the declared host
    directories, and the arguments after -- are the command to run inside the
    container.
    """
    command: list[str] = [proot_distro, "sh", name, "--isolated"]
    for source, destination in binds:
        command.extend(("--bind", f"{source}:{destination}"))
    if shared_tmp:
        command.append("--shared-tmp")
    for key, value in environment:
        command.extend(("-e", f"{key}={value}"))
    command.append("--")
    command.extend(arguments)
    return tuple(command)


def _run(
    runner: CommandRunner,
    command: Sequence[str],
    output: TextIO,
    action: str,
    *,
    capture: bool = False,
    announce: bool = False,
    check_status: bool = True,
):
    """Run one command and turn a failure into a ContainerError.

    check_status is off for the package query: dpkg-query exits with status 1
    when a package is missing, and a missing package is exactly what the
    query is asked to find.
    """
    if announce:
        print(f"Running: {' '.join(command)}", file=output, flush=True)
    try:
        result = runner.run(command, capture=capture)
    except FileNotFoundError as error:
        raise ContainerError(
            f"the program '{command[0]}' was not found. "
            "desk container init must run inside Termux."
        ) from error
    except OSError as error:
        raise ContainerError(f"could not run {' '.join(command)}: {error}") from error
    if check_status and not result.succeeded:
        raise ContainerError(
            f"could not {action}: the command exited with status "
            f"{result.returncode}: {' '.join(command)}"
        )
    return result


def run_container_init(
    paths: Paths,
    name: str,
    *,
    image: str = DEFAULT_IMAGE,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Create the container if needed and prepare it for GUI applications."""
    output = sys.stdout if out is None else out
    commands = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    if not NAME_PATTERN.match(name):
        raise ContainerError(
            f"'{name}' is not a valid container name. "
            "Use letters, digits, '.', '_' or '-', and do not start with '-'."
        )

    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, paths.prefix, environment.get("PATH")
    )
    if proot_distro is None:
        raise ContainerError(
            "the program 'proot-distro' was not found. "
            "Install it with 'pkg install proot-distro' first."
        )

    try:
        existing = list_containers(proot_distro, commands)
    except FileNotFoundError as error:
        raise ContainerError(
            f"the program '{proot_distro}' was not found. "
            "desk container init must run inside Termux."
        ) from error
    except OSError as error:
        raise ContainerError(f"could not list the containers: {error}") from error
    if name in existing:
        print(f"The container {name} already exists.", file=output)
    else:
        print(
            f"Creating the container {name} from the image {image}.",
            file=output,
            flush=True,
        )
        _run(
            commands,
            (proot_distro, "install", image, "--name", name),
            output,
            "create the container",
            announce=True,
        )

    release = _run(
        commands,
        container_command(proot_distro, name, "cat", "/etc/os-release"),
        output,
        "read /etc/os-release",
        capture=True,
    )
    facts = parse_os_release(release.stdout)
    family = family_for(facts)
    profile = PROFILES.get(family) if family is not None else None
    if profile is None:
        raise ContainerError(
            f"desk container init does not know how to install packages in "
            f"'{facts.get('ID', 'this container')}'. Supported families: "
            f"{', '.join(sorted(PROFILES))}. Create the mount points and "
            "install an X client and fonts yourself, or add a profile in "
            "src/termux_desk/container.py."
        )
    print(
        f"Distribution family: {profile.family} "
        f"(packages through {profile.package_manager}).",
        file=output,
    )

    print(f"Creating the mount points: {' '.join(MOUNT_POINTS)}", file=output, flush=True)
    _run(
        commands,
        container_command(proot_distro, name, "mkdir", "-p", *MOUNT_POINTS),
        output,
        "create the mount points",
        announce=True,
    )

    query = _run(
        commands,
        container_command(
            proot_distro,
            name,
            *profile.query,
            *profile.packages,
        ),
        output,
        "read the installed packages",
        capture=True,
        check_status=False,
    )
    installed = parse_installed_packages(query.stdout)
    missing = tuple(
        package for package in profile.packages if package not in installed
    )
    if not missing:
        print("The X client packages are already installed.", file=output)
    else:
        print(f"Missing packages: {', '.join(missing)}", file=output, flush=True)
        _run(
            commands,
            container_command(
                proot_distro,
                name,
                *profile.refresh,
                environment=profile.environment,
            ),
            output,
            "refresh the package index",
            announce=True,
        )
        _run(
            commands,
            container_command(
                proot_distro,
                name,
                *profile.install,
                *missing,
                environment=profile.environment,
            ),
            output,
            "install the X client packages",
            announce=True,
        )

    _run(
        commands,
        container_command(proot_distro, name, "sh", "-c", VERIFY_SCRIPT),
        output,
        "verify the container",
    )
    print(
        f"The container {name} is ready for GUI applications.",
        file=output,
    )
