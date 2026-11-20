"""Tests for the skin catalog commands: list, show, check, apply, reset, init."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace

from termux_desk import catalog, skin, skin_catalog
from termux_desk import layout as layout_tools
from termux_desk.config import DeskError
from termux_desk.layout import LayoutError
from termux_desk.settings import model as settings_model
from termux_desk.skin_model import SkinError

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")

SESSION = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}

SKIN_TEMPLATE = """\
id: {name}
name: {name}
summary: test skin
layout: compact
defaults:
  theme: {theme}
  icons: Icons
themes:
  - name: {theme}
    path: {theme_path}
icons:
  - name: Icons
    path: {icons_path}
"""


class SkinCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        patched = mock.patch.dict(os.environ, self.workspace.environ, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def make_tree(self, name: str) -> str:
        """Create a directory that can stand in for a theme or an icon theme."""
        directory = self.workspace.root / "trees" / name
        (directory / "index.theme").parent.mkdir(parents=True, exist_ok=True)
        (directory / "index.theme").write_text(f"[Icon Theme]\nName={name}\n", encoding="utf-8")
        return str(directory)

    def write_skin(self, name: str, theme_name: str = "Theme", layout: str = "compact") -> None:
        directory = catalog.catalog_directory(self.paths) / name
        directory.mkdir(parents=True, exist_ok=True)
        text = SKIN_TEMPLATE.format(
            name=name,
            theme=theme_name,
            theme_path=self.make_tree(theme_name),
            icons_path=self.make_tree("Icons"),
        ).replace("layout: compact", f"layout: {layout}", 1)
        (directory / catalog.SKIN_FILE).write_text(text, encoding="utf-8")

    def select(self, name: str) -> None:
        config = settings_model.config_directory(self.paths)
        config.mkdir(parents=True, exist_ok=True)
        (config / "config.yaml").write_text(f"version: 1\nskin: {name}\n", encoding="utf-8")


class ListingTest(SkinCase):
    def test_the_builtin_catalog_is_listed_with_counts(self):
        out = io.StringIO()
        self.assertEqual(skin_catalog.run_list(self.paths, out=out), 0)
        text = out.getvalue()
        self.assertIn("nord", text)
        self.assertIn("materia", text)
        self.assertIn("win95", text)
        self.assertIn("TH", text)

    def test_an_applied_skin_is_marked(self):
        self.write_skin("mine")
        from termux_desk import skin_model

        skin_model.write_applied(self.paths, "nord", ":0")
        out = io.StringIO()
        skin_catalog.run_list(self.paths, out=out)
        self.assertIn("*nord", out.getvalue())

    def test_a_broken_manifest_is_marked_invalid(self):
        directory = catalog.catalog_directory(self.paths) / "broken"
        directory.mkdir(parents=True)
        (directory / catalog.SKIN_FILE).write_text("id: broken\n", encoding="utf-8")
        out = io.StringIO()
        skin_catalog.run_list(self.paths, out=out)
        self.assertIn("invalid", out.getvalue())

    def test_show_prints_the_defaults_and_variants(self):
        out = io.StringIO()
        self.assertEqual(skin_catalog.run_show(self.paths, "nord", out=out), 0)
        text = out.getvalue()
        self.assertIn("Nordic-darker", text)
        self.assertIn("kora", text)
        self.assertIn("sha256", text)

    def test_check_prints_the_selected_group(self):
        out = io.StringIO()
        self.assertEqual(skin_catalog.run_check(self.paths, "nord", out=out), 0)
        text = out.getvalue()
        self.assertIn("theme Nordic-darker, icons kora", text)
        self.assertIn("is valid.", text)

    def test_check_rejects_an_unknown_variant(self):
        with self.assertRaisesRegex(catalog.CatalogError, "has no theme 'Nope'"):
            skin_catalog.run_check(
                self.paths, "nord", theme="Nope", out=io.StringIO()
            )


class SelectionTest(SkinCase):
    def test_the_configuration_selects_the_skin_when_the_name_is_omitted(self):
        self.write_skin("mine")
        self.select("mine")
        loaded = skin_catalog.resolve(self.paths, None)
        self.assertEqual(loaded.id, "mine")

    def test_no_selection_is_a_readable_error(self):
        with self.assertRaisesRegex(SkinError, "no skin is selected"):
            skin_catalog.resolve(self.paths, None)

    def test_a_named_variant_becomes_the_selection(self):
        self.write_skin("mine")
        output = io.StringIO()
        with mock.patch.object(skin.theme_tools, "apply_appearance", return_value=None), mock.patch.object(
            skin.layout_tools, "apply_layout", return_value=0
        ):
            skin_catalog.run_apply(self.paths, "mine", theme="Theme", out=output)
        self.assertIn("applied skin mine", output.getvalue())

    def test_install_all_includes_every_variant(self):
        directory = catalog.catalog_directory(self.paths) / "mine"
        directory.mkdir(parents=True)
        text = SKIN_TEMPLATE.format(
            name="mine",
            theme="Theme",
            theme_path=self.make_tree("Theme"),
            icons_path=self.make_tree("Icons"),
        ) + f"  - name: Other\n    path: {self.make_tree('Other')}\n"
        (directory / catalog.SKIN_FILE).write_text(text, encoding="utf-8")
        skin = catalog.load(self.paths, "mine")
        manifest = catalog.build_theme(
            skin, catalog.resolve_selection(skin), install_all=True
        )
        targets = {asset.target for asset in manifest.assets}
        self.assertIn("Other", targets)


class ApplyTest(SkinCase):
    def test_the_appearance_is_applied_before_the_layout(self):
        self.write_skin("mine")
        order = []
        with mock.patch.object(
            skin.theme_tools,
            "apply_appearance",
            side_effect=lambda *a, **k: order.append("appearance"),
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=lambda *a, **k: order.append("layout")
        ):
            out = io.StringIO()
            code = skin_catalog.run_apply(self.paths, "mine", out=out, session=SESSION)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["appearance", "layout"])
        self.assertIn("applied skin mine", out.getvalue())

    def test_a_failed_layout_rolls_the_appearance_back(self):
        self.write_skin("mine")
        application = object()
        seen = {}

        def rollback(paths, record, **kwargs):
            seen["record"] = record

        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=application
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=LayoutError("boom")
        ), mock.patch.object(skin.theme_tools, "rollback_appearance", side_effect=rollback):
            with self.assertRaises(LayoutError):
                skin_catalog.run_apply(self.paths, "mine", out=io.StringIO(), session=SESSION)
        self.assertIs(seen["record"], application)

    def test_a_failed_rollback_is_reported(self):
        self.write_skin("mine")
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=object()
        ), mock.patch.object(
            skin.layout_tools, "apply_layout", side_effect=LayoutError("boom")
        ), mock.patch.object(
            skin.theme_tools, "rollback_appearance", side_effect=DeskError("nope")
        ):
            with self.assertRaisesRegex(
                SkinError, r"the layout failed \(boom\).*could not be"
            ):
                skin_catalog.run_apply(self.paths, "mine", out=io.StringIO(), session=SESSION)


class ResetTest(SkinCase):
    def test_reset_restores_the_layout_before_the_appearance(self):
        order = []
        with mock.patch.object(
            skin.layout_tools, "run_layout_reset", side_effect=lambda *a, **k: order.append("layout")
        ), mock.patch.object(
            skin.theme_tools, "run_theme_reset", side_effect=lambda *a, **k: order.append("appearance")
        ):
            self.assertEqual(skin_catalog.run_reset(self.paths, out=io.StringIO()), 0)
        self.assertEqual(order, ["layout", "appearance"])

    def test_reset_reports_every_failure(self):
        with mock.patch.object(
            skin.layout_tools, "run_layout_reset", side_effect=DeskError("layout broke")
        ), mock.patch.object(
            skin.theme_tools, "run_theme_reset", side_effect=DeskError("appearance broke")
        ):
            with self.assertRaisesRegex(SkinError, "layout broke; appearance broke"):
                skin_catalog.run_reset(self.paths, out=io.StringIO())


class InitTest(SkinCase):
    def test_init_writes_a_template_that_loads(self):
        out = io.StringIO()
        self.assertEqual(skin_catalog.run_init(self.paths, "mine", force=False, out=out), 0)
        loaded = catalog.load(self.paths, "mine")
        self.assertEqual(loaded.id, "mine")

    def test_init_refuses_to_overwrite_without_force(self):
        skin_catalog.run_init(self.paths, "mine", force=False, out=io.StringIO())
        with self.assertRaisesRegex(SkinError, "already exists"):
            skin_catalog.run_init(self.paths, "mine", force=False, out=io.StringIO())

    def test_init_rejects_a_name_that_cannot_be_a_directory(self):
        with self.assertRaisesRegex(SkinError, "not a usable skin name"):
            skin_catalog.run_init(self.paths, "two words", force=False, out=io.StringIO())


@needs_yaml
class HalfApplyTest(SkinCase):
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


@needs_yaml
class AppearanceSubdomainTest(SkinCase):
    def test_a_dry_run_does_not_need_a_running_session(self):
        output = io.StringIO()
        self.assertEqual(
            skin.run_appearance_apply(self.paths, "stock", dry_run=True, out=output), 0
        )
        self.assertIn("would set xsettings/Net/ThemeName: Adwaita", output.getvalue())

    def test_a_layout_dry_run_does_not_need_a_running_session(self):
        output = io.StringIO()
        self.assertEqual(
            skin.run_layout_apply(self.paths, "compact", dry_run=True, out=output), 0
        )
        self.assertIn("would set", output.getvalue())


class AvailablePresetsTest(unittest.TestCase):
    def test_the_layout_presets_are_shipped(self):
        directory = Path(layout_tools.__file__).parent / "presets" / "layout"
        names = {path.stem for path in directory.glob("*.yaml")}
        self.assertIn("panel-nord", names)
        self.assertIn("panel-materia", names)
        self.assertIn("panel-win95", names)
