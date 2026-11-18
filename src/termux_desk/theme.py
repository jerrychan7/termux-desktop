"""Named XFCE appearance sets (themes) and the commands that apply them.

The model lives in theme_model, asset downloads in theme_assets and the
xfconf writer in theme_session; this module keeps the apply/reset flow and
re-exports the names the rest of desk uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TextIO

from . import fonts as font_tools
from .config import Paths
from .theme_assets import Fetcher as Fetcher
from .theme_assets import _default_fetcher as _default_fetcher
from .theme_assets import _extract_archive as _extract_archive
from .theme_assets import _install_assets as _install_assets
from .theme_assets import _sha256 as _sha256
from .theme_model import INIT_TEMPLATE as INIT_TEMPLATE
from .theme_model import Theme as Theme
from .theme_model import ThemeAsset as ThemeAsset
from .theme_model import ThemeError as ThemeError
from .theme_model import ThemeState as ThemeState
from .theme_model import _printable as _printable
from .theme_model import _write_state as _write_state
from .theme_model import assets_directory as assets_directory
from .theme_model import config_directory as config_directory
from .theme_model import list_theme_names as list_theme_names
from .theme_model import load_theme as load_theme
from .theme_model import presets_directory as presets_directory
from .theme_model import read_state as read_state
from .theme_model import run_theme_init as run_theme_init
from .theme_model import run_theme_list as run_theme_list
from .theme_model import run_theme_show as run_theme_show
from .theme_model import state_path as state_path
from .theme_model import themes_directory as themes_directory
from .theme_session import Runner as Runner
from .theme_session import _check_available_components as _check_available_components
from .theme_session import _check_fonts as _check_fonts
from .theme_session import _default_runner as _default_runner
from .theme_session import _font_matcher as _font_matcher
from .theme_session import _needed_properties as _needed_properties
from .theme_session import _planned_writes as _planned_writes
from .theme_session import _reload as _reload
from .theme_session import _resolve_components as _resolve_components
from .theme_session import _restore_snapshot as _restore_snapshot
from .theme_session import _session_environment as _session_environment
from .theme_session import _snapshot as _snapshot
from .theme_session import _write_components as _write_components
from .theme_session import _write_fonts as _write_fonts


def run_theme_apply(
    paths: Paths,
    name: str,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch: Fetcher | None = None,
    runner: Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Apply the theme with this name."""
    theme = load_theme(paths, name, environ)
    return apply_theme(
        paths,
        theme,
        allow_unverified=allow_unverified,
        force=force,
        dry_run=dry_run,
        display=display,
        out=out,
        fetch=fetch,
        runner=runner,
        matcher=matcher,
        session=session,
    )


@dataclass(frozen=True)
class AppearanceApplication:
    """The values one apply replaced, so a caller can undo just that step."""

    previous: ThemeState
    before: tuple[dict[str, str], ...]
    display: str


def apply_theme(
    paths: Paths,
    theme: Theme,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    fetch: Fetcher | None = None,
    runner: Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Install any missing assets, apply a parsed theme and remember the old values."""
    apply_appearance(
        paths,
        theme,
        allow_unverified=allow_unverified,
        force=force,
        dry_run=dry_run,
        display=display,
        out=out,
        fetch=fetch,
        runner=runner,
        matcher=matcher,
        session=session,
    )
    return 0


def apply_appearance(
    paths: Paths,
    theme: Theme,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    dry_run: bool = False,
    display: str = ":0",
    out: TextIO,
    fetch: Fetcher | None = None,
    runner: Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> AppearanceApplication | None:
    """Do the apply and return what it replaced, or None for a dry run."""
    environment = dict(session) if session is not None else _session_environment(display)
    state = read_state(paths)
    fetch = fetch or _default_fetcher
    runner = runner or _default_runner
    matcher = _font_matcher(paths, matcher)
    _check_available_components(theme, paths, matcher, out)
    _check_fonts(theme, matcher)
    if dry_run:
        planned = _planned_writes(theme, theme.components, environment, runner)
        for channel, name, value in planned:
            print(f"would set {channel}{name}: {_printable(value)}", file=out)
        print(f"would apply theme {theme.name}", file=out)
        return None
    installed_state, installed = _install_assets(
        paths, theme, allow_unverified=allow_unverified, force=force, state=state, fetch=fetch, out=out
    )
    components = _resolve_components(theme, installed, paths, matcher, out)
    current = _snapshot(environment, runner, _needed_properties(theme, components, environment, runner))
    # 第一次 apply 才记录“最初”的值；重复 apply 保留它，reset 才能回到原点
    snapshot = state.snapshot or current
    merged = ThemeState(
        active=state.active,
        snapshot=snapshot,
        installed=installed_state.installed,
        hashes=installed_state.hashes,
        display=state.display,
    )
    # 先落盘已验证的哈希，后面即使失败也不会丢掉这层信任记录
    _write_state(paths, merged)
    try:
        _write_components(components, environment, runner)
        _write_fonts(theme, paths, environment, runner, matcher)
        _write_state(
            paths,
            ThemeState(
                active=theme.name,
                snapshot=snapshot,
                installed=merged.installed,
                hashes=merged.hashes,
                display=display,
            ),
        )
        _reload(environment, out)
    except Exception:
        _restore_snapshot(current, environment, runner)
        _write_state(
            paths,
            ThemeState(
                active=state.active,
                snapshot=state.snapshot,
                installed=merged.installed,
                hashes=merged.hashes,
                display=state.display,
            ),
        )
        raise
    print(f"applied theme {theme.name}", file=out)
    return AppearanceApplication(previous=state, before=current, display=display)


def rollback_appearance(
    paths: Paths,
    application: AppearanceApplication,
    *,
    out: TextIO,
    runner: Runner | None = None,
    session: Mapping[str, str] | None = None,
) -> None:
    """Put back the values this apply replaced and restore the state before it."""
    environment = (
        dict(session) if session is not None else _session_environment(application.display)
    )
    runner = runner or _default_runner
    _restore_snapshot(application.before, environment, runner)
    _write_state(paths, application.previous)
    _reload(environment, out)


def run_theme_reset(
    paths: Paths,
    *,
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    session: Mapping[str, str] | None = None,
    display: str = ":0",
) -> int:
    """Restore the xfconf values that the last applied theme replaced."""
    state = read_state(paths)
    if state.active is None:
        print("No theme is applied.", file=out)
        return 0
    if state.display is not None and state.display != display:
        raise ThemeError(
            f"the theme was applied on {state.display}; pass --display {state.display} to reset it there"
        )
    environment = dict(session) if session is not None else _session_environment(display)
    runner = runner or _default_runner
    _restore_snapshot(state.snapshot, environment, runner)
    _write_state(
        paths,
        ThemeState(snapshot=(), installed=state.installed, hashes=state.hashes, display=state.display),
    )
    _reload(environment, out)
    print(f"reset the theme {state.active}", file=out)
    return 0
