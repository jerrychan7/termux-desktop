"""Tests for the YAML configuration file and its built-in defaults."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace

from termux_desk.settings import (
    AppSettings,
    SettingsError,
    config_path,
    load_settings,
    run_config_check,
    run_config_init,
    run_config_path,
    run_config_show,
)

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - exercised on a host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")

SAMPLE = """\
# desk configuration; comments are kept because desk never writes this file
version: 1

apps:
  "ubuntu-desktop-test/com.microsoft.VSCode":
    program: /usr/local/bin/code
    args_prefix:
      - --no-sandbox
    environment:
      GTK_IM_MODULE: xim
"""


class ConfigPathTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_default_path_is_under_dot_config(self):
        self.assertEqual(
            config_path(self.workspace.paths, self.workspace.environ),
            self.workspace.home / ".config" / "termux-desktop" / "config.yaml",
        )

    def test_xdg_config_home_is_used_when_it_is_set(self):
        environ = dict(self.workspace.environ, XDG_CONFIG_HOME="/tmp/xdg-config")
        self.assertEqual(
            config_path(self.workspace.paths, environ),
            Path("/tmp/xdg-config/termux-desktop/config.yaml"),
        )

    def test_the_path_is_printed(self):
        output = io.StringIO()
        run_config_path(self.workspace.paths, environ=self.workspace.environ, out=output)
        self.assertIn("termux-desktop/config.yaml", output.getvalue())


class LoadSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def write(self, text, path=None):
        target = self.path if path is None else path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def test_a_missing_file_means_defaults(self):
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.version, 1)
        self.assertEqual(settings.apps, {})

    @needs_yaml
    def test_app_overrides_are_parsed(self):
        self.write(SAMPLE)
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.version, 1)
        self.assertEqual(
            settings.apps["ubuntu-desktop-test/com.microsoft.VSCode"],
            AppSettings(
                program="/usr/local/bin/code",
                args_prefix=("--no-sandbox",),
                environment=(("GTK_IM_MODULE", "xim"),),
            ),
        )

    @needs_yaml
    def test_an_empty_file_means_defaults(self):
        self.write("")
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps, {})

    @needs_yaml
    def test_comments_and_quoted_keys_are_accepted(self):
        self.write(
            "# comment\n"
            "version: 1  # trailing comment\n"
            "apps:\n"
            '  "ubuntu/xterm":\n'
            "    args_prefix: ['-fa', 'Noto Sans CJK SC']\n"
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["ubuntu/xterm"].args_prefix, ("-fa", "Noto Sans CJK SC"))

    @needs_yaml
    def test_duplicate_keys_are_rejected(self):
        self.write("version: 1\nversion: 1\n")
        with self.assertRaises(SettingsError) as context:
            load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertIn("duplicate key", str(context.exception))

    @needs_yaml
    def test_unknown_top_level_keys_are_rejected(self):
        self.write("version: 1\ncolors: {}\n")
        with self.assertRaises(SettingsError) as context:
            load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertIn("unknown key", str(context.exception))

    @needs_yaml
    def test_the_version_must_match(self):
        for text in ("version: 2\n", "version: true\n", "version: one\n"):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(SettingsError):
                    load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_apps_must_be_a_mapping(self):
        self.write("version: 1\napps: []\n")
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_app_keys_must_be_container_app_pairs(self):
        for spec in ("xterm", "/xterm", "ubuntu/", "--opt/xterm", "ubuntu/-xterm"):
            with self.subTest(spec=spec):
                self.write(f'version: 1\napps:\n  "{spec}": {{}}\n')
                with self.assertRaises(SettingsError):
                    load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_unknown_app_keys_are_rejected(self):
        self.write('version: 1\napps:\n  "ubuntu/xterm":\n    progam: /usr/bin/xterm\n')
        with self.assertRaises(SettingsError) as context:
            load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertIn("unknown key", str(context.exception))

    @needs_yaml
    def test_program_must_be_a_usable_string(self):
        for program in ("-xterm", '""'):
            with self.subTest(program=program):
                self.write(f'version: 1\napps:\n  "ubuntu/xterm":\n    program: {program}\n')
                with self.assertRaises(SettingsError):
                    load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_args_prefix_must_be_a_list_of_strings(self):
        for value in ("['']", "[1]", "notalist"):
            with self.subTest(value=value):
                self.write(f'version: 1\napps:\n  "ubuntu/xterm":\n    args_prefix: {value}\n')
                with self.assertRaises(SettingsError):
                    load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_environment_values_must_be_strings(self):
        for value in ("1", "true", "[]"):
            with self.subTest(value=value):
                self.write(
                    f'version: 1\napps:\n  "ubuntu/xterm":\n    environment:\n'
                    f"      GTK_IM_MODULE: {value}\n"
                )
                with self.assertRaises(SettingsError):
                    load_settings(self.workspace.paths, environ=self.workspace.environ)

    def test_missing_pyyaml_gives_a_hint(self):
        self.write("version: 1\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            with self.assertRaises(SettingsError) as context:
                load_settings(self.workspace.paths, environ=self.workspace.environ)
        message = str(context.exception)
        self.assertIn("needs PyYAML", message)
        self.assertIn("desk setup", message)


class BuiltInDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    @needs_yaml
    def test_a_missing_file_uses_the_built_in_defaults(self):
        loaded = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(loaded.version, 1)
        self.assertEqual(dict(loaded.apps), {})

    @needs_yaml
    def test_a_missing_built_in_file_is_an_error(self):
        with mock.patch(
            "termux_desk.settings.default_config_path",
            return_value=self.workspace.home / "missing.yaml",
        ):
            with self.assertRaises(SettingsError) as raised:
                load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertIn("built-in configuration is missing", str(raised.exception))


class ShowAndCheckTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def test_show_without_a_file_reports_the_defaults(self):
        output = io.StringIO()
        run_config_show(self.workspace.paths, environ=self.workspace.environ, out=output)
        text = output.getvalue()
        self.assertIn("not present", text)
        self.assertIn("built-in defaults", text)
        self.assertIn("version: 1", text)
        self.assertIn("apps: (none)", text)

    @needs_yaml
    def test_show_lists_the_app_overrides(self):
        self.write(SAMPLE)
        output = io.StringIO()
        run_config_show(self.workspace.paths, environ=self.workspace.environ, out=output)
        text = output.getvalue()
        self.assertIn("ubuntu-desktop-test/com.microsoft.VSCode", text)
        self.assertIn("program: /usr/local/bin/code", text)
        self.assertIn("args_prefix: --no-sandbox", text)
        self.assertIn("environment: GTK_IM_MODULE=xim", text)

    def test_check_without_a_file_exits_zero(self):
        output = io.StringIO()
        self.assertEqual(
            run_config_check(self.workspace.paths, environ=self.workspace.environ, out=output),
            0,
        )
        self.assertIn("built-in defaults are valid", output.getvalue())

    @needs_yaml
    def test_check_reports_a_valid_file(self):
        self.write(SAMPLE)
        output = io.StringIO()
        self.assertEqual(
            run_config_check(self.workspace.paths, environ=self.workspace.environ, out=output),
            0,
        )
        self.assertIn("is valid", output.getvalue())

    @needs_yaml
    def test_check_raises_for_an_invalid_file(self):
        self.write("version: 1\napps: []\n")
        output = io.StringIO()
        with self.assertRaises(SettingsError):
            run_config_check(self.workspace.paths, environ=self.workspace.environ, out=output)


class SettingsForTest(unittest.TestCase):
    def test_lookup_returns_none_for_an_unknown_app(self):
        from termux_desk.settings import Settings, setting_for

        self.assertIsNone(setting_for(Settings(), "ubuntu/xterm"))
        entry = AppSettings(program="/usr/local/bin/code")
        settings = Settings(apps={"ubuntu/xterm": entry})
        self.assertEqual(setting_for(settings, "ubuntu/xterm"), entry)


class ConfigInitTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def test_init_writes_the_built_in_defaults_with_comments(self):
        output = io.StringIO()
        run_config_init(self.workspace.paths, environ=self.workspace.environ, out=output)
        self.assertTrue(self.path.is_file())
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# termux-desktop configuration.", text)
        self.assertIn("version: 1", text)
        self.assertIn("apps: {}", text)
        self.assertIn("Wrote", output.getvalue())

    def test_init_creates_the_parent_directory(self):
        run_config_init(
            self.workspace.paths, environ=self.workspace.environ, out=io.StringIO()
        )
        self.assertTrue(self.path.parent.is_dir())

    def test_an_existing_file_needs_force(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("version: 1\napps: {}\n", encoding="utf-8")
        with self.assertRaises(SettingsError) as raised:
            run_config_init(
                self.workspace.paths, environ=self.workspace.environ, out=io.StringIO()
            )
        self.assertIn("already exists", str(raised.exception))
        self.assertNotIn("# termux-desktop configuration.", self.path.read_text(encoding="utf-8"))

    def test_force_replaces_an_existing_file(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("version: 1\n", encoding="utf-8")
        run_config_init(
            self.workspace.paths,
            environ=self.workspace.environ,
            force=True,
            out=io.StringIO(),
        )
        self.assertIn(
            "# termux-desktop configuration.", self.path.read_text(encoding="utf-8")
        )

    def test_a_missing_built_in_file_is_an_error(self):
        with mock.patch(
            "termux_desk.settings.default_config_path",
            return_value=self.workspace.home / "missing.yaml",
        ):
            with self.assertRaises(SettingsError):
                run_config_init(
                    self.workspace.paths,
                    environ=self.workspace.environ,
                    out=io.StringIO(),
                )
