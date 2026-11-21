"""Tests for the settings baseline that skins restore when they change."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import Workspace, make_result

from termux_desk import skin_baseline
from termux_desk.layout import Layout, _Setting
from termux_desk.theme_model import Theme


class FakeSession:
    """A tiny xfconf replacement: one dict of channel/property values."""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    def __call__(self, command, env):
        self.calls.append(tuple(command))
        args = list(command[1:])
        channel = args[args.index("-c") + 1]
        name = args[args.index("-p") + 1]
        key = (channel, name)
        if "-r" in args:
            self.values.pop(key, None)
            return make_result(command)
        if "-l" in args:
            lines = sorted(f"{c}/{n.lstrip('/')}" for c, n in self.values if c == channel)
            return make_result(command, stdout="\n".join(lines) + ("\n" if lines else ""))
        if "-s" in args:
            value = args[args.index("-s") + 1]
            self.values[key] = value
            return make_result(command)
        if key not in self.values:
            return make_result(command, 1, stderr="property not found")
        return make_result(command, stdout=self.values[key] + "\n")


class BaselineCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.out = io.StringIO()

    def theme(self, components=None, fonts=None):
        return Theme(
            name="test",
            path=self.paths.home / "test.yaml",
            description="",
            components=components or {},
            fonts=fonts or {},
            assets=(),
        )

    def layout(self, settings=()):
        return Layout(name="test", description="", settings=settings, source=self.paths.home / "l.yaml")


class KeysTest(BaselineCase):
    def test_import(self):
        self.assertTrue(skin_baseline)


class PrepareTest(BaselineCase):
    def test_first_apply_only_records_the_current_values(self):
        session = FakeSession({("xsettings", "/Net/ThemeName"): "Adwaita"})
        keys = [("xsettings", "/Net/ThemeName")]
        baseline = skin_baseline.prepare(self.paths, ":0", keys, {}, session, self.out)
        self.assertEqual(baseline.values["xsettings"]["/Net/ThemeName"], "Adwaita")
        self.assertEqual(session.values[("xsettings", "/Net/ThemeName")], "Adwaita")

    def test_a_key_the_new_skin_skips_goes_back_to_the_old_value(self):
        session = FakeSession(
            {
                ("xsettings", "/Net/ThemeName"): "Adwaita",
                ("xsettings", "/Gtk/FontName"): "More Perfect DOS VGA 10",
            }
        )
        skin_baseline.record(
            self.paths,
            skin_baseline.Baseline(
                display=":0",
                skin="win95",
                values={"xsettings": {"/Gtk/FontName": "Sans 10"}},
            ),
            "win95",
        )
        session.values[("xsettings", "/Net/ThemeName")] = "Windows-10"
        session.values[("xsettings", "/Gtk/FontName")] = "More Perfect DOS VGA 10"
        skin_baseline.prepare(
            self.paths, ":0", [("xsettings", "/Net/ThemeName")], {}, session, self.out
        )
        self.assertEqual(session.values[("xsettings", "/Gtk/FontName")], "Sans 10")
        self.assertIn("restored xsettings/Gtk/FontName", self.out.getvalue())

    def test_a_setting_that_did_not_exist_is_removed_again(self):
        session = FakeSession()
        skin_baseline.record(
            self.paths,
            skin_baseline.Baseline(
                display=":0", skin="a", values={"xfwm4": {"/general/inactive_opacity": None}}
            ),
            "a",
        )
        session.values[("xfwm4", "/general/inactive_opacity")] = "92"
        skin_baseline.prepare(self.paths, ":0", [], {}, session, self.out)
        self.assertNotIn(("xfwm4", "/general/inactive_opacity"), session.values)

    def test_a_key_is_only_captured_the_first_time_it_is_written(self):
        key = ("xfwm4", "/general/workspace_count")
        session = FakeSession({key: "4"})
        first = skin_baseline.prepare(self.paths, ":0", [key], {}, session, self.out)
        self.assertEqual(first.values["xfwm4"]["/general/workspace_count"], "4")
        skin_baseline.record(self.paths, first, "a")
        session.values[key] = "2"
        second = skin_baseline.prepare(self.paths, ":0", [key], {}, session, self.out)
        self.assertEqual(second.values["xfwm4"]["/general/workspace_count"], "4")

    def test_another_display_starts_from_scratch(self):
        session = FakeSession({("xsettings", "/Net/ThemeName"): "Adwaita"})
        skin_baseline.prepare(self.paths, ":0", [("xsettings", "/Net/ThemeName")], {}, session, self.out)
        baseline = skin_baseline.prepare(self.paths, ":1", [], {}, session, self.out)
        self.assertEqual(baseline.values, {})


class RestoreTest(BaselineCase):
    def test_restore_puts_every_value_back_and_forgets_the_file(self):
        session = FakeSession({("xsettings", "/Net/ThemeName"): "Windows-10"})
        skin_baseline.record(
            self.paths,
            skin_baseline.Baseline(
                display=":0", skin="a", values={"xsettings": {"/Net/ThemeName": "Adwaita"}}
            ),
            "a",
        )
        count = skin_baseline.restore(self.paths, {}, session)
        self.assertEqual(count, 1)
        self.assertEqual(session.values[("xsettings", "/Net/ThemeName")], "Adwaita")
        self.assertFalse(skin_baseline.path_for(self.paths).exists())


class KeysForTest(BaselineCase):
    def test_components_fonts_and_layout_produce_the_keys(self):
        theme = self.theme(
            components={"gtk": "A", "icons": "B", "cursors": "C", "xfwm": "D"},
            fonts={"default": "E", "default_size": "10"},
        )
        setting = _Setting(channel="xfwm4", property="/general/inactive_opacity", value="92")
        keys = skin_baseline.keys_for(theme, self.layout((setting,)), {}, FakeSession())
        self.assertIn(("xsettings", "/Net/ThemeName"), keys)
        self.assertIn(("xsettings", "/Net/IconThemeName"), keys)
        self.assertIn(("xsettings", "/Gtk/CursorThemeName"), keys)
        self.assertIn(("xfwm4", "/general/theme"), keys)
        self.assertIn(("xsettings", "/Gtk/FontName"), keys)
        self.assertIn(("xfwm4", "/general/inactive_opacity"), keys)
        self.assertNotIn(("xsettings", "/Gtk/MonospaceFontName"), keys)


if __name__ == "__main__":
    unittest.main()
