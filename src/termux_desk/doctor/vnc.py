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
    from .. import vnc, webproxy

    prefix = paths.prefix
    web_root = (
        prefix / "share" / "termux-desktop" / VNC_WEB_DIR_NAME
        if prefix is not None
        else None
    )
    page = None if web_root is None else web_root / "novnc" / "vnc.html"
    rs_proxy = None if web_root is None else web_root / "websockify_rs"
    py_proxy = None if web_root is None else web_root / "websockify"

    search_path = environ.get("PATH")
    checks = [
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
        _vnc_asset_check(
            "noVNC page", page, required=required,
            hint="Run 'desk vnc web setup' to download noVNC.",
        ),
    ]

    installed_proxies = [
        label
        for label, path in (("rs", rs_proxy), ("py", py_proxy))
        if path is not None and path.exists()
    ]
    if installed_proxies:
        checks.append(
            CheckResult(
                name="VNC websocket proxies",
                status=OK,
                message="installed: " + ", ".join(installed_proxies) + ".",
            )
        )
    else:
        checks.append(
            _vnc_asset_check(
                "websocket proxies", None, required=required,
                hint="Run 'desk vnc web setup' to download a proxy.",
            )
        )

    try:
        mirror_state = vnc.read_mirror_state(paths, vnc.MIRROR_DISPLAY)
    except vnc.VncError as error:
        checks.append(
            CheckResult(
                name="VNC mirror state",
                status=WARNING,
                message=str(error),
                hint="Remove the mirror state file if no mirror is running.",
            )
        )
    else:
        if mirror_state is None:
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=OK,
                    message="no mirror is recorded.",
                )
            )
        elif pid_alive(mirror_state.pid):
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=OK,
                    message=f"mirroring :0 with pid {mirror_state.pid}.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="VNC mirror state",
                    status=WARNING,
                    message=f"the recorded mirror is stale (pid {mirror_state.pid}).",
                    hint="Run 'desk vnc stop --display :0' to remove the stale state.",
                )
            )

    stale_web: list[str] = []
    try:
        displays = webproxy.web_displays(paths)
    except (DeskError, OSError) as error:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=WARNING,
                message=str(error),
            )
        )
        return tuple(checks)
    for display in displays:
        try:
            state = webproxy.read_web_state(paths, display)
        except (DeskError, OSError) as error:
            stale_web.append(f"{display} ({error})")
            continue
        if state is None or pid_alive(state.web_pid):
            continue
        stale_web.append(display)
    if stale_web:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=WARNING,
                message="stale state for " + ", ".join(stale_web) + ".",
                hint="Run 'desk vnc stop --all' to remove stale web state.",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="VNC web state",
                status=OK,
                message="no stale web state is recorded.",
            )
        )

    return tuple(checks)
