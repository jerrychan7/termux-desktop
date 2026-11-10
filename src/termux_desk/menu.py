"""Generate host menu entries for the applications installed in containers.

The entries live in the per-user applications directory, next to the hand
written ones, and every generated file starts with GENERATED_PREFIX so that
a later refresh can remove the files it wrote without touching anything else.

Only the [Desktop Entry] group is read. Entries that ask for a terminal, are
hidden, or are not applications are skipped, because desk app launch has no
terminal to give them and the desktop would show nothing.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .config import DeskError, Paths, resolve_program
from .container import PROOT_DISTRO_PROGRAM, container_command, list_containers
from .runner import CommandRunner

GENERATED_PREFIX = "desk-"
APPLICATION_DIR = "/usr/share/applications"

# The scan prints one record per file: the marker, the file name, the marker,
# then the file content. U+001E is not valid in a .desktop file.
SCAN_MARKER = "\x1e"

FIELD_CODES = (
    "%f", "%F", "%u", "%U", "%i", "%c", "%k", "%d", "%D", "%n", "%N", "%v", "%m",
)

# Characters that make the desktop entry specification require quoting in an
# Exec value.
EXEC_RESERVED = ' \t\n"\'\\><~|&;$*?#()`'

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class MenuError(DeskError):
    """The menu entries could not be generated."""


@dataclass(frozen=True)
class ContainerApp:
    """One application that is installed in a container."""

    container: str
    app_id: str
    name: str
    command: tuple[str, ...]
    icon: str | None
    entry_name: str

    @property
    def spec(self) -> str:
        """Return the container/app form that desk app launch accepts."""
        return f"{self.container}/{self.app_id}"


def scan_command(proot_distro: str, container: str) -> tuple[str, ...]:
    """Build the command that prints every desktop file of one container."""
    script = (
        f"cd {APPLICATION_DIR} 2>/dev/null || exit 0; "
        'for f in *.desktop; do [ -f "$f" ] || continue; '
        "printf '\\036%s\\036\\n' \"$f\"; cat \"$f\"; echo; done"
    )
    return container_command(proot_distro, container, "sh", "-c", script)


def split_desktop_output(text: str) -> tuple[tuple[str, str], ...]:
    """Split the scan output into file name and file content pairs."""
    files: list[tuple[str, str]] = []
    name: str | None = None
    lines: list[str] = []
    # str.splitlines() treats U+001E as a line boundary, so the marker would
    # be split away from the file name. Split on newlines only.
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith(SCAN_MARKER):
            if name is not None:
                files.append((name, "\n".join(lines).rstrip("\n")))
            name = line.strip(SCAN_MARKER).strip()
            lines = []
        elif name is not None:
            lines.append(line)
    if name is not None:
        files.append((name, "\n".join(lines).rstrip("\n")))
    return tuple(files)


def parse_desktop_entry(text: str) -> dict[str, str]:
    """Return the keys of the [Desktop Entry] group."""
    values: dict[str, str] = {}
    group = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            group = stripped[1:-1]
            continue
        if group != "Desktop Entry" or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def command_from_exec(exec_line: str) -> tuple[str, ...]:
    """Return the command line of an Exec value without the field codes."""
    try:
        tokens = shlex.split(exec_line)
    except ValueError:
        return ()
    command: list[str] = []
    for token in tokens:
        protected = token.replace("%%", "\0")
        for code in FIELD_CODES:
            protected = protected.replace(code, "")
        cleaned = protected.replace("\0", "%")
        if cleaned:
            command.append(cleaned)
    return tuple(command)


def is_menu_app(values: Mapping[str, str]) -> bool:
    """Return True when the entry describes something the menu can launch."""
    if values.get("Type", "Application") != "Application":
        return False
    for key in ("Hidden", "NoDisplay", "Terminal"):
        if values.get(key, "").strip().lower() == "true":
            return False
    if not values.get("Name", "").strip():
        return False
    return bool(command_from_exec(values.get("Exec", "")))


def safe_entry_name(container: str, app_id: str) -> str:
    """Return the generated file name for one container application."""
    return f"{GENERATED_PREFIX}{container}-{SAFE_NAME.sub('_', app_id)}.desktop"


def collect_apps(container: str, text: str) -> tuple[ContainerApp, ...]:
    """Return the menu applications found in one scan output."""
    apps: list[ContainerApp] = []
    for filename, content in split_desktop_output(text):
        if not filename.endswith(".desktop"):
            continue
        values = parse_desktop_entry(content)
        if not is_menu_app(values):
            continue
        app_id = filename[: -len(".desktop")]
        command = command_from_exec(values["Exec"])
        icon = values.get("Icon", "").strip() or None
        if icon is not None and icon.startswith("/"):
            # Icons inside the container are not copied; a path would not
            # resolve on the host.
            icon = None
        apps.append(
            ContainerApp(
                container=container,
                app_id=app_id,
                name=values["Name"].strip(),
                command=command,
                icon=icon,
                entry_name=safe_entry_name(container, app_id),
            )
        )
    return tuple(apps)


def quote_exec_argument(argument: str) -> str:
    """Quote one argument for an Exec value."""
    escaped = (
        argument.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("%", "%%")
    )
    if any(character in argument for character in EXEC_RESERVED):
        return f'"{escaped}"'
    return escaped


def render_entry(app: ContainerApp, desk_command: str) -> str:
    """Return the text of one generated .desktop file."""
    argv = (desk_command, "app", "launch", app.spec, "--", *app.command)
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={app.name} ({app.container})",
        f"Exec={' '.join(quote_exec_argument(item) for item in argv)}",
        "Terminal=false",
        "StartupNotify=false",
        f"X-Desk-Container={app.container}",
        f"X-Desk-App={app.app_id}",
    ]
    if app.icon:
        lines.append(f"Icon={app.icon}")
    return "\n".join(lines) + "\n"


def desk_command() -> str:
    """Return the command that a generated entry should call."""
    candidate = os.path.abspath(sys.argv[0])
    try:
        if os.path.isfile(candidate):
            return candidate
    except OSError:
        pass
    found = shutil.which("desk")
    return found if found is not None else "desk"


def run_refresh(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Scan every container and rewrite the generated menu entries."""
    output = sys.stdout if out is None else out
    commands = runner if runner is not None else CommandRunner()
    environment = os.environ if environ is None else environ

    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, paths.prefix, environment.get("PATH")
    )
    if proot_distro is None:
        raise MenuError(
            "the program 'proot-distro' was not found. "
            "Install it with 'pkg install proot-distro' first."
        )

    try:
        containers = list_containers(proot_distro, commands)
    except OSError as error:
        raise MenuError(f"could not list the containers: {error}") from error

    apps: list[ContainerApp] = []
    for container in containers:
        try:
            result = commands.run(scan_command(proot_distro, container), capture=True)
        except OSError as error:
            raise MenuError(f"could not scan the container {container}: {error}") from error
        if not result.succeeded:
            raise MenuError(
                f"could not read {APPLICATION_DIR} in {container}: "
                f"the command exited with status {result.returncode}"
            )
        apps.extend(collect_apps(container, result.stdout))

    menu_dir = paths.menu_dir
    menu_dir.mkdir(parents=True, exist_ok=True)
    command = desk_command()
    written: dict[str, Path] = {}
    for app in apps:
        path = menu_dir / app.entry_name
        path.write_text(render_entry(app, command), encoding="utf-8")
        written[app.entry_name] = path

    removed = 0
    for existing in sorted(menu_dir.glob(f"{GENERATED_PREFIX}*.desktop")):
        if existing.name in written:
            continue
        try:
            existing.unlink()
        except OSError as error:
            raise MenuError(f"could not remove the stale entry {existing}: {error}") from error
        removed += 1

    print(
        f"Scanned {len(containers)} containers and wrote {len(written)} entries",
        file=output,
    )
    for app in apps:
        print(f"  {app.container}/{app.app_id}: {app.name}", file=output)
    if removed:
        print(f"Removed {removed} entries that no longer match.", file=output)
