"""Read-only checks for the optional VNC components."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..config import DeskError, Paths, resolve_program
from ..session import pid_alive
from .base import FAILED, OK, WARNING, CheckResult

VNC_WEB_DIR_NAME = "vnc-web"


def _vnc_program_check(
    name: str,
    paths: Paths,
    search_path: str | None,
    *,
    required: bool,
    hint: str,
) -> CheckResult:
    """Report one VNC program, failing only when VNC was asked for."""
    executable = resolve_program(name, paths.prefix, search_path)
    if executable is not None:
        return CheckResult(name=f"VNC {name}", status=OK, message=str(executable))
    if required:
        return CheckResult(
            name=f"VNC {name}",
            status=FAILED,
            message="not found.",
            hint=hint,
        )
    return CheckResult(
        name=f"VNC {name}",
        status=OK,
        message="not installed (optional).",
        hint=hint,
    )


def _vnc_asset_check(
    name: str,
    path: Path | None,
    *,
    required: bool,
    hint: str,
) -> CheckResult:
    """Report a downloaded VNC asset; it is optional unless VNC was asked for."""
    if path is not None and path.exists():
        return CheckResult(name=f"VNC {name}", status=OK, message=str(path))
    if required:
        return CheckResult(name=f"VNC {name}", status=WARNING, message="not installed.", hint=hint)
    return CheckResult(
        name=f"VNC {name}",
        status=OK,
        message="not installed (optional).",
        hint=hint,
    )


def _web_root(paths: Paths) -> Path | None:
    """Return the directory that holds the downloaded web assets."""
    if paths.prefix is None:
        return None
    return paths.prefix / "share" / "termux-desktop" / VNC_WEB_DIR_NAME


def _program_checks(
    paths: Paths, search_path: str | None, *, required: bool
) -> list[CheckResult]:
    """Report the VNC programs and darkhttpd."""
    return [
        _vnc_program_check(
            "Xvnc", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "x0vncserver", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "vncpasswd", paths, search_path, required=required,
            hint="Run 'desk vnc setup' to install tigervnc.",
        ),
        _vnc_program_check(
            "darkhttpd", paths, search_path, required=False,
            hint="Run 'desk vnc setup' to install darkhttpd for the rs proxy.",
        ),
    ]


def _proxy_check(
    rs_proxy: Path | None, py_proxy: Path | None, *, required: bool
) -> CheckResult:
    """Report which downloaded WebSocket proxies exist."""
    installed_proxies = [
        label
        for label, path in (("rs", rs_proxy), ("py", py_proxy))
        if path is not None and path.exists()
    ]
    if installed_proxies:
        return CheckResult(
            name="VNC websocket proxies",
            status=OK,
            message="installed: " + ", ".join(installed_proxies) + ".",
        )
    return _vnc_asset_check(
        "websocket proxies", None, required=required,
        hint="Run 'desk vnc web setup' to download a proxy.",
    )


def _mirror_check(paths: Paths) -> CheckResult:
    """Report the recorded x0vncserver mirror."""
    from .. import vnc

    try:
        mirror_state = vnc.read_mirror_state(paths, vnc.MIRROR_DISPLAY)
    except vnc.VncError as error:
        return CheckResult(
            name="VNC mirror state",
            status=WARNING,
            message=str(error),
            hint="Remove the mirror state file if no mirror is running.",
        )
    if mirror_state is None:
        return CheckResult(
            name="VNC mirror state",
            status=OK,
            message="no mirror is recorded.",
        )
    if pid_alive(mirror_state.pid):
        return CheckResult(
            name="VNC mirror state",
            status=OK,
            message=f"mirroring :0 with pid {mirror_state.pid}.",
        )
    return CheckResult(
        name="VNC mirror state",
        status=WARNING,
        message=f"the recorded mirror is stale (pid {mirror_state.pid}).",
        hint="Run 'desk vnc stop --display :0' to remove the stale state.",
    )


def _web_state_check(paths: Paths) -> CheckResult:
    """Report web proxy state files whose process is gone."""
    from .. import webproxy

    try:
        displays = webproxy.web_displays(paths)
    except (DeskError, OSError) as error:
        return CheckResult(
            name="VNC web state",
            status=WARNING,
            message=str(error),
        )

    stale: list[str] = []
    for display in displays:
        try:
            state = webproxy.read_web_state(paths, display)
        except (DeskError, OSError) as error:
            stale.append(f"{display} ({error})")
            continue
        if state is None or pid_alive(state.web_pid):
            continue
        stale.append(display)
    if stale:
        return CheckResult(
            name="VNC web state",
            status=WARNING,
            message="stale state for " + ", ".join(stale) + ".",
            hint="Run 'desk vnc stop --all' to remove stale web state.",
        )
    return CheckResult(
        name="VNC web state",
        status=OK,
        message="no stale web state is recorded.",
    )


def collect_vnc_checks(
    paths: Paths,
    *,
    environ: Mapping[str, str],
    required: bool,
) -> tuple[CheckResult, ...]:
    """Run the checks that belong to the VNC domain.

    required is False when desk doctor aggregates them: missing optional
    pieces stay OK, so a machine without VNC is not reported as unhealthy.
    It is True for desk doctor --vnc and desk vnc doctor: the essential
    programs then fail and the downloaded assets warn.
    """
    web_root = _web_root(paths)
    page = None if web_root is None else web_root / "novnc" / "vnc.html"
    rs_proxy = None if web_root is None else web_root / "websockify_rs"
    py_proxy = None if web_root is None else web_root / "websockify"

    search_path = environ.get("PATH")
    checks = _program_checks(paths, search_path, required=required)
    checks.append(
        _vnc_asset_check(
            "noVNC page", page, required=required,
            hint="Run 'desk vnc web setup' to download noVNC.",
        )
    )
    checks.append(_proxy_check(rs_proxy, py_proxy, required=required))
    checks.append(_mirror_check(paths))
    checks.append(_web_state_check(paths))
    return tuple(checks)
