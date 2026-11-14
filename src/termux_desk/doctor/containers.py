"""Read-only checks for proot-distro containers."""

from __future__ import annotations

from typing import Mapping

from ..config import Paths, resolve_program
from ..runner import CommandRunner
from .base import FAILED, OK, WARNING, CheckResult


def collect_container_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
    runner: CommandRunner | None = None,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the container domain.

    required is False when desk doctor aggregates them: a machine without
    proot-distro or containers stays healthy. It is True for
    desk doctor --container: a missing proot-distro then fails.
    """
    from .. import container as container_module

    prefix = paths.prefix
    if prefix is None:
        return (CheckResult(name="containers", status=FAILED, message="PREFIX is not set."),)
    search_path = environ.get("PATH")
    proot_distro = resolve_program(
        container_module.PROOT_DISTRO_PROGRAM, prefix, search_path
    )
    if proot_distro is None:
        return (
            CheckResult(
                name="containers",
                status=FAILED if required else OK,
                message=(
                    "proot-distro was not found."
                    if required
                    else "not installed (optional)."
                ),
                hint="Run 'pkg install proot-distro' to create containers.",
            ),
        )
    commands = runner if runner is not None else CommandRunner()
    try:
        names = container_module.list_containers(proot_distro, commands)
    except OSError as error:
        return (
            CheckResult(
                name="containers",
                status=FAILED if required else WARNING,
                message=f"could not list the containers: {error}",
            ),
        )
    if not names:
        return (CheckResult(name="containers", status=OK, message="no containers are installed."),)
    results: list[CheckResult] = []
    for name in names:
        rootfs = container_module.rootfs_path(prefix, name)
        release = container_module.read_container_release(prefix, name)
        if release is None:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=FAILED,
                    message="the rootfs has no readable /etc/os-release.",
                    hint="Recreate the container with 'desk container init NAME'.",
                )
            )
            continue
        label = container_module.distro_label(release)
        entries = container_module.desktop_entry_count(rootfs)
        missing = [
            point
            for point in container_module.MOUNT_POINTS
            if not (rootfs / point.lstrip("/")).is_dir()
        ]
        if missing:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=WARNING,
                    message=(
                        f"{label}, {entries} desktop entries; missing mounts: "
                        f"{', '.join(missing)}."
                    ),
                    hint="Run 'desk container init NAME' to create the mount points.",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"container {name}",
                    status=OK,
                    message=f"{label}, {entries} desktop entries, mounts ready.",
                )
            )
    return tuple(results)
