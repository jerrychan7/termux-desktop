"""YAML configuration for desk.

The user file belongs to the user: desk reads it, and the ``config`` commands
that write it only touch the ``apps`` section. When the file does not exist,
desk reads the commented built-in copy (``default_config.yaml``) instead; the
defaults therefore live in one YAML document rather than in Python code.
PyYAML is a normal dependency of the configuration layer and is installed by
``desk setup``.
"""

from __future__ import annotations

from .commands import (
    run_config_check,
    run_config_init,
    run_config_path,
    run_config_profiles,
    run_config_set_app,
    run_config_set_name,
    run_config_set_section,
    run_config_show,
    run_config_unset_app,
    run_config_unset_name,
    run_config_unset_section,
)
from .loading import load_settings
from .model import (
    CONFIG_DIRECTORY_NAME,
    CONFIG_FILE_NAME,
    CONFIG_VERSION,
    DEFAULT_CONFIG_FILE_NAME,
    DEFAULT_PROFILE,
    MAX_CONFIG_BYTES,
    PROFILE_ENVIRONMENT,
    PROFILES_DIRECTORY_NAME,
    AppSettings,
    Settings,
    SettingsError,
    VncSettings,
    config_directory,
    config_path,
    default_config_path,
    list_profiles,
    profile_name,
    profiles_directory,
    require_profile,
    setting_for,
)

__all__ = [
    "CONFIG_DIRECTORY_NAME",
    "CONFIG_FILE_NAME",
    "CONFIG_VERSION",
    "DEFAULT_CONFIG_FILE_NAME",
    "DEFAULT_PROFILE",
    "MAX_CONFIG_BYTES",
    "PROFILE_ENVIRONMENT",
    "PROFILES_DIRECTORY_NAME",
    "AppSettings",
    "Settings",
    "SettingsError",
    "VncSettings",
    "config_directory",
    "config_path",
    "list_profiles",
    "profile_name",
    "profiles_directory",
    "default_config_path",
    "require_profile",
    "load_settings",
    "run_config_check",
    "run_config_init",
    "run_config_profiles",
    "run_config_path",
    "run_config_set_app",
    "run_config_set_name",
    "run_config_set_section",
    "run_config_show",
    "run_config_unset_app",
    "run_config_unset_name",
    "run_config_unset_section",
    "setting_for",
]
