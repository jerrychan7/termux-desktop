"""Tests for layout manifests and the xfconf apply/reset flow."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import Workspace

from termux_desk import layout

try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover - devices without PyYAML skip parsing
    yaml = None


def needs_yaml(function):
    return unittest.skipIf(yaml is None, "PyYAML is not installed")(function)


class FakeSession:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.reloaded = 0
        self.fail_after: int | None = None

    def get(self, channel: str, name: str) -> str | None:
        return self.values.get((channel, name))

    def set(self, channel: str, name: str, value: str) -> None:
        if self.fail_after is not None and len(self.values) >= self.fail_after:
            raise RuntimeError("injected failure")
        self.values[(channel, name)] = value

    def remove(self, channel: str, name: str) -> None:
        self.values.pop((channel, name), None)

    def reload(self, out: io.StringIO) -> None:
        self.reloaded += 1


class LayoutCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths

    def write_layout(self, text: str, name: str = "test") -> Path:
        directory = layout.layouts_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        return path


@needs_yaml
class ManifestFailureTest(LayoutCase):
    def test_a_manifest_that_is_not_utf8_is_reported(self):
        directory = layout.layouts_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bad.yaml").write_bytes(b"layout: bad\n\xff\xfe")
        with self.assertRaisesRegex(layout.LayoutError, "is not UTF-8"):
            layout.load_layout(self.paths, "bad")


class StateTest(LayoutCase):
    def test_a_corrupt_state_file_is_reported(self):
        path = layout.state_path(self.paths)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(layout.LayoutError, "is not valid JSON"):
            layout.run_layout_reset(self.paths, out=io.StringIO(), session=FakeSession())

    def test_reset_refuses_a_different_display(self):
        self.write_layout("layout: compact\nxfwm4:\n  button_layout: O|SHMC\n", name="compact")
        layout.run_layout_apply(
            self.paths, "compact", out=io.StringIO(), session=FakeSession(), display=":1"
        )
        with self.assertRaisesRegex(layout.LayoutError, "was applied on :1"):
            layout.run_layout_reset(self.paths, out=io.StringIO(), session=FakeSession())


class ManifestTest(LayoutCase):
    def test_compact_preset_is_available(self):
        manifest = layout.load_layout(self.paths, "compact")
        self.assertIn("/general/button_layout", {item.property for item in manifest.settings})
        self.assertIn(layout.PANEL_SIZE_PROPERTY, {item.property for item in manifest.settings})

    def test_unknown_layout_is_reported(self):
        with self.assertRaisesRegex(layout.LayoutError, "no layout named"):
            layout.load_layout(self.paths, "missing")

    def test_duplicate_keys_are_rejected(self):
        self.write_layout("layout: test\nxfwm4:\n  title_alignment: center\n  title_alignment: left\n")
        with self.assertRaisesRegex(layout.LayoutError, "repeats the key"):
            layout.load_layout(self.paths, "test")

    def test_unknown_xfwm4_key_is_rejected(self):
        self.write_layout("layout: test\nxfwm4:\n  button_layout: O|SHMC\n  magic: true\n")
        with self.assertRaisesRegex(layout.LayoutError, "unknown xfwm4"):
            layout.load_layout(self.paths, "test")

    def test_unknown_panel_key_is_rejected(self):
        self.write_layout("layout: test\npanel:\n  position: bottom\n")
        with self.assertRaisesRegex(layout.LayoutError, "unknown panel"):
            layout.load_layout(self.paths, "test")

    def test_workspace_count_is_written_to_xfwm4(self):
        self.write_layout("layout: test\nxfwm4:\n  workspace_count: 2\n")
        manifest = layout.load_layout(self.paths, "test")
        setting = next(item for item in manifest.settings if item.property == "/general/workspace_count")
        self.assertEqual((setting.channel, setting.value), ("xfwm4", "2"))

    def test_workspace_names_are_kept_as_a_string_array(self):
        self.write_layout('layout: test\nxfwm4:\n  workspace_names:\n    - "1"\n    - "2"\n')
        manifest = layout.load_layout(self.paths, "test")
        setting = next(item for item in manifest.settings if item.property == "/general/workspace_names")
        self.assertEqual(setting.value, ("1", "2"))

    def test_workspace_names_must_be_a_non_empty_string_list(self):
        for body in ("[],", '["1", ""]', '[1, 2]', '[' + ", ".join('"n"' for _ in range(37)) + ']'):
            self.write_layout(f"layout: test\nxfwm4:\n  workspace_names: {body}")
            with self.assertRaisesRegex(layout.LayoutError, "names"):
                layout.load_layout(self.paths, "test")

    def test_workspace_count_must_be_an_integer_in_range(self):
        for value in ("0", "37", "true", '"2"'):
            self.write_layout(f"layout: test\nxfwm4:\n  workspace_count: {value}\n")
            with self.assertRaisesRegex(layout.LayoutError, "1 and 36"):
                layout.load_layout(self.paths, "test")

    def test_bad_button_layout_is_rejected(self):
        self.write_layout("layout: test\nxfwm4:\n  button_layout: Z|Q\n")
        with self.assertRaisesRegex(layout.LayoutError, "OSHM"):
            layout.load_layout(self.paths, "test")

    def test_panel_size_must_be_an_integer_in_range(self):
        for value in ("small", "true", "8", "200"):
            self.write_layout(f"layout: test\npanel:\n  size: {value}\n")
            with self.assertRaisesRegex(layout.LayoutError, "16 and 128"):
                layout.load_layout(self.paths, "test")

    def test_missing_name_or_wrong_name_is_rejected(self):
        self.write_layout("xfwm4:\n  title_alignment: center\n")
        with self.assertRaisesRegex(layout.LayoutError, "declares layout"):
            layout.load_layout(self.paths, "test")
        self.write_layout("layout: other\nxfwm4:\n  title_alignment: center\n")
        with self.assertRaisesRegex(layout.LayoutError, "expected 'test'"):
            layout.load_layout(self.paths, "test")

    def test_empty_layout_is_rejected(self):
        self.write_layout("layout: test\n")
        with self.assertRaisesRegex(layout.LayoutError, "does not change anything"):
            layout.load_layout(self.paths, "test")

    def test_list_marks_the_active_layout(self):
        layout._write_state(self.paths, layout.LayoutState(active="compact"))
        output = io.StringIO()
        self.assertEqual(layout.run_layout_list(self.paths, out=output), 0)
        self.assertIn("* compact (preset)", output.getvalue())

    def test_check_prints_each_setting(self):
        output = io.StringIO()
        self.assertEqual(layout.run_layout_check(self.paths, "compact", out=output), 0)
        text = output.getvalue()
        self.assertIn("xfwm4/general/button_layout", text)
        self.assertIn("size = 32", text)
        self.assertIn("is valid", text)

    def test_init_refuses_to_replace_without_force(self):
        path = layout.layouts_directory(self.paths) / "mine.yaml"
        output = io.StringIO()
        self.assertEqual(layout.run_layout_init(self.paths, "mine", force=False, out=output), 0)
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(layout.LayoutError, "already exists"):
            layout.run_layout_init(self.paths, "mine", force=False, out=output)
        self.assertEqual(layout.run_layout_init(self.paths, "mine", force=True, out=output), 0)

    def test_init_backs_up_the_file_it_replaces(self):
        directory = layout.layouts_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "mine.yaml"
        path.write_text("keep me\n", encoding="utf-8")
        output = io.StringIO()
        self.assertEqual(layout.run_layout_init(self.paths, "mine", force=True, out=output), 0)
        backup = directory / "mine.yaml.bak"
        self.assertEqual(backup.read_text(encoding="utf-8"), "keep me\n")
        self.assertIn("backed up", output.getvalue())

    def test_init_refuses_names_that_escape_the_directory(self):
        output = io.StringIO()
        for name in ("../config", "..", "/tmp/pwned", "a/b", ""):
            with self.assertRaises(layout.LayoutError):
                layout.run_layout_init(self.paths, name, force=True, out=output)
        config_root = layout.config_directory(self.paths)
        written = sorted(item.name for item in config_root.rglob("*")) if config_root.exists() else []
        self.assertEqual(written, [])

    def test_init_refuses_to_replace_a_symlink(self):
        directory = layout.layouts_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        victim = Path(self.temporary.name) / "victim.txt"
        victim.write_text("untouched\n", encoding="utf-8")
        (directory / "mine.yaml").symlink_to(victim)
        with self.assertRaisesRegex(layout.LayoutError, "not a regular file"):
            layout.run_layout_init(self.paths, "mine", force=True, out=io.StringIO())
        self.assertEqual(victim.read_text(encoding="utf-8"), "untouched\n")


@needs_yaml
class ApplyTest(LayoutCase):
    def test_apply_writes_and_snapshots_then_reset_restores(self):
        session = FakeSession()
        session.values[("xfwm4", "/general/title_alignment")] = "left"
        session.values[(layout.PANEL_CHANNEL, layout.PANEL_SIZE_PROPERTY)] = "28"
        output = io.StringIO()
        self.assertEqual(
            layout.run_layout_apply(
                self.paths, "compact", out=output, session=session
            ),
            0,
        )
        self.assertEqual(session.values[("xfwm4", "/general/title_alignment")], "center")
        self.assertEqual(session.values[(layout.PANEL_CHANNEL, layout.PANEL_SIZE_PROPERTY)], "32")
        self.assertEqual(session.reloaded, 1)
        self.assertIn("applied layout compact", output.getvalue())

        self.assertEqual(layout.run_layout_reset(self.paths, out=output, session=session), 0)
        self.assertEqual(session.values[("xfwm4", "/general/title_alignment")], "left")
        self.assertEqual(session.values[(layout.PANEL_CHANNEL, layout.PANEL_SIZE_PROPERTY)], "28")
        self.assertEqual(session.reloaded, 2)
        self.assertFalse(layout.state_path(self.paths).exists())

    def test_reapplying_keeps_the_values_from_the_first_apply(self):
        session = FakeSession()
        session.values[("xfwm4", "/general/title_alignment")] = "left"
        session.values[(layout.PANEL_CHANNEL, layout.PANEL_SIZE_PROPERTY)] = "28"
        for _ in range(2):
            self.assertEqual(
                layout.run_layout_apply(self.paths, "compact", out=io.StringIO(), session=session),
                0,
            )
        self.assertEqual(
            layout.run_layout_reset(self.paths, out=io.StringIO(), session=session), 0
        )
        self.assertEqual(session.values[("xfwm4", "/general/title_alignment")], "left")
        self.assertEqual(session.values[(layout.PANEL_CHANNEL, layout.PANEL_SIZE_PROPERTY)], "28")

    def test_dry_run_does_not_write_or_snapshot(self):
        session = FakeSession()
        output = io.StringIO()
        self.assertEqual(
            layout.run_layout_apply(
                self.paths, "compact", dry_run=True, out=output, session=session
            ),
            0,
        )
        self.assertEqual(session.values, {})
        self.assertFalse(layout.state_path(self.paths).exists())
        self.assertIn("would set", output.getvalue())

    def test_failure_rolls_back_written_values(self):
        session = FakeSession()
        session.fail_after = 1
        output = io.StringIO()
        with self.assertRaisesRegex(layout.LayoutError, "could not apply"):
            layout.run_layout_apply(self.paths, "compact", out=output, session=session)
        self.assertEqual(session.values, {})
        self.assertFalse(layout.state_path(self.paths).exists())

    def test_reset_without_state_reports_and_succeeds(self):
        output = io.StringIO()
        self.assertEqual(layout.run_layout_reset(self.paths, out=output, session=FakeSession()), 0)
        self.assertIn("No layout is applied", output.getvalue())

    def test_reset_removes_properties_that_did_not_exist(self):
        session = FakeSession()
        output = io.StringIO()
        layout.run_layout_apply(self.paths, "compact", out=output, session=session)
        layout.run_layout_reset(self.paths, out=output, session=session)
        self.assertEqual(session.values, {})
