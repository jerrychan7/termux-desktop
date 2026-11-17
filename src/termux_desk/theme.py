"""Named XFCE appearance sets (themes) and the commands that apply them.

The model lives in theme_model, asset downloads in theme_assets and the
xfconf writer in theme_session; this module keeps the apply/reset flow and
re-exports the names the rest of desk uses.
"""

from __future__ import annotations

from typing import Mapping, TextIO

from . import fonts as font_tools
from . import settings
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
from .theme_session import _default_runner as _default_runner
from .theme_session import _font_matcher as _font_matcher
from .theme_session import _reload as _reload
from .theme_session import _resolve_components as _resolve_components
from .theme_session import _restore_snapshot as _restore_snapshot
from .theme_session import _session_environment as _session_environment
from .theme_session import _snapshot as _snapshot
from .theme_session import _write_components as _write_components
from .theme_session import _write_fonts as _write_fonts


def run_theme_apply(
    paths: Paths,
    name: str | None,
    *,
    allow_unverified: bool = False,
    force: bool = False,
    display: str = ":0",
    out: TextIO,
    environ: Mapping[str, str] | None = None,
    fetch: Fetcher | None = None,
    runner: Runner | None = None,
    matcher: font_tools.FamilyMatcher | None = None,
    session: Mapping[str, str] | None = None,
) -> int:
    """Install any missing assets, apply a theme and remember the old values."""
    if name is None:
        selected = settings.load_settings(paths, allow_missing_pyyaml=True).theme
        if not selected:
            raise ThemeError(
                "no theme is selected; pass a name or set theme in the configuration "
                "(desk config set theme NAME)"
            )
        name = selected
    theme = load_theme(paths, name, environ)
    environment = dict(session) if session is not None else _session_environment(display)
    state = read_state(paths)
    fetch = fetch or _default_fetcher
    runner = runner or _default_runner
    matcher = _font_matcher(paths, matcher)
    state, installed = _install_assets(
        paths, theme, allow_unverified=allow_unverified, force=force, state=state, fetch=fetch, out=out
    )
    components = _resolve_components(theme, installed, paths, matcher, out)
    snapshot = _snapshot(environment, runner)
    _write_components(components, environment, runner)
    _write_fonts(theme, paths, environment, runner, matcher)
    _write_state(
        paths,
        ThemeState(active=theme.name, snapshot=snapshot, installed=state.installed, hashes=state.hashes),
    )
    _reload(environment, out)
    print(f"applied theme {theme.name}", file=out)
    return 0


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
    environment = dict(session) if session is not None else _session_environment(display)
    runner = runner or _default_runner
    _restore_snapshot(state.snapshot, environment, runner)
    _write_state(paths, ThemeState(snapshot=(), installed=state.installed, hashes=state.hashes))
    _reload(environment, out)
    print(f"reset the theme {state.active}", file=out)
    return 0
