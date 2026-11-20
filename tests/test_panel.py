"""Tests for the whole-panel layout helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from termux_desk import panel

SAMPLE_XML = """<?xml version="1.1" encoding="UTF-8"?>

<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
    <value type="int" value="1"/>
    <property name="dark-mode" type="bool" value="true"/>
    <property name="panel-1" type="empty">
      <property name="position" type="string" value="p=6;x=0;y=0"/>
      <property name="length" type="uint" value="100"/>
      <property name="size" type="uint" value="26"/>
      <property name="plugin-ids" type="array">
        <value type="int" value="1"/>
        <value type="int" value="2"/>
      </property>
    </property>
  </property>
  <property name="plugins" type="empty">
    <property name="plugin-1" type="string" value="whiskermenu"/>
    <property name="plugin-2" type="string" value="tasklist">
      <property name="grouping" type="uint" value="1"/>
    </property>
  </property>
</channel>
"""


SNAPSHOT_VALUES = {
    "/configver": "variant       uint32 2",
    "/panels": "variant       array [\n         variant             int32 1\n      ]",
    "/panels/panel-1/size": "variant       uint32 26",
    "/plugins/plugin-1/type": "variant       string whiskermenu",
}


class FakeRunner:
    def __init__(self, fail_at: int | None = None, values=None) -> None:
        self.calls: list[list[str]] = []
        self.values = dict(SNAPSHOT_VALUES if values is None else values)
        self.fail_at = fail_at
        self.writes = 0

    def __call__(self, command, environ):
        item = list(command)
        self.calls.append(item)
        if item[0] == "xfconf-query" and "-l" in item:
            return mock.Mock(returncode=0, stdout="\n".join(self.values) + "\n", stderr="")
        if item[0] == "dbus-send":
            path = item[-1].removeprefix("string:")
            return mock.Mock(returncode=0, stdout="method return time=0.0\n" + self.values[path] + "\n", stderr="")
        self.writes += 1
        if self.fail_at is not None and self.writes == self.fail_at:
            raise panel.PanelError("injected failure")
        return mock.Mock(returncode=0, stdout="", stderr="")


class ManifestTest(unittest.TestCase):
    def test_one_panel_with_settings_and_items(self):
        panels = panel.parse_panels(
            {
                "panel": {
                    "position": "bottom",
                    "size": 40,
                    "length": 100,
                    "plugins": [
                        {"type": "whiskermenu"},
                        {"type": "separator", "settings": {"expand": True}},
                        {"type": "launcher", "items": ["xfce4-terminal.desktop"]},
                    ],
                }
            }
        )
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].position, "bottom")
        self.assertEqual(panels[0].size, 40)
        self.assertEqual(panels[0].plugins[1].settings, (("expand", "bool", "true"),))
        self.assertEqual(panels[0].plugins[2].items, ("xfce4-terminal.desktop",))

    def test_two_panels(self):
        panels = panel.parse_panels(
            {"panels": [{"plugins": [{"type": "clock"}]}, {"plugins": [{"type": "systray"}]}]}
        )
        self.assertEqual([body.plugins[0].type for body in panels], ["clock", "systray"])

    def test_both_panel_and_panels_are_rejected(self):
        with self.assertRaisesRegex(panel.PanelError, "either panel or panels"):
            panel.parse_panels({"panel": {}, "panels": []})

    def test_unknown_panel_key_is_rejected(self):
        with self.assertRaisesRegex(panel.PanelError, "unknown keys"):
            panel.parse_panels({"panel": {"plugins": [{"type": "clock"}], "floating": True}})

    def test_position_must_be_a_known_edge(self):
        with self.assertRaisesRegex(panel.PanelError, "position must be top, bottom or bottom-center"):
            panel.parse_panels({"panel": {"position": "left", "plugins": [{"type": "clock"}]}})

    def test_size_and_length_are_bounded(self):
        for size in (15, 129):
            with self.assertRaisesRegex(panel.PanelError, "size must be between"):
                panel.parse_panels({"panel": {"size": size, "plugins": [{"type": "clock"}]}})
        for length in (0, 101):
            with self.assertRaisesRegex(panel.PanelError, "length must be"):
                panel.parse_panels({"panel": {"length": length, "plugins": [{"type": "clock"}]}})

    def test_plugins_must_not_be_empty(self):
        with self.assertRaisesRegex(panel.PanelError, "plugins must be a non-empty list"):
            panel.parse_panels({"panel": {"plugins": []}})

    def test_too_many_plugins_are_rejected(self):
        plugins = [{"type": "clock"}] * (panel.PANEL_MAX_PLUGINS + 1)
        with self.assertRaisesRegex(panel.PanelError, "at most"):
            panel.parse_panels({"panel": {"plugins": plugins}})

    def test_unknown_plugin_is_rejected(self):
        with self.assertRaisesRegex(panel.PanelError, "not a known panel plugin"):
            panel.parse_panels({"panel": {"plugins": [{"type": "nope"}]}})

    def test_unknown_plugin_setting_is_rejected(self):
        with self.assertRaisesRegex(panel.PanelError, "unknown clock settings"):
            panel.parse_panels(
                {"panel": {"plugins": [{"type": "clock", "settings": {"format": "%H"}}]}}
            )

    def test_items_are_only_for_launchers(self):
        with self.assertRaisesRegex(panel.PanelError, "does not take items"):
            panel.parse_panels(
                {"panel": {"plugins": [{"type": "clock", "items": ["a.desktop"]}]}}
            )

    def test_items_must_be_desktop_ids(self):
        with self.assertRaisesRegex(panel.PanelError, "list of desktop ids"):
            panel.parse_panels(
                {"panel": {"plugins": [{"type": "launcher", "items": ["../evil"]}]}}
            )


class CommandTest(unittest.TestCase):
    def test_commands_create_arrays_with_force_array(self):
        panels = panel.parse_panels(
            {
                "panel": {
                    "position": "top",
                    "plugins": [
                        {"type": "separator", "settings": {"expand": True}},
                        {"type": "launcher", "items": ["a.desktop", "b.desktop"]},
                    ],
                }
            }
        )
        commands = panel.panel_commands(panels)
        joined = [" ".join(command) for command in commands]
        panels_command = next(item for item in joined if " -p /panels " in f" {item} ")
        self.assertIn(" -a", panels_command)
        self.assertIn("-p /configver", " ".join(joined))
        self.assertIn("-p /panels/panel-1/position -n -t string -s p=6;x=0;y=0", " ".join(joined))
        items_command = next(item for item in joined if "/plugins/plugin-2/items" in item)
        self.assertIn(" -a", items_command)
        ids_command = next(item for item in joined if "/panels/panel-1/plugin-ids" in item)
        self.assertIn(" -a", ids_command)

    def test_snapshot_commands_keep_scalar_and_array_types(self):
        snapshot = panel.snapshot_from_xml(SAMPLE_XML)
        commands = panel.snapshot_commands(snapshot)
        joined = [" ".join(command) for command in commands]
        self.assertTrue(any("/configver" in item and "-t int -s 2" in item for item in joined))
        self.assertTrue(
            any("/panels/panel-1/plugin-ids" in item and " -a" in item for item in joined)
        )
        self.assertTrue(any("/plugins/plugin-1" in item and "-t string -s whiskermenu" in item for item in joined))

    def test_snapshot_round_trips_through_json(self):
        snapshot = panel.snapshot_from_xml(SAMPLE_XML)
        restored = panel.snapshot_from_json(panel.snapshot_to_json(snapshot))
        self.assertEqual(restored.entries, snapshot.entries)


class SnapshotTest(unittest.TestCase):
    def test_empty_kind_is_kept_with_its_children(self):
        snapshot = panel.snapshot_from_xml(SAMPLE_XML)
        paths = [entry.path for entry in snapshot.entries]
        self.assertIn("/panels", paths)
        self.assertIn("/panels/panel-1/plugin-ids", paths)
        empty = next(entry for entry in snapshot.entries if entry.path == "/plugins")
        self.assertEqual(empty.kind, "empty")
        self.assertEqual(empty.values, ())

    def test_missing_config_has_an_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = panel.read_snapshot({"HOME": temporary}, Path(temporary), FakeRunner(values={}))
        self.assertEqual(snapshot.entries, ())


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        channel = self.home / ".config" / "xfce4" / "xfconf" / "xfce-perchannel-xml"
        channel.mkdir(parents=True)
        (channel / "xfce4-panel.xml").write_text(SAMPLE_XML, encoding="utf-8")
        self.config = {"HOME": str(self.home), "DISPLAY": ":0", "PATH": "/bin"}
        self.panels = panel.parse_panels({"panel": {"plugins": [{"type": "clock"}]}})

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_apply_stops_writes_and_starts(self):
        runner = FakeRunner()
        spawned: list[tuple[str, dict]] = []
        with mock.patch.object(panel, "panel_running", return_value=False), mock.patch.object(
            panel.shutil, "which", return_value="/bin/xfce4-panel"
        ):
            previous = panel.apply_panels(
                self.panels,
                self.config,
                self.home,
                runner,
                spawn=lambda executable, environ: spawned.append((executable, dict(environ))),
            )
        self.assertTrue(any("/configver" in call for call in [" ".join(c) for c in runner.calls]))
        self.assertEqual(spawned[0][0], "/bin/xfce4-panel")
        self.assertTrue(
            any(entry.path == "/plugins/plugin-1/type" for entry in previous.entries)
        )

    def test_failed_apply_restores_the_snapshot(self):
        runner = FakeRunner(fail_at=3)
        spawned: list[str] = []
        with mock.patch.object(panel, "panel_running", return_value=False), mock.patch.object(
            panel.shutil, "which", return_value="/bin/xfce4-panel"
        ):
            with self.assertRaises(panel.PanelError):
                panel.apply_panels(
                    self.panels,
                    self.config,
                    self.home,
                    runner,
                    spawn=lambda executable, environ: spawned.append(executable),
                )
        joined = [" ".join(call) for call in runner.calls]
        # The failed attempt is followed by a reset and a replay of the old values.
        self.assertTrue(any("/plugins/plugin-1" in item and "-s whiskermenu" in item for item in joined))
        self.assertEqual(spawned, ["/bin/xfce4-panel"])

    def test_start_retries_after_a_panel_that_quits_right_away(self):
        spawned: list[str] = []
        killed: list[int] = []

        class Process:
            def __init__(self, exit_code):
                self.exit_code = exit_code

            def poll(self):
                return self.exit_code

        processes = [Process(1), Process(None)]

        def spawn(executable, environ):
            spawned.append(executable)
            return processes.pop(0)

        with mock.patch.object(panel, "_kill_panels", side_effect=lambda: killed.append(1)), mock.patch.object(
            panel.shutil, "which", return_value="/bin/xfce4-panel"
        ):
            panel.start_panel(self.config, spawn, sleep=lambda seconds: None, timeout=0.01)
        self.assertEqual(spawned, ["/bin/xfce4-panel", "/bin/xfce4-panel"])
        self.assertEqual(killed, [1])

    def test_start_gives_up_when_every_panel_quits(self):
        class Process:
            def poll(self):
                return 1

        with mock.patch.object(panel, "_kill_panels"), mock.patch.object(
            panel.shutil, "which", return_value="/bin/xfce4-panel"
        ):
            with self.assertRaisesRegex(panel.PanelError, "the panel exited while starting"):
                panel.start_panel(
                    self.config, lambda executable, environ: Process(), sleep=lambda seconds: None, timeout=0.01
                )

    def test_missing_panel_binary_is_reported(self):
        with mock.patch.object(panel, "panel_running", return_value=False), mock.patch.object(
            panel.shutil, "which", return_value=None
        ):
            with self.assertRaisesRegex(panel.PanelError, "xfce4-panel was not found"):
                panel.apply_panels(self.panels, self.config, self.home, FakeRunner())


class PluginFilesTest(unittest.TestCase):
    def test_missing_plugins_are_listed_with_an_install_hint(self):
        self.assertEqual(panel.library_name("whiskermenu"), "libwhiskermenu.so")
        self.assertEqual(panel.library_name("clock"), "libclock.so")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "libclock.so").write_text("", encoding="utf-8")
            panels = panel.parse_panels(
                {"panel": {"plugins": [{"type": "clock"}, {"type": "whiskermenu"}]}}
            )
            self.assertEqual(panel.missing_plugins(panels, directory), ["whiskermenu"])
            hint = panel.install_hint(["whiskermenu"])
        self.assertIn("xfce4-whiskermenu-plugin", hint)
        self.assertIn("xfce4-whiskermenu-plugin", hint)

    def test_unknown_plugin_names_share_a_generic_hint(self):
        self.assertEqual(
            panel.install_hint(["mystery"]),
            "install the missing Xfce panel plugin packages with pkg",
        )


if __name__ == "__main__":
    unittest.main()


class ItemsTest(unittest.TestCase):
    def test_launcher_items_and_actions_items_are_string_arrays(self):
        panels = panel.parse_panels(
            {
                "panels": [
                    {
                        "position": "bottom",
                        "size": 40,
                        "plugins": [
                            {"type": "actions", "items": ["+lock-screen", "-shutdown"]},
                            {"type": "launcher", "items": ["xterm.desktop"]},
                        ],
                    }
                ]
            }
        )
        commands = panel.panel_commands(panels)
        items = [command for command in commands if "/items" in " ".join(command)]
        self.assertEqual(len(items), 2)
        for command in items:
            self.assertEqual(command.count("-t"), command.count("string"))


class AutoLengthTest(unittest.TestCase):
    def test_auto_length_turns_on_length_adjust(self):
        panels = panel.parse_panels(
            {"panels": [{"position": "bottom-center", "length": "auto", "plugins": [{"type": "clock"}]}]}
        )
        joined = [" ".join(command) for command in panel.panel_commands(panels)]
        self.assertIn("-p /panels/panel-1/length -n -t uint -s 1", " | ".join(joined))
        self.assertIn("-p /panels/panel-1/length-adjust -n -t bool -s true", " | ".join(joined))
