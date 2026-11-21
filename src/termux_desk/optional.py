"""Install the optional components a skin asks for.

A skin may list extra components in its manifest (``requires``/``optional``).
Each name maps to a small recipe in ``presets/optional`` that says which
Termux packages to install and which commands to run afterwards.  A component
that cannot be installed is reported and skipped: an optional component never
fails the skin itself.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TextIO

from .config import DeskError, Paths
from .settings.model import config_directory

BUILTIN_DIRECTORY = Path(__file__).resolve().parent / "presets" / "optional"
RECIPE_NAME = "optional"

PACKAGE_TIMEOUT = 1800


class OptionalError(DeskError):
    """An optional component recipe is malformed."""


@dataclass(frozen=True)
class Recipe:
    name: str
    summary: str
    packages: tuple[str, ...]
    commands: tuple[str, ...]


def _recipe_file(
    paths: Paths, name: str, environ: Mapping[str, str] | None = None
) -> Path:
    return config_directory(paths, environ) / "optional" / f"{name}.yaml"


def load_recipe(
    paths: Paths, name: str, environ: Mapping[str, str] | None = None
) -> Recipe | None:
    """Return the recipe for one component, or None when there is none."""
    candidate = _recipe_file(paths, name, environ)
    if not candidate.is_file():
        candidate = BUILTIN_DIRECTORY / f"{name}.yaml"
    if not candidate.is_file():
        return None
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - PyYAML is a runtime extra
        raise OptionalError("PyYAML is required to read optional recipes") from error
    try:
        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise OptionalError(f"{candidate}: {error}") from error
    if not isinstance(data, dict):
        raise OptionalError(f"{candidate}: the recipe must be a mapping")
    return Recipe(
        name=name,
        summary=str(data.get("summary") or ""),
        packages=_strings(data.get("packages"), candidate, "packages"),
        commands=_strings(data.get("commands"), candidate, "commands"),
    )


def _strings(value: object, path: Path, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise OptionalError(f"{path}: {field} must be a list of non-empty strings")
    return tuple(value)


def _installed(package: str) -> bool:
    """Say whether a Termux package is installed already."""
    if shutil.which(package) is not None:
        return True
    try:
        completed = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.stdout.strip() == "install ok installed"


def install(
    paths: Paths,
    names: tuple[str, ...],
    *,
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    dry_run: bool = False,
    runner=subprocess.run,
    installed=_installed,
) -> int:
    """Install every component, skipping the ones that are not available.

    Returns the number of components that were installed or were already
    present.  A component that fails is reported and left out of the count.
    """
    installed_count = 0
    for name in names:
        recipe = load_recipe(paths, name, environ)
        if recipe is None:
            print(f"optional {name}: no recipe, skipped", file=out)
            continue
        if all(installed(package) for package in recipe.packages):
            print(f"optional {name}: already installed", file=out)
            installed_count += 1
            continue
        if dry_run:
            print(
                f"optional {name}: would install "
                + ", ".join(recipe.packages)
                + (" then run %d command(s)" % len(recipe.commands) if recipe.commands else ""),
                file=out,
            )
            installed_count += 1
            continue
        missing = [package for package in recipe.packages if not installed(package)]
        if missing:
            print(f"optional {name}: installing {', '.join(missing)}", file=out)
            if not _run(
                ["pkg", "install", "-y", *missing],
                out=out,
                environ=environ,
                runner=runner,
            ):
                print(f"optional {name}: could not be installed, skipped", file=out)
                continue
        failed = False
        for command in recipe.commands:
            if not _run(["sh", "-c", command], out=out, environ=environ, runner=runner):
                print(f"optional {name}: command failed, skipped: {command}", file=out)
                failed = True
                break
        if not failed:
            print(f"optional {name}: installed", file=out)
            installed_count += 1
    return installed_count


def _run(
    argv: list[str],
    *,
    out: TextIO,
    environ: Mapping[str, str] | None,
    runner,
) -> bool:
    try:
        completed = runner(argv, capture_output=True, text=True, timeout=PACKAGE_TIMEOUT, env=environ)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"  {argv[0]}: {error}", file=out)
        return False
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        print(f"  {argv[0]} exited {completed.returncode}" + (f": {detail[-1]}" if detail else ""), file=out)
        return False
    return True
