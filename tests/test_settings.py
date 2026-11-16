"""Tests for the YAML configuration file and its built-in defaults."""

from __future__ import annotations

import io
import os
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
    list_profiles,
    load_settings,
    profiles_directory,
    require_profile,
    run_config_check,
    run_config_init,
    run_config_path,
    run_config_set_app,
    run_config_set_section,
    run_config_show,
    run_config_unset_app,
    run_config_unset_section,
)

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - exercised on a host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")

SAMPLE = """\
# desk configuration; desk only rewrites it on explicit config set/unset
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
            "termux_desk.settings.model.default_config_path",
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
        self.assertIn("desk never rewrites the file on its own.", text)
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
        self.assertNotIn("desk never rewrites the file on its own.", self.path.read_text(encoding="utf-8"))

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
            "desk never rewrites the file on its own.", self.path.read_text(encoding="utf-8")
        )

    def test_a_missing_built_in_file_is_an_error(self):
        with mock.patch(
            "termux_desk.settings.model.default_config_path",
            return_value=self.workspace.home / "missing.yaml",
        ):
            with self.assertRaises(SettingsError):
                run_config_init(
                    self.workspace.paths,
                    environ=self.workspace.environ,
                    out=io.StringIO(),
                )


@needs_yaml
class SetAppTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def set(self, **kwargs):
        run_config_set_app(
            self.workspace.paths,
            environ=self.workspace.environ,
            out=io.StringIO(),
            **kwargs,
        )

    def test_set_program_writes_the_file_and_keeps_the_header(self):
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("desk never rewrites the file on its own.", text)
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["ubuntu/xterm"].program, "/usr/bin/foo")

    def test_set_args_prefix_and_environment(self):
        self.set(
            spec="ubuntu/xterm",
            field_name="args-prefix",
            arguments=("--no-sandbox", "--flag=1"),
        )
        self.set(
            spec="ubuntu/xterm",
            field_name="environment",
            environment_name="GTK_IM_MODULE",
            value="xim",
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        entry = settings.apps["ubuntu/xterm"]
        self.assertEqual(entry.args_prefix, ("--no-sandbox", "--flag=1"))
        self.assertEqual(entry.environment, (("GTK_IM_MODULE", "xim"),))

    def test_a_leading_double_dash_is_dropped_from_the_prefix(self):
        self.set(
            spec="ubuntu/xterm",
            field_name="args-prefix",
            arguments=("--", "--flag"),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["ubuntu/xterm"].args_prefix, ("--flag",))

    def test_existing_content_outside_apps_is_kept_and_backed_up(self):
        self.path.parent.mkdir(parents=True)
        original = "# my note\nversion: 1\n\napps: {}\n# trailing note\n"
        self.path.write_text(original, encoding="utf-8")
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# my note", text)
        self.assertIn("# trailing note", text)
        backup = self.path.with_name(self.path.name + ".bak")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)

    def test_an_invalid_spec_is_rejected(self):
        with self.assertRaises(SettingsError):
            self.set(spec="no-slash", field_name="program", value="/usr/bin/foo")

    def test_an_empty_program_is_rejected(self):
        with self.assertRaises(SettingsError):
            self.set(spec="ubuntu/xterm", field_name="program", value="")

    def test_a_whitespace_program_is_rejected(self):
        with self.assertRaises(SettingsError):
            self.set(spec="ubuntu/xterm", field_name="program", value="   ")

    def test_an_environment_name_with_an_equals_sign_is_rejected(self):
        with self.assertRaises(SettingsError):
            self.set(
                spec="ubuntu/xterm",
                field_name="environment",
                environment_name="A=B",
                value="v",
            )
        self.assertFalse(self.path.exists())

    def test_quoted_apps_key_is_replaced_once(self):
        self.path.parent.mkdir(parents=True)
        original = '# note\n"version": 1\n"apps": {}\n# trailing note\n'
        self.path.write_text(original, encoding="utf-8")
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("apps:"), 1)
        self.assertIn("# trailing note", text)
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["ubuntu/xterm"].program, "/usr/bin/foo")

    def test_a_failed_set_leaves_the_file_untouched(self):
        self.path.parent.mkdir(parents=True)
        original = "version: 1\n? [a]\n: 1\n"
        self.path.write_text(original, encoding="utf-8")
        with self.assertRaises(SettingsError):
            self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_merge_keys_have_a_clear_error(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            "version: 1\napps:\n  ubuntu/xterm:\n    <<: {program: /bin/echo}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SettingsError, "merge keys are not supported"):
            load_settings(self.workspace.paths, environ=self.workspace.environ)


class ValidationEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)
        self.path.parent.mkdir(parents=True)

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")

    @needs_yaml
    def test_version_one_point_zero_is_rejected(self):
        self.write("version: 1.0\napps: {}\n")
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_a_non_scalar_key_is_a_settings_error(self):
        self.write("version: 1\napps:\n  ? [ubuntu/xterm]\n  : {}\n")
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_a_non_string_top_level_key_is_rejected(self):
        self.write("version: 1\napps: {}\n1: x\n")
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_a_null_apps_mapping_means_no_overrides(self):
        self.write("version: 1\napps:\n")
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(dict(settings.apps), {})


@needs_yaml
class UnsetAppTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def set(self, **kwargs):
        run_config_set_app(
            self.workspace.paths,
            environ=self.workspace.environ,
            out=io.StringIO(),
            **kwargs,
        )

    def unset(self, **kwargs):
        output = io.StringIO()
        run_config_unset_app(
            self.workspace.paths,
            environ=self.workspace.environ,
            out=output,
            **kwargs,
        )
        return output.getvalue()

    def test_unset_program_and_environment(self):
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        self.set(
            spec="ubuntu/xterm",
            field_name="environment",
            environment_name="GTK_IM_MODULE",
            value="xim",
        )
        self.unset(
            spec="ubuntu/xterm",
            field_name="environment",
            environment_name="GTK_IM_MODULE",
        )
        self.unset(spec="ubuntu/xterm", field_name="program")
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertNotIn("ubuntu/xterm", settings.apps)
        self.assertIn("apps: {}", self.path.read_text(encoding="utf-8"))

    def test_unset_args_prefix(self):
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        self.set(
            spec="ubuntu/xterm",
            field_name="args-prefix",
            arguments=("--flag",),
        )
        self.unset(spec="ubuntu/xterm", field_name="args-prefix")
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["ubuntu/xterm"].args_prefix, ())

    def test_unset_an_absent_value_reports_nothing_changed(self):
        self.set(spec="ubuntu/xterm", field_name="program", value="/usr/bin/foo")
        before = self.path.read_text(encoding="utf-8")
        message = self.unset(spec="ubuntu/xterm", field_name="args-prefix")
        self.assertIn("Nothing changed", message)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)



class HardeningTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def test_a_relative_xdg_config_home_is_rejected(self):
        with self.assertRaises(SettingsError):
            load_settings(
                self.workspace.paths,
                environ={"XDG_CONFIG_HOME": "relative/path"},
            )

    @needs_yaml
    def test_an_oversized_file_is_rejected(self):
        self.write("version: 1\napps: {}\n# " + "x" * (70 * 1024) + "\n")
        with self.assertRaisesRegex(SettingsError, "too large"):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_deeply_nested_yaml_is_rejected(self):
        self.write("[" * 3000 + "]" * 3000)
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    @needs_yaml
    def test_show_escapes_control_characters(self):
        self.write(
            'version: 1\napps:\n  ubuntu/xterm:\n    environment:\n      X: "a\\nb"\n'
        )
        output = io.StringIO()
        run_config_show(
            self.workspace.paths, environ=self.workspace.environ, out=output
        )
        self.assertIn("X=a\\nb", output.getvalue())

    def test_a_symlinked_config_file_is_not_replaced(self):
        target = self.workspace.home / "real.yaml"
        target.write_text("version: 1\napps: {}\n", encoding="utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.symlink_to(target)
        with self.assertRaises(SettingsError):
            run_config_set_app(
                self.workspace.paths,
                environ=self.workspace.environ,
                out=io.StringIO(),
                spec="ubuntu/xterm",
                field_name="program",
                value="/usr/bin/foo",
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "version: 1\napps: {}\n")

    def test_a_symlinked_backup_file_is_not_followed(self):
        self.write("version: 1\napps: {}\n")
        victim = self.workspace.home / "victim"
        victim.write_text("do not touch\n", encoding="utf-8")
        backup = self.path.with_name(self.path.name + ".bak")
        backup.symlink_to(victim)
        with self.assertRaises(SettingsError):
            run_config_set_app(
                self.workspace.paths,
                environ=self.workspace.environ,
                out=io.StringIO(),
                spec="ubuntu/xterm",
                field_name="program",
                value="/usr/bin/foo",
            )
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")

    def test_program_rejects_control_characters(self):
        with self.assertRaises(SettingsError):
            run_config_set_app(
                self.workspace.paths,
                environ=self.workspace.environ,
                out=io.StringIO(),
                spec="ubuntu/xterm",
                field_name="program",
                value="/usr/bin/foo\nbar",
            )


class ReviewRegressionTest(unittest.TestCase):
    """Regression tests for the configuration review findings."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def write(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def write_bytes(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)

    def set_app(self, **kwargs):
        return run_config_set_app(
            self.workspace.paths,
            environ=self.workspace.environ,
            out=io.StringIO(),
            **kwargs,
        )

    def test_a_second_identical_set_changes_nothing(self):
        self.write("version: 1\napps: {}\n# trailing note\n")
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        before = self.path.read_bytes()
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn(b"# trailing note", before)

    def test_a_block_mapping_keeps_trailing_comments(self):
        self.write(
            "version: 1\n"
            "apps:\n"
            "  u/x:\n"
            "    program: /bin/true\n"
            "# note\n"
            "# second\n"
        )
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# note\n# second", text)
        self.assertEqual(text.count("apps:"), 1)

    def test_a_comment_only_file_keeps_its_comments(self):
        self.write("# keep me\n# and me\n")
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# keep me", text)
        self.assertIn("# and me", text)

    def test_a_non_utf8_file_is_rejected(self):
        self.write_bytes(b"version: 1\n# caf\xe9\n")
        with self.assertRaisesRegex(SettingsError, "not UTF-8"):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    def test_an_oversized_result_is_refused_before_it_is_written(self):
        with self.assertRaisesRegex(SettingsError, "limit is 65536 bytes"):
            self.set_app(
                spec="c/a",
                field_name="environment",
                environment_name="BIG",
                value="x" * 70000,
            )
        self.assertFalse(self.path.exists())

    def test_a_directory_is_not_a_configuration_file(self):
        self.path.mkdir(parents=True)
        with self.assertRaisesRegex(SettingsError, "not a regular file"):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    def test_crlf_is_preserved(self):
        self.write_bytes(b"version: 1\r\napps: {}\r\n# note\r\n")
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        data = self.path.read_bytes()
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_control_characters_in_a_spec_are_rejected(self):
        with self.assertRaisesRegex(SettingsError, "control characters"):
            self.set_app(
                spec="c/a\x1b]52;c;ZXZpbA==\x07",
                field_name="program",
                value="/bin/echo",
            )

    def test_file_environment_names_must_be_safe(self):
        self.write(
            "version: 1\n"
            "apps:\n"
            "  c/a:\n"
            "    environment:\n"
            '      "--bind": "/:/"\n'
        )
        with self.assertRaises(SettingsError):
            load_settings(self.workspace.paths, environ=self.workspace.environ)

    def test_a_value_with_nel_round_trips(self):
        self.set_app(
            spec="c/a",
            field_name="environment",
            environment_name="FOO",
            value="a\x85b",
        )
        self.assertIn("\\u0085", self.path.read_text(encoding="utf-8"))
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["c/a"].environment, (("FOO", "a\x85b"),))
        output = io.StringIO()
        run_config_show(self.workspace.paths, environ=self.workspace.environ, out=output)
        self.assertIn("FOO=a\\x85b", output.getvalue())

    def test_surrogate_values_are_rejected(self):
        with self.assertRaisesRegex(SettingsError, "invalid Unicode"):
            self.set_app(
                spec="c/a",
                field_name="environment",
                environment_name="FOO",
                value="a\udc85b",
            )
        with self.assertRaisesRegex(SettingsError, "valid Unicode"):
            self.set_app(
                spec="c/a",
                field_name="args-prefix",
                arguments=("a\udc85b",),
            )

    def test_unset_without_an_override_changes_nothing(self):
        self.write("version: 1\napps:\n  c/a:\n    environment:\n      FOO: bar\n")
        before = self.path.read_bytes()
        run_config_unset_app(
            self.workspace.paths,
            environ=self.workspace.environ,
            out=io.StringIO(),
            spec="c/a",
            field_name="program",
        )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + ".bak").exists())

    def test_stale_temporaries_are_removed(self):
        self.write("version: 1\napps: {}\n")
        stale = self.path.parent / ".config.yaml.dead.tmp"
        stale.write_text("junk", encoding="utf-8")
        self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        self.assertFalse(stale.exists())

    def test_a_hardlinked_backup_is_refused(self):
        self.write("version: 1\napps: {}\n")
        victim = self.workspace.home / "victim"
        victim.write_text("do not touch\n", encoding="utf-8")
        backup = self.path.with_name(self.path.name + ".bak")
        os.link(victim, backup)
        with self.assertRaises(SettingsError):
            self.set_app(spec="c/a", field_name="program", value="/bin/echo")
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")

    def test_show_uses_one_line_for_environment(self):
        self.set_app(
            spec="c/a",
            field_name="environment",
            environment_name="FOO",
            value="1",
        )
        self.set_app(
            spec="c/a",
            field_name="environment",
            environment_name="BAR",
            value="2",
        )
        output = io.StringIO()
        run_config_show(self.workspace.paths, environ=self.workspace.environ, out=output)
        text = output.getvalue()
        self.assertIn("environment:", text)
        self.assertIn("FOO=1", text)
        self.assertIn("BAR=2", text)
        self.assertEqual(text.count("environment:"), 1)

    def test_an_xdg_path_with_a_control_character_is_rejected(self):
        with self.assertRaises(SettingsError):
            config_path(
                self.workspace.paths,
                {"XDG_CONFIG_HOME": "/tmp/x\x1b"},
            )
        with self.assertRaises(SettingsError):
            config_path(
                self.workspace.paths,
                {"XDG_CONFIG_HOME": "/tmp/x\x00y"},
            )

    def test_args_prefix_keeps_arguments_after_the_first_double_dash(self):
        self.set_app(
            spec="c/a",
            field_name="args-prefix",
            arguments=("--", "a", "--", "b"),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.apps["c/a"].args_prefix, ("a", "--", "b"))


class SectionsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.path = config_path(self.workspace.paths, self.workspace.environ)

    def load(self, text):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")
        return load_settings(self.workspace.paths, environ=self.workspace.environ)

    def test_sections_default_when_missing(self):
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.vnc.display, ":1")
        self.assertEqual(settings.vnc.geometry, "1280x720")
        self.assertEqual(settings.vnc.proxy, "rs")
        self.assertEqual(settings.vnc.view_only, False)
        self.assertEqual(settings.ime.enabled, "auto")
        self.assertEqual(settings.fonts.language, "auto")

    def test_sections_are_parsed(self):
        settings = self.load(
            """version: 1
vnc:
  display: ":2"
  geometry: "1024x600"
  depth: 16
  host: "0.0.0.0"
  port: 6090
  proxy: py
  view_only: true
ime:
  enabled: false
fonts:
  language: zh-Hant
  sans_serif: "Noto Sans CJK TC"
  monospace: "Sarasa Mono TC"
"""
        )
        self.assertEqual(settings.vnc.display, ":2")
        self.assertEqual(settings.vnc.geometry, "1024x600")
        self.assertEqual(settings.vnc.depth, 16)
        self.assertEqual(settings.vnc.host, "0.0.0.0")
        self.assertEqual(settings.vnc.port, 6090)
        self.assertEqual(settings.vnc.proxy, "py")
        self.assertIs(settings.vnc.view_only, True)
        self.assertEqual(settings.ime.enabled, "false")
        self.assertEqual(settings.fonts.language, "zh-Hant")
        self.assertEqual(settings.fonts.sans_serif, "Noto Sans CJK TC")
        self.assertIsNone(settings.fonts.serif)
        self.assertEqual(settings.fonts.monospace, "Sarasa Mono TC")

    def test_ime_accepts_yaml_booleans(self):
        settings = self.load("version: 1\nime:\n  enabled: true\n")
        self.assertEqual(settings.ime.enabled, "true")

    def test_invalid_section_values_are_rejected(self):
        cases = [
            "vnc:\n  display: 1\n",
            "vnc:\n  geometry: 1280*720\n",
            "vnc:\n  depth: 20\n",
            "vnc:\n  host: 1\n",
            "vnc:\n  port: 70000\n",
            "vnc:\n  proxy: nginx\n",
            "vnc:\n  view_only: maybe\n",
            "vnc:\n  scale: 2\n",
            "ime:\n  enabled: sometimes\n",
            "fonts:\n  language: fr\n",
            "fonts:\n  sans_serif: 3\n",
            "fonts:\n  serif: \"\"\n",
        ]
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(SettingsError):
                    self.load("version: 1\n" + body)

    def test_set_and_unset_vnc_defaults(self):
        run_config_init(self.workspace.paths, environ=self.workspace.environ, out=io.StringIO())
        run_config_set_section(
            self.workspace.paths,
            section="vnc",
            key="geometry",
            value="1024x600",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.vnc.geometry, "1024x600")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# VNC defaults used when a command omits the flag.", text)
        run_config_unset_section(
            self.workspace.paths,
            section="vnc",
            key="geometry",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.vnc.geometry, "1280x720")

    def test_set_ime_and_fonts(self):
        run_config_init(self.workspace.paths, environ=self.workspace.environ, out=io.StringIO())
        run_config_set_section(
            self.workspace.paths,
            section="ime",
            key="enabled",
            value="true",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        run_config_set_section(
            self.workspace.paths,
            section="fonts",
            key="language",
            value="ja",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        run_config_set_section(
            self.workspace.paths,
            section="fonts",
            key="serif",
            value="Noto Serif CJK JP",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertEqual(settings.ime.enabled, "true")
        self.assertEqual(settings.fonts.language, "ja")
        self.assertEqual(settings.fonts.serif, "Noto Serif CJK JP")
        shown = io.StringIO()
        run_config_show(self.workspace.paths, environ=self.workspace.environ, out=shown)
        output = shown.getvalue()
        self.assertIn("enabled: true", output)
        self.assertIn("language: ja", output)
        run_config_unset_section(
            self.workspace.paths,
            section="fonts",
            key="serif",
            environ=self.workspace.environ,
            out=io.StringIO(),
        )
        settings = load_settings(self.workspace.paths, environ=self.workspace.environ)
        self.assertIsNone(settings.fonts.serif)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("  serif:", text)

    def test_setting_the_same_value_does_not_rewrite(self):
        run_config_init(self.workspace.paths, environ=self.workspace.environ, out=io.StringIO())
        before = self.path.read_bytes()
        out = io.StringIO()
        run_config_set_section(
            self.workspace.paths,
            section="vnc",
            key="display",
            value=":1",
            environ=self.workspace.environ,
            out=out,
        )
        self.assertIn("Nothing changed", out.getvalue())
        self.assertEqual(self.path.read_bytes(), before)


@needs_yaml
class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_default_path_is_the_plain_configuration(self):
        path = config_path(self.workspace.paths, self.workspace.environ)
        self.assertEqual(path.name, "config.yaml")
        self.assertEqual(path.parent.name, "termux-desktop")

    def test_the_profile_environment_selects_a_profile_file(self):
        environ = dict(self.workspace.environ, DESK_PROFILE="work")
        path = config_path(self.workspace.paths, environ)
        self.assertEqual(path.name, "work.yaml")
        self.assertEqual(path.parent.name, "profiles")

    def test_an_explicit_profile_overrides_the_environment(self):
        environ = dict(self.workspace.environ, DESK_PROFILE="work")
        path = config_path(self.workspace.paths, environ, profile="tiling")
        self.assertEqual(path.name, "tiling.yaml")

    def test_an_invalid_profile_name_is_rejected(self):
        environ = dict(self.workspace.environ, DESK_PROFILE="bad name")
        with self.assertRaises(SettingsError):
            config_path(self.workspace.paths, environ)

    def test_require_profile_rejects_a_missing_profile_file(self):
        environ = dict(self.workspace.environ, DESK_PROFILE="work")
        with self.assertRaises(SettingsError) as raised:
            require_profile(self.workspace.paths, environ)
        self.assertIn("has no configuration file", str(raised.exception))
        require_profile(self.workspace.paths, self.workspace.environ)

    def test_a_missing_profile_file_is_an_error(self):
        environ = dict(self.workspace.environ, DESK_PROFILE="work")
        with self.assertRaises(SettingsError) as raised:
            load_settings(self.workspace.paths, environ=environ)
        self.assertIn("has no configuration file", str(raised.exception))
        self.assertIn("config init", str(raised.exception))

    def test_profiles_are_listed_and_parsed(self):
        directory = profiles_directory(self.workspace.paths, self.workspace.environ)
        directory.mkdir(parents=True)
        (directory / "work.yaml").write_text("version: 1\n", encoding="utf-8")
        (directory / "tiling.yaml").write_text("version: 1\n", encoding="utf-8")
        (directory / "notes.txt").write_text("", encoding="utf-8")
        self.assertEqual(
            list_profiles(self.workspace.paths, self.workspace.environ),
            ("tiling", "work"),
        )
        environ = dict(self.workspace.environ, DESK_PROFILE="work")
        settings = load_settings(self.workspace.paths, environ=environ)
        self.assertEqual(settings.version, 1)
