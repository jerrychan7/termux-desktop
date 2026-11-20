"""Experimental: build a skin's assets from an upstream source inside a container."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence, TextIO

from . import container as container_tools
from .config import DeskError, Paths
from .theme_model import _load_yaml, _printable, _read_manifest

BUILD_DIRECTORY_NAME = "builds"
CONTAINER_WORK_DIRECTORY = "/home/termux/desk-build"
BUILD_ARTIFACT_KINDS = ("theme", "icons", "cursors")
BUILD_KEYS = {"build", "container", "source", "packages", "commands", "artifacts"}
SOURCE_KEYS = {"url", "sha256", "strip"}
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_COMMANDS = 40
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


class BuildError(DeskError):
    """The build recipe or the build itself cannot be used."""


@dataclass(frozen=True)
class BuildArtifact:
    path: str
    kind: str
    target: str


@dataclass(frozen=True)
class BuildRecipe:
    name: str
    container: str
    source_url: str
    source_sha256: str | None
    strip: int
    packages: tuple[str, ...]
    commands: tuple[str, ...]
    artifacts: tuple[BuildArtifact, ...]


def _validate_artifact(entry: object, source: Path) -> BuildArtifact:
    if not isinstance(entry, dict):
        raise BuildError(f"{source} has a non-mapping artifact")
    unknown = set(entry) - {"path", "kind", "target"}
    if unknown:
        raise BuildError(f"{source} has unknown artifact keys: {', '.join(sorted(unknown))}")
    path = entry.get("path")
    kind = entry.get("kind")
    target = entry.get("target")
    if not isinstance(path, str) or not path.strip():
        raise BuildError(f"{source} has an artifact without a path")
    if not isinstance(kind, str) or kind not in BUILD_ARTIFACT_KINDS:
        raise BuildError(f"{source} has an unsupported artifact kind: {kind!r}")
    if not isinstance(target, str) or not target.strip():
        raise BuildError(f"{source} has an artifact without a target name")
    return BuildArtifact(path=path.strip(), kind=kind, target=target.strip())


def load_recipe(path: Path) -> BuildRecipe:
    """Read and validate one build recipe."""
    record = _load_yaml(_read_manifest(path), path)
    if not isinstance(record, dict):
        raise BuildError(f"{path} must contain a mapping")
    unknown = set(record) - BUILD_KEYS
    if unknown:
        raise BuildError(f"{path} uses unknown keys: {', '.join(sorted(unknown))}")
    name = record.get("build", path.stem)
    if not isinstance(name, str) or not name.strip():
        raise BuildError(f"{path} has an invalid build name")
    container = record.get("container", "ubuntu")
    if not isinstance(container, str) or not container.strip():
        raise BuildError(f"{path} has an invalid container name")
    source = record.get("source")
    if not isinstance(source, dict):
        raise BuildError(f"{path} needs a 'source' mapping")
    unknown_source = set(source) - SOURCE_KEYS
    if unknown_source:
        raise BuildError(f"{path} has unknown source keys: {', '.join(sorted(unknown_source))}")
    url = source.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise BuildError(f"{path} needs an HTTPS 'source.url'")
    digest = source.get("sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
        raise BuildError(f"{path} has an invalid source sha256")
    strip = source.get("strip", 1)
    if not isinstance(strip, int) or isinstance(strip, bool) or not 0 <= strip <= 3:
        raise BuildError(f"{path} has an invalid source strip")
    packages = record.get("packages", [])
    if not isinstance(packages, list) or any(not isinstance(entry, str) for entry in packages):
        raise BuildError(f"{path} has an invalid packages list")
    commands = record.get("commands", [])
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise BuildError(f"{path} needs 1 to {MAX_COMMANDS} commands")
    if any(not isinstance(entry, str) or not entry.strip() for entry in commands):
        raise BuildError(f"{path} has an empty command")
    artifacts = record.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        raise BuildError(f"{path} needs at least one artifact")
    return BuildRecipe(
        name=name,
        container=container,
        source_url=url,
        source_sha256=digest,
        strip=strip,
        packages=tuple(packages),
        commands=tuple(entry.strip() for entry in commands),
        artifacts=tuple(_validate_artifact(entry, path) for entry in artifacts),
    )


def _fetch(url: str, expected: str | None) -> bytes:
    import urllib.request

    if not url.startswith("https://"):
        raise BuildError(f"{_printable(url)} is not HTTPS")
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise BuildError(f"{_printable(url)} is larger than {MAX_SOURCE_BYTES} bytes")
    if expected:
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise BuildError(f"{_printable(url)} has sha256 {digest}, expected {expected}")
    return data


def _build_script(recipe: BuildRecipe, workdir: str) -> str:
    lines = ["set -e", f"cd {workdir}"]
    if recipe.strip:
        lines.append(f"tar -xf source.tar.gz --strip-components={recipe.strip}")
    else:
        lines.append("tar -xf source.tar.gz")
    if recipe.packages:
        lines.append("apt-get update -qq")
        lines.append("apt-get install -y --no-install-recommends " + " ".join(recipe.packages))
    lines.extend(recipe.commands)
    return "\n".join(lines)


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def build_directory(paths: Paths, name: str) -> Path:
    return paths.xdg_data_home / "termux-desktop" / BUILD_DIRECTORY_NAME / name


def run_skin_build(
    paths: Paths,
    recipe_path: Path,
    *,
    yes: bool = False,
    out: TextIO,
    fetch: Callable[[str, str | None], bytes] | None = None,
    runner: Runner | None = None,
    proot_distro: str = container_tools.PROOT_DISTRO_PROGRAM,
) -> int:
    """Build the artifacts of one recipe inside a proot container."""
    recipe = load_recipe(recipe_path)
    if not yes:
        print(
            "this command downloads a source archive and runs the recipe's commands "
            f"inside the '{recipe.container}' container; pass --yes to continue.",
            file=out,
        )
        return 1
    runner = runner or _default_runner
    listing = runner((proot_distro, "list"))
    if listing.returncode:
        raise BuildError(f"could not list containers: {(listing.stderr or '').strip()}")
    if recipe.container not in container_tools.parse_container_list(listing.stdout or ""):
        raise BuildError(
            f"there is no container named {recipe.container!r}; "
            "run 'desk container list' or create one with 'desk container init'"
        )
    rootfs = container_tools.rootfs_path(paths.prefix, recipe.container)
    workdir = rootfs / CONTAINER_WORK_DIRECTORY.lstrip("/") / recipe.name
    workdir.mkdir(parents=True, exist_ok=True)
    source = (fetch or _fetch)(recipe.source_url, recipe.source_sha256)
    (workdir / "source.tar.gz").write_bytes(source)
    script = _build_script(recipe, f"{CONTAINER_WORK_DIRECTORY}/{recipe.name}")
    result = runner((proot_distro, "login", recipe.container, "--", "sh", "-lc", script))
    if getattr(result, "stdout", ""):
        print(result.stdout, file=out)
    if result.returncode:
        detail = (getattr(result, "stderr", "") or "").strip()
        raise BuildError(f"the build failed with exit code {result.returncode}: {detail}")
    destination = build_directory(paths, recipe.name)
    for artifact in recipe.artifacts:
        source_path = rootfs / artifact.path.lstrip("/")
        if not source_path.exists():
            raise BuildError(f"the build did not produce {_printable(artifact.path)}")
        target = destination / artifact.target
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, target)
        else:
            shutil.copyfile(source_path, target)
        print(f"built {artifact.kind} {_printable(artifact.target)}: {target}", file=out)
    print(
        "reference the result with a 'path:' asset, for example:\n"
        f"  - kind: {recipe.artifacts[0].kind}\n"
        f"    target: {recipe.artifacts[0].target}\n"
        f"    path: {destination / recipe.artifacts[0].target}",
        file=out,
    )
    return 0
