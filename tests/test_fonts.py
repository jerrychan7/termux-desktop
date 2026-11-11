"""Tests for the Simplified Chinese font preference that desk setup writes."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import Workspace
from termux_desk.config import DeskError, load_paths
from termux_desk.fonts import (
    GENERATED_MARK,
    PREFERENCE_FILE_NAME,
    build_preference_text,
    choose_families,
    preference_path,
    remove_preference,
    run_font_preference,
    write_preference,
)


class FakeMatcher:
    """A matcher that answers from a fixed set of family names."""

    def __init__(self, families):
        self.families = set(families)

    def family_exists(self, name):
        return name in self.families


class ChooseTest(unittest.TestCase):
    def test_noto_is_used_when_it_is_installed(self):
        matcher = FakeMatcher({"Noto Sans CJK SC"})
        self.assertEqual(choose_families(matcher), ("Noto Sans CJK SC", None, "Noto Sans CJK SC"))

    def test_a_vendor_family_is_used_when_noto_is_missing(self):
        matcher = FakeMatcher({"OPPO Sans 4.0 SC", "OPPO Serif SC"})
        self.assertEqual(
            choose_families(matcher),
            ("OPPO Sans 4.0 SC", "OPPO Serif SC", "OPPO Sans 4.0 SC"),
        )

    def test_noto_is_checked_before_a_vendor_family(self):
        matcher = FakeMatcher({"Noto Sans CJK SC", "OPPO Sans 4.0 SC"})
        self.assertEqual(
            choose_families(matcher)[0],
            "Noto Sans CJK SC",
        )

    def test_a_monospace_family_is_used_when_it_is_installed(self):
        matcher = FakeMatcher({"Noto Sans CJK SC", "Sarasa Mono SC"})
        self.assertEqual(
            choose_families(matcher),
            ("Noto Sans CJK SC", None, "Sarasa Mono SC"),
        )

    def test_no_family_installed_gives_nothing(self):
        self.assertEqual(choose_families(FakeMatcher(set())), (None, None, None))


class TextTest(unittest.TestCase):
    def test_all_three_aliases_are_written(self):
        text = build_preference_text(
            sans="Noto Sans CJK SC",
            serif="Noto Serif CJK SC",
            mono="Noto Sans CJK SC",
        )
        self.assertIn(GENERATED_MARK, text)
        self.assertIn(
            "<alias><family>sans-serif</family>"
            "<prefer><family>Noto Sans CJK SC</family></prefer></alias>",
            text,
        )
        self.assertIn(
            "<alias><family>serif</family>"
            "<prefer><family>Noto Serif CJK SC</family></prefer></alias>",
            text,
        )
        self.assertIn(
            "<alias><family>monospace</family>"
            "<prefer><family>Noto Sans CJK SC</family></prefer></alias>",
            text,
        )
        self.assertTrue(text.endswith("</fontconfig>\n"))

    def test_a_missing_serif_or_monospace_is_not_written(self):
        text = build_preference_text(sans="Noto Sans CJK SC", serif=None, mono=None)
        self.assertNotIn("<family>serif</family>", text)
        self.assertNotIn("<family>monospace</family>", text)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        self.directory.mkdir(parents=True)
        self.path = preference_path(self.workspace.paths)

    def test_a_new_file_is_written(self):
        status = write_preference(self.workspace.paths, "text\n")
        self.assertEqual(status, "written")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "text\n")

    def test_writing_the_same_text_again_changes_nothing(self):
        write_preference(self.workspace.paths, "text\n")
        self.assertEqual(write_preference(self.workspace.paths, "text\n"), "unchanged")

    def test_a_file_with_other_content_is_kept(self):
        self.path.write_text("other\n", encoding="utf-8")
        self.assertEqual(write_preference(self.workspace.paths, "text\n"), "kept")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "other\n")

    def test_a_generated_file_with_other_content_is_replaced(self):
        self.path.write_text(f"<!-- {GENERATED_MARK}. -->\nold\n", encoding="utf-8")
        self.assertEqual(write_preference(self.workspace.paths, "text\n"), "written")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "text\n")

    def test_a_missing_directory_is_reported(self):
        self.directory.rmdir()
        self.assertEqual(write_preference(self.workspace.paths, "text\n"), "missing-directory")


class RunFontPreferenceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        self.directory.mkdir(parents=True)

    def run_step(self, matcher):
        output = io.StringIO()
        run_font_preference(self.workspace.paths, out=output, matcher=matcher)
        return output.getvalue()

    def test_the_file_is_written_and_reported(self):
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC", "Noto Serif CJK SC"}))
        path = self.directory / PREFERENCE_FILE_NAME
        self.assertTrue(path.is_file())
        content = path.read_text(encoding="utf-8")
        self.assertIn("<family>sans-serif</family><prefer><family>Noto Sans CJK SC</family></prefer>", content)
        self.assertIn("Simplified Chinese font preference: Noto Sans CJK SC (sans-serif)", text)
        self.assertIn("Wrote the font preference to", text)
        self.assertIn(str(path), text)

    def test_a_second_run_reports_the_file_is_already_in_place(self):
        self.run_step(FakeMatcher({"Noto Sans CJK SC"}))
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}))
        self.assertIn("already in place", text)

    def test_a_monospace_face_is_reported(self):
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC", "Sarasa Mono SC"}))
        self.assertIn("Sarasa Mono SC (monospace)", text)

    def test_no_simplified_chinese_face_is_a_warning(self):
        text = self.run_step(FakeMatcher({"OPPO Serif SC"}))
        self.assertIn("no Simplified Chinese font was found", text)
        self.assertFalse((self.directory / PREFERENCE_FILE_NAME).exists())

    def test_a_kept_file_is_reported(self):
        path = self.directory / PREFERENCE_FILE_NAME
        path.write_text("other\n", encoding="utf-8")
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}))
        self.assertIn("was not changed", text)
        self.assertEqual(path.read_text(encoding="utf-8"), "other\n")

    def test_a_missing_fontconfig_directory_is_a_warning(self):
        self.directory.rmdir()
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}))
        self.assertIn("fontconfig directory was not found", text)

    def test_missing_prefix_is_a_warning(self):
        paths = load_paths({"HOME": str(self.workspace.home)})
        output = io.StringIO()
        run_font_preference(paths, out=output, matcher=FakeMatcher({"Noto Sans CJK SC"}))
        self.assertIn("PREFIX is not set", output.getvalue())


class RemovePreferenceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        self.directory.mkdir(parents=True)
        self.path = self.directory / PREFERENCE_FILE_NAME

    def test_a_generated_file_is_removed(self):
        self.path.write_text(f"<!-- {GENERATED_MARK}. -->\n", encoding="utf-8")
        output = io.StringIO()
        remove_preference(self.workspace.paths, out=output)
        self.assertFalse(self.path.exists())
        self.assertIn("Removed the font preference file at", output.getvalue())

    def test_a_missing_file_is_not_an_error(self):
        output = io.StringIO()
        remove_preference(self.workspace.paths, out=output)
        self.assertIn("There is no font preference file at", output.getvalue())

    def test_a_file_with_other_content_is_kept(self):
        self.path.write_text("other\n", encoding="utf-8")
        output = io.StringIO()
        remove_preference(self.workspace.paths, out=output)
        self.assertTrue(self.path.exists())
        self.assertIn("was not written by desk setup", output.getvalue())

    def test_missing_prefix_is_an_error(self):
        paths = load_paths({"HOME": str(self.workspace.home)})
        with self.assertRaises(DeskError) as raised:
            remove_preference(paths, out=io.StringIO())
        self.assertIn("PREFIX", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
