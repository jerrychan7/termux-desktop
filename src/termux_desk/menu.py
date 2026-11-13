"""Generate host menu entries for the applications installed in containers.

The entries live in the per-user applications directory, next to the hand
written ones, and every generated file starts with GENERATED_PREFIX so that
a later refresh can remove the files it wrote without touching anything else.

Only the [Desktop Entry] group is read. Entries that ask for a terminal, are
hidden, or are not applications are skipped, because desk app launch has no
terminal to give them and the desktop would show nothing.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, replace
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

# Icon files are looked up in these container directories, most specific
# size first, because a generated entry needs a file that the host can read.
ICON_DIRECTORIES = (
    "/usr/share/icons/hicolor/128x128/apps",
    "/usr/share/icons/hicolor/64x64/apps",
    "/usr/share/icons/hicolor/48x48/apps",
    "/usr/share/icons/hicolor/32x32/apps",
    "/usr/share/icons/hicolor/scalable/apps",
    "/usr/share/pixmaps",
)
ICON_SUFFIXES = (".png", ".svg", ".xpm")
MAX_ICON_BYTES = 512 * 1024
ICON_NAME = re.compile(r"^[A-Za-z0-9._+@-]+$")


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
    localized_names: tuple[tuple[str, str], ...] = ()
    icon_file: Path | None = None

    @property
    def spec(self) -> str:
        """Return the container/app form that desk app launch accepts."""
        return f"{self.container}/{self.app_id}"


@dataclass(frozen=True)
class RefreshReport:
    """What one refresh scanned and wrote."""

    containers: tuple[str, ...]
    entries: tuple[ContainerApp, ...]
    missing: tuple[tuple[str, str], ...] = ()

    def count_for(self, container: str) -> int:
        """Return the number of entries written for one container."""
        return sum(1 for app in self.entries if app.container == container)


def scan_command(proot_distro: str, container: str) -> tuple[str, ...]:
    """Build the command that prints every desktop file of one container."""
    script = (
        f"cd {APPLICATION_DIR} 2>/dev/null || exit 0; "
        'for f in *.desktop; do [ -f "$f" ] || continue; '
        "printf '\\036%s\\036\\n' \"$f\"; cat \"$f\"; echo; done"
    )
    return container_command(proot_distro, container, "sh", "-c", script)


def icon_candidates(icon: str) -> tuple[str, ...]:
    """Return the container paths that may hold the icon of one entry.

    Icon may be an icon name or an absolute path inside the container. The
    host has neither the container icon themes nor the container path, so
    the file is copied out of the container and the entry gets the host path.
    """
    if not icon:
        return ()
    if icon.startswith("/"):
        if Path(icon).suffix.lower() in ICON_SUFFIXES:
            return (icon,)
        return tuple(f"{icon}{suffix}" for suffix in ICON_SUFFIXES)
    if ICON_NAME.match(icon) is None:
        return ()
    return tuple(
        f"{directory}/{icon}{suffix}"
        for directory in ICON_DIRECTORIES
        for suffix in ICON_SUFFIXES
    )


def icon_scan_command(
    proot_distro: str, container: str, icon: str
) -> tuple[str, ...]:
    """Build the command that prints one icon file as base64.

    The first candidate that exists is printed as the marker, the container
    path, the marker, the base64 text, and the marker. An empty result means
    the container has no such icon file.
    """
    candidates = icon_candidates(icon)
    if not candidates:
        return ()
    quoted = " ".join(shlex.quote(item) for item in candidates)
    script = (
        f"# desk icon {icon}\n"
        f"for f in {quoted}; do "
        'if [ -f "$f" ]; then '
        'printf "\\036%s\\036" "$f"; base64 "$f" | tr -d "\\n"; printf "\\036\\n"; '
        "exit 0; fi; done; exit 0"
    )
    return container_command(proot_distro, container, "sh", "-c", script)


def parse_icon_output(text: str) -> tuple[str, bytes] | None:
    """Return the container path and the bytes printed by icon_scan_command."""
    # str.strip() removes U+001E as well, because Python counts the
    # separator characters as whitespace, so only spaces and newlines go.
    parts = text.replace("\r\n", "\n").strip(" \t\n").split(SCAN_MARKER)
    if len(parts) != 4:
        return None
    container_path = parts[1].strip()
    encoded = parts[2].strip()
    if not container_path or not encoded:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        return None
    return container_path, data


def copy_icon(
    container: str,
    icon: str,
    icon_dir: Path,
    *,
    proot_distro: str,
    commands: CommandRunner,
) -> Path | None:
    """Copy one icon out of a container and return the host file.

    A container may not have the icon file even when the entry names one,
    so a missing file, a failed command, or an oversized file all return
    None and the entry keeps the icon name it already had.
    """
    command = icon_scan_command(proot_distro, container, icon)
    if not command:
        return None
    try:
        result = commands.run(command, capture=True)
    except OSError:
        return None
    if not result.succeeded:
        return None
    parsed = parse_icon_output(result.stdout)
    if parsed is None:
        return None
    container_path, data = parsed
    if not data or len(data) > MAX_ICON_BYTES:
        return None
    suffix = Path(container_path).suffix.lower()
    if suffix not in ICON_SUFFIXES:
        suffix = ".png"
    target = icon_dir / SAFE_NAME.sub("_", Path(container_path).stem + suffix)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError:
        return None
    return target


def prune_icon_directory(directory: Path, keep: set[str]) -> None:
    """Remove copied icon files that this refresh did not write."""
    if not directory.is_dir():
        return
    for existing in sorted(directory.iterdir()):
        if existing.name in keep or not existing.is_file():
            continue
        try:
            existing.unlink()
        except OSError:
            continue


def prune_removed_containers(paths: Paths, containers: Sequence[str]) -> None:
    """Remove the icon directories of containers that no longer exist."""
    root = paths.icon_root
    if not root.is_dir():
        return
    keep = {SAFE_NAME.sub("_", container) for container in containers}
    for existing in sorted(root.iterdir()):
        if not existing.is_dir() or existing.name in keep:
            continue
        shutil.rmtree(existing, ignore_errors=True)


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


def command_check_command(
    proot_distro: str, container: str, commands: Sequence[str]
) -> tuple[str, ...]:
    """Build the command that reports which Exec programs resolve in a container.

    An absolute path is checked with -x; a bare name with command -v. The
    answer only says whether the command exists, not whether the program
    starts, because a program may legitimately exit at once.
    """
    quoted = " ".join(shlex.quote(item) for item in commands)
    script = (
        "# desk check\n"
        f"for cmd in {quoted}; do "
        'case "$cmd" in '
        '/*) if [ -x "$cmd" ]; then s=ok; else s=missing; fi;; '
        '*) if command -v "$cmd" >/dev/null 2>&1; then s=ok; else s=missing; fi;; '
        'esac; '
        "printf '\\036%s\\036%s\\036\\n' \"$s\" \"$cmd\"; done"
    )
    return container_command(proot_distro, container, "sh", "-c", script)


def parse_check_output(text: str) -> set[str]:
    """Return the commands that command_check_command reported as missing."""
    missing: set[str] = set()
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.startswith(SCAN_MARKER):
            continue
        parts = line.split(SCAN_MARKER)
        if len(parts) < 4:
            continue
        status = parts[1].strip()
        command = parts[2].strip()
        if status == "missing" and command:
            missing.add(command)
    return missing


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


def localized_name_pairs(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """Return the Name[locale] pairs that the entry carries."""
    pairs = [
        (key, value)
        for key, value in values.items()
        if key.startswith("Name[") and key.endswith("]") and value
    ]
    return tuple(sorted(pairs))


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
        apps.append(
            ContainerApp(
                container=container,
                app_id=app_id,
                name=values["Name"].strip(),
                command=command,
                icon=icon,
                entry_name=safe_entry_name(container, app_id),
                localized_names=localized_name_pairs(values),
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
    ]
    for key, value in app.localized_names:
        lines.append(f"{key}={value}")
    lines.extend(
        [
            f"Exec={' '.join(quote_exec_argument(item) for item in argv)}",
            "Terminal=false",
            "StartupNotify=false",
            f"X-Desk-Container={app.container}",
            f"X-Desk-App={app.app_id}",
        ]
    )
    if app.icon_file is not None:
        lines.append(f"Icon={os.fspath(app.icon_file)}")
    elif app.icon:
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
    quiet: bool = False,
) -> RefreshReport:
    """Scan every container and rewrite the generated menu entries.

    Every icon that a container entry names is copied into
    ~/.local/share/icons/termux-desktop/<container>/ and the entry points at
    the copied file, because the host has neither the container icon themes
    nor the container paths. A copy that fails leaves the entry with the
    icon name it already had. The return value says which containers were
    scanned and how many entries each one produced, which is what desk pkg
    reports after a package manager run. quiet keeps the detailed report
    off the screen; desk start uses it because the menu is not what the
    user asked about there.
    """
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
    missing: list[tuple[str, str]] = []
    icons_attempted = 0
    icons_copied = 0
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
        container_apps = list(collect_apps(container, result.stdout))
        first_commands = sorted(
            {app.command[0] for app in container_apps if app.command}
        )
        if first_commands:
            try:
                checked = commands.run(
                    command_check_command(proot_distro, container, first_commands),
                    capture=True,
                )
            except OSError:
                checked = None
            if checked is not None and checked.succeeded:
                unavailable = parse_check_output(checked.stdout)
                missing.extend(
                    (app.spec, app.command[0])
                    for app in container_apps
                    if app.command and app.command[0] in unavailable
                )
        icon_dir = paths.icon_root / SAFE_NAME.sub("_", container)
        copied: set[str] = set()
        icon_names = sorted({app.icon for app in container_apps if app.icon})
        for icon in icon_names:
            icons_attempted += 1
            target = copy_icon(
                container,
                icon,
                icon_dir,
                proot_distro=proot_distro,
                commands=commands,
            )
            if target is None:
                continue
            copied.add(target.name)
            icons_copied += 1
            container_apps = [
                replace(app, icon_file=target) if app.icon == icon else app
                for app in container_apps
            ]
        prune_icon_directory(icon_dir, copied)
        apps.extend(container_apps)

    prune_removed_containers(paths, containers)

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

    report = RefreshReport(
        containers=containers, entries=tuple(apps), missing=tuple(missing)
    )
    if quiet:
        return report

    print(
        f"Scanned {len(containers)} containers and wrote {len(written)} entries",
        file=output,
    )
    for app in apps:
        print(f"  {app.container}/{app.app_id}: {app.name}", file=output)
    if missing:
        for spec, command in missing:
            print(
                f"  {spec}: the command {command} was not found in the container",
                file=output,
            )
        count = len(missing)
        noun = "application" if count == 1 else "applications"
        pronoun = "it" if count == 1 else "they"
        print(
            f"Found {count} {noun} whose command was not found; "
            f"{pronoun} may fail to start.",
            file=output,
        )
    if removed:
        print(f"Removed {removed} entries that no longer match.", file=output)
    if icons_attempted:
        print(f"Copied {icons_copied} of {icons_attempted} icons.", file=output)
    return report


def refresh_for_session(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> None:
    """Refresh the menu entries before a session starts.

    Containers can only exist while proot-distro is installed, so a missing
    proot-distro means there is nothing to scan and the step is skipped
    silently. A refresh that fails is reported as a warning: the session
    start must not fail because a menu file could not be written, and the
    next desk app refresh repairs it.
    """
    output = sys.stdout if out is None else out
    environment = os.environ if environ is None else environ
    proot_distro = resolve_program(
        PROOT_DISTRO_PROGRAM, paths.prefix, environment.get("PATH")
    )
    if proot_distro is None:
        return
    try:
        run_refresh(
            paths,
            runner=runner,
            environ=environment,
            out=output,
            quiet=True,
        )
    except MenuError as error:
        print(f"the menu was not refreshed: {error}", file=output)
