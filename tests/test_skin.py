"""Tests for the skin catalog commands: list, show, check, apply, reset, init."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace

from termux_desk import catalog, optional, skin, skin_build, skin_catalog
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

    def test_check_reports_a_default_that_still_needs_a_build(self):
        # nord 的默认光标来自 qogir-icons 的本地构建，测试环境里没有 builds。
        out = io.StringIO()
        self.assertEqual(skin_catalog.run_check(self.paths, "nord", out=out), 1)
        text = out.getvalue()
        self.assertIn("theme Nordic-darker, icons kora", text)
        self.assertIn("Qogir-dark: local", text)
        self.assertIn("desk skin build", text)

    def test_check_rejects_an_unknown_variant(self):
        with self.assertRaisesRegex(catalog.CatalogError, "has no theme 'Nope'"):
            skin_catalog.run_check(
                self.paths, "nord", theme="Nope", out=io.StringIO()
            )


class CheckTest(SkinCase):
    def run_check(self, name="mine", **kwargs):
        out = io.StringIO()
        code = skin_catalog.run_check(self.paths, name, out=out, **kwargs)
        return code, out.getvalue()

    def append_to_skin(self, name, text):
        manifest = catalog.catalog_directory(self.paths) / name / catalog.SKIN_FILE
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + text, encoding="utf-8"
        )

    def dpkg(self, installed=()):
        """Stand in for dpkg-query, reporting only the named packages as installed."""

        def runner(argv, env):
            package = argv[-1]
            present = package in installed
            return subprocess.CompletedProcess(
                argv,
                0 if present else 1,
                "install ok installed" if present else "",
                "" if present else "no packages found",
            )

        return runner

    def test_a_local_asset_is_reported_as_local(self):
        self.write_skin("mine")
        code, text = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("asset theme Theme: local", text)
        self.assertIn("asset icons Icons: local", text)
        self.assertNotIn("allow-unverified", text)
        self.assertIn("skin mine is valid.", text)

    def test_a_missing_local_asset_fails_the_check(self):
        self.write_skin("mine")
        shutil.rmtree(self.workspace.root / "trees" / "Theme")
        code, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("asset theme Theme: local, missing", text)
        self.assertIn("does not exist", text)
        self.assertIn("fix the 'path'", text)
        self.assertNotIn("allow-unverified", text)
        self.assertIn("skin mine is invalid: 1 local asset is missing.", text)

    def test_a_missing_built_asset_names_the_build_recipe(self):
        directory = catalog.catalog_directory(self.paths) / "mine"
        directory.mkdir(parents=True)
        (directory / catalog.SKIN_FILE).write_text(
            "id: mine\n"
            "name: mine\n"
            "summary: test skin\n"
            "defaults:\n"
            "  theme: Lavanda-Dark\n"
            "  icons: Icons\n"
            "themes:\n"
            "  - name: Lavanda-Dark\n"
            "    path: ~/.local/share/termux-desktop/builds/lavanda-theme/Lavanda-Dark\n"
            "icons:\n"
            f"  - name: Icons\n    path: {self.make_tree('Icons')}\n",
            encoding="utf-8",
        )
        code, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("asset theme Lavanda-Dark: local, missing", text)
        self.assertIn("run 'desk skin build' with the lavanda-theme recipe first", text)
        self.assertNotIn("allow-unverified", text)

    def test_all_checks_every_variant(self):
        directory = catalog.catalog_directory(self.paths) / "mine"
        directory.mkdir(parents=True)
        digest = "a" * 64
        (directory / catalog.SKIN_FILE).write_text(
            "id: mine\n"
            "name: mine\n"
            "summary: test skin\n"
            "defaults:\n"
            "  theme: Theme\n"
            "  icons: Icons\n"
            "themes:\n"
            f"  - name: Theme\n    path: {self.make_tree('Theme')}\n"
            "  - name: Other\n"
            "    url: https://example.invalid/Other.tar.gz\n"
            f"    sha256: {digest}\n"
            "    size: 1000\n"
            "icons:\n"
            f"  - name: Icons\n    path: {self.make_tree('Icons')}\n"
            "  - name: OtherIcons\n"
            "    url: https://example.invalid/OtherIcons.tar.gz\n"
            f"    sha256: {digest}\n"
            "    size: 2000\n",
            encoding="utf-8",
        )
        code, text = self.run_check()
        self.assertEqual(code, 0)
        self.assertNotIn("asset theme Other:", text)
        self.assertIn("download -", text)
        code, text = self.run_check(install_all=True)
        self.assertEqual(code, 0)
        self.assertIn("(checking every variant)", text)
        self.assertIn("asset theme Other: sha256 pinned", text)
        self.assertIn("asset icons OtherIcons: sha256 pinned", text)
        self.assertIn("download 2.9 KB", text)

    def test_missing_packages_are_reported_without_failing(self):
        self.write_skin("mine")
        self.append_to_skin(
            "mine",
            "packages:\n  - xfce4-whiskermenu-plugin\n  - xfce4-clipman-plugin\n",
        )
        code, text = self.run_check(runner=self.dpkg(installed={"xfce4-clipman-plugin"}))
        self.assertEqual(code, 0)
        self.assertIn("packages missing: xfce4-whiskermenu-plugin;", text)
        self.assertIn("'desk skin apply' will fail", text)
        self.assertIn("skin mine is valid; install the missing packages", text)

    def test_installed_packages_are_reported(self):
        self.write_skin("mine")
        self.append_to_skin("mine", "packages:\n  - xfce4-whiskermenu-plugin\n")
        code, text = self.run_check(
            runner=self.dpkg(installed={"xfce4-whiskermenu-plugin"})
        )
        self.assertEqual(code, 0)
        self.assertIn("packages installed: xfce4-whiskermenu-plugin", text)
        self.assertIn("skin mine is valid.", text)

    @needs_yaml
    def test_with_optional_reports_the_optional_components(self):
        self.write_skin("mine")
        self.append_to_skin("mine", "requires:\n  - eww\noptional:\n  - kvantum\n")
        code, text = self.run_check()
        self.assertEqual(code, 0)
        self.assertNotIn("requires eww", text)
        self.assertNotIn("optional kvantum", text)
        code, text = self.run_check(with_optional=True, runner=self.dpkg())
        self.assertEqual(code, 0)
        self.assertIn("requires eww: packages eww (missing)", text)
        self.assertIn("optional kvantum: packages kvantum (missing)", text)
        self.assertIn("skin mine is valid.", text)


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

    def test_settings_the_previous_skin_changed_go_back_to_the_baseline(self):
        self.write_skin("mine")
        from termux_desk import skin_baseline

        skin_baseline.record(
            self.paths,
            skin_baseline.Baseline(
                display=":0", skin="win95", values={"xsettings": {"/Gtk/FontName": "Sans 10"}}
            ),
            "win95",
        )
        session = {("xsettings", "/Gtk/FontName"): "More Perfect DOS VGA 10"}
        from tests.test_skin_baseline import FakeSession

        runner = FakeSession(session)
        out = io.StringIO()
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=object()
        ), mock.patch.object(skin.layout_tools, "apply_layout"):
            code = skin_catalog.run_apply(
                self.paths, "mine", out=out, session=SESSION, runner=runner
            )
        self.assertEqual(code, 0)
        self.assertEqual(runner.values[("xsettings", "/Gtk/FontName")], "Sans 10")
        self.assertIn("restored xsettings/Gtk/FontName", out.getvalue())

    def test_a_dry_run_reports_the_layout_without_applying_it(self):
        self.write_skin("mine")
        out = io.StringIO()
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=None
        ), mock.patch.object(skin.layout_tools, "apply_layout") as apply_layout:
            code = skin_catalog.run_apply(
                self.paths, "mine", dry_run=True, out=out, session=SESSION
            )
        self.assertEqual(code, 0)
        self.assertIn("would apply layout compact", out.getvalue())
        apply_layout.assert_not_called()

    def test_with_optional_installs_the_components_the_skin_lists(self):
        self.write_skin("mine")
        directory = catalog.catalog_directory(self.paths) / "mine"
        manifest = directory / catalog.SKIN_FILE
        manifest.write_text(
            manifest.read_text(encoding="utf-8")
            + "requires:\n  - eww\noptional:\n  - kvantum\n",
            encoding="utf-8",
        )
        out = io.StringIO()
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=object()
        ), mock.patch.object(skin.layout_tools, "apply_layout"), mock.patch.object(
            optional, "install", return_value=2
        ) as install:
            skin_catalog.run_apply(
                self.paths, "mine", with_optional=True, out=out, session=SESSION
            )
        install.assert_called_once()
        self.assertEqual(install.call_args.args[1], ("eww", "kvantum"))

    def test_without_optional_nothing_extra_is_installed(self):
        self.write_skin("mine")
        out = io.StringIO()
        with mock.patch.object(
            skin.theme_tools, "apply_appearance", return_value=object()
        ), mock.patch.object(skin.layout_tools, "apply_layout"), mock.patch.object(
            optional, "install"
        ) as install:
            skin_catalog.run_apply(self.paths, "mine", out=out, session=SESSION)
        install.assert_not_called()

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

@needs_yaml
class BuiltinSkinVariantsTest(unittest.TestCase):
    """Every shipped path variant must name a build recipe artifact."""

    BUILDS_SOURCE_PREFIX = "~/.local/share/termux-desktop/builds/"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths

    def builtin_skins(self):
        directory = catalog.builtin_directory()
        manifests = sorted(directory.glob("*/skin.yaml"))
        self.assertTrue(manifests, "no builtin skin manifests are shipped")
        return [catalog.load(self.paths, manifest.parent.name) for manifest in manifests]

    def test_every_path_variant_maps_to_a_build_artifact(self):
        recipes = {
            path.stem: skin_build.load_recipe(path)
            for path in sorted((catalog.builtin_directory().parent / "builds").glob("*.yaml"))
        }
        for entry in self.builtin_skins():
            for kind in catalog.VARIANT_KINDS:
                for variant in entry.variants(kind):
                    if variant.is_url:
                        continue
                    context = f"{entry.id} {kind} {variant.name}: {variant.source}"
                    self.assertTrue(
                        variant.source.startswith(self.BUILDS_SOURCE_PREFIX), context
                    )
                    recipe, _, name = variant.source[len(self.BUILDS_SOURCE_PREFIX):].partition("/")
                    self.assertIn(recipe, recipes, context)
                    targets = [artifact.target for artifact in recipes[recipe].artifacts]
                    self.assertIn(name, targets, context)

    def test_every_url_variant_has_a_sha256(self):
        for entry in self.builtin_skins():
            for kind in catalog.VARIANT_KINDS:
                for variant in entry.variants(kind):
                    if not variant.is_url:
                        continue
                    self.assertRegex(
                        variant.sha256 or "",
                        r"^[0-9a-f]{64}$",
                        f"{entry.id} {kind} {variant.name}",
                    )

