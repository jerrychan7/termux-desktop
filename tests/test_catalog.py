"""Tests for the skin catalog model."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import Workspace

from termux_desk import catalog
from termux_desk.catalog import CatalogError

SHA = "a" * 64
BASE = """\
id: {id}
name: {name}
summary: a test skin
layout: panel-test
defaults:
  theme: Theme-A
  icons: Icons-A
themes:
  - name: Theme-A
    url: https://example.invalid/theme-a.tar.gz
    sha256: {sha}
    pick: Theme-A
    size: 100
  - name: Theme-B
    url: https://example.invalid/theme-b.tar.gz
    sha256: {sha}
    size: 200
icons:
  - name: Icons-A
    url: https://example.invalid/icons-a.tar.gz
    sha256: {sha}
    size: 300
  - name: Icons-B
    url: https://example.invalid/icons-b.tar.gz
    sha256: {sha}
    size: 400
cursors:
  - name: Cursor-A
    url: https://example.invalid/cursor-a.tar.gz
    sha256: {sha}
    size: 50
fonts:
  - name: Font-A
    url: https://example.invalid/font-a.tar.gz
    sha256: {sha}
    size: 25
wallpaper:
  url: https://example.invalid/wallpapers.tar.gz
  sha256: {sha}
  pick: Extras/Wallpaper
  target: Bubbles.png
  size: 75
