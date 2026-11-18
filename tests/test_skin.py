"""Tests for the desk skin manifests and the skin actions."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace, make_result

from termux_desk import skin, skin_model, theme
from termux_desk.config import DeskError
from termux_desk.layout import LayoutError
from termux_desk.settings import model as settings_model
from termux_desk.skin_model import SkinError

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - exercised on a host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")


class FakeMatcher:
    def family_exists(self, name):
        return True


class SkinCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        patched = mock.patch.dict(os.environ, self.workspace.environ, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def write_skin(self, name: str, text: str) -> None:
        directory = skin_model.skins_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.yaml").write_text(text, encoding="utf-8")


class ManifestTest(SkinCase):
    def test_a_skin_may_name_presets_for_both_halves(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\nlayout: compact\n")
        loaded = skin_model.load_skin(self.paths, "mine")
        self.assertEqual(loaded.theme.name, "stock")
        self.assertEqual(len(loaded.layout.settings), 3)

    def test_a_skin_may_write_both_halves_inline(self):
        self.write_skin(
            "mine",
            "skin: mine\n"
            "appearance:\n"
            "  gtk_theme: Adwaita\n"
            "  window_border: Default\n"
            "layout:\n"
            "  xfwm4:\n"
            "    title_alignment: left\n",
        )
        loaded = skin_model.load_skin(self.paths, "mine")
        self.assertEqual(loaded.theme.components["gtk"], "Adwaita")
        self.assertEqual(loaded.layout.settings[0].property, "/general/title_alignment")

    def test_a_skin_may_omit_one_half(self):
        self.write_skin("mine", "skin: mine\nlayout: compact\n")
        loaded = skin_model.load_skin(self.paths, "mine")
        self.assertIsNone(loaded.theme)
        self.assertIsNotNone(loaded.layout)

    def test_the_name_must_match_the_file(self):
        self.write_skin("mine", "skin: other\nlayout: compact\n")
        with self.assertRaisesRegex(skin_model.SkinError, "must match the file name"):
            skin_model.load_skin(self.paths, "mine")

    def test_unknown_appearance_keys_are_rejected(self):
        self.write_skin(
            "mine", "skin: mine\nappearance:\n  gtk: Adwaita\n"
        )
        with self.assertRaisesRegex(skin_model.SkinError, "unknown appearance keys"):
            skin_model.load_skin(self.paths, "mine")

    def test_an_unknown_layout_setting_is_rejected(self):
        self.write_skin(
            "mine",
            "skin: mine\nlayout:\n  xfwm4:\n    workspace_names: nope\n",
        )
        with self.assertRaisesRegex(skin_model.SkinError, "unknown"):
            skin_model.load_skin(self.paths, "mine")

    def test_a_missing_appearance_preset_is_reported(self):
        self.write_skin("mine", "skin: mine\nappearance: nope\n")
        with self.assertRaisesRegex(skin_model.SkinError, "there is no appearance preset"):
            skin_model.load_skin(self.paths, "mine")

    def test_check_prints_what_the_skin_would_change(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\nlayout: compact\n")
        out = io.StringIO()
        code = skin_model.run_skin_check(self.paths, "mine", out=out)
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("gtk_theme = Adwaita", text)
        self.assertIn("xfwm4/general/button_layout = O|SHMC", text)
        self.assertIn("skin mine is valid.", text)

    def test_init_writes_a_template_that_loads(self):
        out = io.StringIO()
        self.assertEqual(skin_model.run_skin_init(self.paths, "mine", out=out), 0)
        loaded = skin_model.load_skin(self.paths, "mine")
        self.assertIsNotNone(loaded.theme)
        self.assertIsNotNone(loaded.layout)

    def test_init_refuses_to_overwrite_without_force(self):
        skin_model.run_skin_init(self.paths, "mine", out=io.StringIO())
        with self.assertRaisesRegex(skin_model.SkinError, "already exists"):
            skin_model.run_skin_init(self.paths, "mine", out=io.StringIO())

    def test_presets_are_listed(self):
        out = io.StringIO()
        skin_model.run_appearance_list(self.paths, out=out)
        self.assertIn("stock (preset)", out.getvalue())
        out = io.StringIO()
        skin_model.run_layout_preset_list(self.paths, out=out)
        self.assertIn("compact (preset)", out.getvalue())


class ApplyTest(SkinCase):
    def test_the_appearance_is_applied_before_the_layout(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\nlayout: compact\n")
        order = []
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        with mock.patch.object(
            skin.theme_tools,
            "apply_appearance",
            side_effect=lambda *a, **k: order.append("appearance"),
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=lambda *a, **k: order.append("layout")
        ):
            out = io.StringIO()
            code = skin.run_skin_apply(self.paths, "mine", out=out, session=session)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["appearance", "layout"])
        self.assertIn("applied skin mine", out.getvalue())

    def test_a_failed_layout_rolls_the_appearance_back(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\nlayout: compact\n")
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        application = object()
        thread = {"rolled": False}

        def rollback(paths, record, **kwargs):
            thread["rolled"] = record is application

        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=application
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=LayoutError("boom")
        ), mock.patch.object(skin.theme_tools, "rollback_appearance", side_effect=rollback):
            with self.assertRaises(LayoutError):
                skin.run_skin_apply(self.paths, "mine", out=io.StringIO(), session=session)
        self.assertTrue(thread["rolled"])

    def test_a_failed_rollback_is_reported(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\nlayout: compact\n")
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=object()
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=LayoutError("boom")
        ), mock.patch.object(
            skin.theme_tools, "rollback_appearance", side_effect=DeskError("nope")
        ):
            with self.assertRaisesRegex(
                skin_model.SkinError, r"the layout failed \(boom\).*could not be"
            ):
                skin.run_skin_apply(self.paths, "mine", out=io.StringIO(), session=session)

    def test_reset_restores_the_layout_before_the_appearance(self):
        order = []
        with mock.patch.object(
            skin.layout_tools, "run_layout_reset", side_effect=lambda *a, **k: order.append("layout")
        ), mock.patch.object(
            skin.theme_tools, "run_theme_reset", side_effect=lambda *a, **k: order.append("appearance")
        ):
            self.assertEqual(skin.run_skin_reset(self.paths, out=io.StringIO()), 0)
        self.assertEqual(order, ["layout", "appearance"])

    def test_reset_reports_every_failure(self):
        with mock.patch.object(
            skin.layout_tools, "run_layout_reset", side_effect=DeskError("layout broke")
        ), mock.patch.object(
            skin.theme_tools, "run_theme_reset", side_effect=DeskError("appearance broke")
        ):
            with self.assertRaisesRegex(skin_model.SkinError, "layout broke; appearance broke"):
                skin.run_skin_reset(self.paths, out=io.StringIO())

    def test_the_selected_skin_is_used_when_no_name_is_given(self):
        self.write_skin("mine", "skin: mine\nappearance: stock\n")
        config = skin_model.skins_directory(self.paths).parent / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("version: 1\nskin: mine\n", encoding="utf-8")
        selected = skin.resolve_skin(self.paths, None)
        self.assertEqual(selected.name, "mine")

    def test_no_selected_skin_is_a_readable_error(self):
        with self.assertRaisesRegex(skin_model.SkinError, "no skin is selected"):
            skin.resolve_skin(self.paths, None)

    def test_the_appearance_half_can_be_applied_alone(self):
        with mock.patch.object(skin.theme_tools, "apply_theme", return_value=0) as apply:
            code = skin.run_appearance_apply(self.paths, "stock", out=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(apply.call_count, 1)

    def test_the_layout_half_can_be_applied_alone(self):
        with mock.patch.object(skin.layout_tools, "apply_layout", return_value=0) as apply:
            code = skin.run_layout_apply(self.paths, "compact", out=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(apply.call_count, 1)

class RollbackStateTest(SkinCase):
    """The regression behind "re-apply a skin, then let its layout fail"."""

    class _Runner:
        def __init__(self):
            self.values = {"xsettings/Net/ThemeName": "Original"}
            self.failing = ("xfwm4", "/general/title_alignment", "center")

        def __call__(self, argv, env):
            command = tuple(str(item) for item in argv)
            if command[0] in ("xfsettingsd", "xfwm4"):
                return make_result(command)
            if "-l" in command:
                return make_result(command, stdout="")
            if "-s" in command:
                channel = command[command.index("-c") + 1]
                name = command[command.index("-p") + 1]
                value = command[command.index("-s") + 1]
                if (channel, name, value) == self.failing:
                    return make_result(command, returncode=1)
                self.values[f"{channel}{name}"] = value
                return make_result(command)
            if "-r" in command:
                channel = command[command.index("-c") + 1]
                name = command[command.index("-p") + 1]
                self.values.pop(f"{channel}{name}", None)
                return make_result(command)
            if "-p" in command:
                channel = command[command.index("-c") + 1]
                name = command[command.index("-p") + 1]
                key = f"{channel}{name}"
                if key in self.values:
                    return make_result(command, stdout=self.values[key] + "\n")
                return make_result(command, returncode=1)
            return make_result(command)

    def test_a_failed_layout_restores_the_values_the_previous_skin_left(self):
        self.write_skin("first", "skin: first\nappearance:\n  gtk_theme: Adwaita\n")
        self.write_skin(
            "second",
            "skin: second\nappearance:\n  gtk_theme: Adwaita-dark\n"
            "layout:\n  xfwm4:\n    title_alignment: center\n",
        )
        runner = self._Runner()
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        skin.run_skin_apply(
            self.paths, "first", out=io.StringIO(), runner=runner, session=session,
            matcher=FakeMatcher(),
        )
        self.assertEqual(runner.values["xsettings/Net/ThemeName"], "Adwaita")
        before = theme.read_state(self.paths)
        with mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=LayoutError("the panel refused")
        ):
            with self.assertRaises(LayoutError):
                skin.run_skin_apply(
                    self.paths, "second", out=io.StringIO(), runner=runner,
                    session=session, matcher=FakeMatcher(),
                )
        self.assertEqual(runner.values["xsettings/Net/ThemeName"], "Adwaita")
        after = theme.read_state(self.paths)
        self.assertEqual(after.active, "first")
        self.assertEqual(after.snapshot, before.snapshot)
        theme.run_theme_reset(self.paths, out=io.StringIO(), runner=runner, session=session)
        self.assertEqual(runner.values["xsettings/Net/ThemeName"], "Original")


@needs_yaml
class LegacySelectionTest(SkinCase):
    def test_a_configuration_that_still_selects_a_theme_explains_the_migration(self):
        directory = settings_model.config_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.yaml").write_text(
            "version: 1\ntheme: stock\nlayout: compact\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(SkinError, "still selects a theme or a layout"):
            skin.resolve_skin(self.paths, None)