requires: []
optional: [eww]
"""


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths

    def write(self, skin_id: str, text: str) -> None:
        directory = catalog.catalog_directory(self.paths) / skin_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / catalog.SKIN_FILE).write_text(text, encoding="utf-8")

    def good(self, skin_id: str = "test") -> None:
        self.write(skin_id, BASE.format(id=skin_id, name="Test", sha=SHA))


class ParseTest(CatalogCase):
    def test_a_valid_skin_lists_every_variant(self):
        self.good()
        skin = catalog.load(self.paths, "test")
        self.assertEqual(skin.name, "Test")
        self.assertEqual(skin.layout, "panel-test")
        self.assertEqual([item.name for item in skin.themes], ["Theme-A", "Theme-B"])
        self.assertEqual([item.name for item in skin.icons], ["Icons-A", "Icons-B"])
        self.assertEqual(skin.defaults, {"theme": "Theme-A", "icons": "Icons-A"})
        self.assertEqual(skin.wallpaper.target, "Bubbles.png")
        self.assertEqual(skin.optional, ("eww",))

    def test_the_id_must_match_the_directory(self):
        self.write("test", BASE.format(id="other", name="Test", sha=SHA))
        with self.assertRaisesRegex(CatalogError, "must declare 'id: test'"):
            catalog.load(self.paths, "test")

    def test_unknown_keys_are_rejected(self):
        self.write("test", BASE.format(id="test", name="Test", sha=SHA) + "magic: true\n")
        with self.assertRaisesRegex(CatalogError, "unknown keys: magic"):
            catalog.load(self.paths, "test")

    def test_a_default_must_name_a_listed_variant(self):
        text = BASE.format(id="test", name="Test", sha=SHA).replace(
            "theme: Theme-A", "theme: Missing"
        )
        self.write("test", text)
        with self.assertRaisesRegex(CatalogError, "does not list"):
            catalog.load(self.paths, "test")

    def test_a_theme_default_is_required(self):
        text = BASE.format(id="test", name="Test", sha=SHA)
        text = text.replace("theme: Theme-A\n", "")
        self.write("test", text)
        with self.assertRaisesRegex(CatalogError, "no default theme"):
            catalog.load(self.paths, "test")

    def test_duplicate_variant_names_are_rejected(self):
        text = BASE.format(id="test", name="Test", sha=SHA).replace(
            "name: Theme-B", "name: Theme-A"
        )
        self.write("test", text)
        with self.assertRaisesRegex(CatalogError, "repeats the theme"):
            catalog.load(self.paths, "test")

    def test_a_pick_cannot_escape_the_archive(self):
        text = BASE.format(id="test", name="Test", sha=SHA).replace(
            "pick: Theme-A", "pick: ../../etc"
        )
        self.write("test", text)
        with self.assertRaisesRegex(CatalogError, "escapes the archive"):
            catalog.load(self.paths, "test")

    def test_url_and_path_are_mutually_exclusive(self):
        text = (
            "id: test\n"
            "name: Test\n"
            "defaults:\n  theme: Theme-A\n  icons: Icons-A\n"
            "themes:\n"
            "  - name: Theme-A\n"
            "    url: https://example.invalid/a.tar.gz\n"
            f"    sha256: {SHA}\n"
            "    path: /tmp/a.tar.gz\n"
            "icons:\n"
            "  - name: Icons-A\n"
            "    url: https://example.invalid/i.tar.gz\n"
            f"    sha256: {SHA}\n"
        )
        self.write("test", text)
        with self.assertRaisesRegex(CatalogError, "both 'url' and 'path'"):
            catalog.load(self.paths, "test")

    def test_a_bad_sha256_is_rejected(self):
        self.write("test", BASE.format(id="test", name="Test", sha="xyz"))
        with self.assertRaisesRegex(CatalogError, "invalid sha256"):
            catalog.load(self.paths, "test")

    def test_a_missing_skin_is_reported(self):
        with self.assertRaisesRegex(CatalogError, "there is no skin named 'nope'"):
            catalog.load(self.paths, "nope")

    def test_list_ids_returns_user_skins(self):
        self.good("zeta")
        self.assertIn("zeta", catalog.list_ids(self.paths))


class SelectionTest(CatalogCase):
    def setUp(self):
        super().setUp()
        self.good()
        self.skin = catalog.load(self.paths, "test")

    def test_defaults_are_selected(self):
        selection = catalog.resolve_selection(self.skin)
        self.assertEqual(selection.theme.name, "Theme-A")
        self.assertEqual(selection.icons.name, "Icons-A")
        self.assertIsNone(selection.cursors)

    def test_overrides_pick_other_variants(self):
        selection = catalog.resolve_selection(self.skin, theme="Theme-B", icons="Icons-B")
        self.assertEqual(selection.theme.name, "Theme-B")
        self.assertEqual(selection.icons.name, "Icons-B")

    def test_the_cursor_override_is_checked(self):
        with self.assertRaisesRegex(CatalogError, "has no cursors 'Missing'"):
            catalog.resolve_selection(self.skin, cursors="Missing")

    def test_an_unknown_theme_lists_the_choices(self):
        with self.assertRaisesRegex(CatalogError, "choose one of: Theme-A, Theme-B"):
            catalog.resolve_selection(self.skin, theme="Missing")


class BuildThemeTest(CatalogCase):
    def setUp(self):
        super().setUp()
        self.good()
        self.skin = catalog.load(self.paths, "test")

    def test_only_selected_variants_become_assets(self):
        selection = catalog.resolve_selection(self.skin, cursors="Cursor-A")
        theme = catalog.build_theme(self.skin, selection)
        self.assertEqual(theme.name, "test")
        self.assertEqual(
            theme.components,
            {"gtk": "Theme-A", "xfwm": "Theme-A", "icons": "Icons-A", "cursors": "Cursor-A"},
        )
        self.assertEqual(theme.fonts, {})
        kinds = [(asset.kind, asset.target) for asset in theme.assets]
        self.assertEqual(
            kinds,
            [("theme", "Theme-A"), ("icons", "Icons-A"), ("cursors", "Cursor-A"),
             ("wallpaper", "Bubbles.png")],
        )

    def test_install_all_adds_every_variant_but_keeps_the_selection(self):
        selection = catalog.resolve_selection(self.skin)
        theme = catalog.build_theme(self.skin, selection, install_all=True)
        self.assertEqual(theme.components["gtk"], "Theme-A")
        targets = [asset.target for asset in theme.assets]
        self.assertIn("Theme-B", targets)
        self.assertIn("Icons-B", targets)
        self.assertIn("Cursor-A", targets)
        self.assertIn("Font-A", targets)

    def test_sizes_add_up(self):
        selection = catalog.resolve_selection(self.skin)
        self.assertEqual(catalog.group_size(self.skin, selection), 100 + 300 + 75)
        self.assertEqual(catalog.group_size(self.skin, all_variants=True), 100 + 200 + 300 + 400 + 50 + 25 + 75)


if __name__ == "__main__":
    unittest.main()
