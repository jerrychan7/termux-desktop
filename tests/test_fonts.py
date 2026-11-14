"""Tests for the font preference that desk setup writes."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import Workspace
from termux_desk.config import DeskError, load_paths
from termux_desk.fonts import (
    DEFAULT_LANGUAGE,
    GENERATED_MARK,
    LEGACY_PREFERENCE_FILE_NAMES,
    PREFERENCE_FILE_NAME,
    build_preference_text,
    choose_families,
    detected_language,
    language_tag,
    preference_path,
    read_system_locale,
    remove_preference,
    run_font_preference,
    run_font_status,
    write_preference,
)


class FakeMatcher:
    """A matcher that answers from a fixed set of family names."""

    def __init__(self, families):
        self.families = set(families)

    def family_exists(self, name):
        return name in self.families


class FakeRun:
    """A subprocess.run replacement that answers per getprop property."""

    def __init__(self, outputs, returncode=0):
        self.outputs = dict(outputs)
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        value = self.outputs.get(command[-1], "")
        return subprocess.CompletedProcess(
            command, self.returncode, stdout=value, stderr=""
        )


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

    def test_the_japanese_language_prefers_the_japanese_family(self):
        matcher = FakeMatcher({"Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Serif CJK JP"})
        self.assertEqual(
            choose_families(matcher, "ja"),
            ("Noto Sans CJK JP", "Noto Serif CJK JP", "Noto Sans CJK JP"),
        )

    def test_the_korean_language_prefers_the_korean_family(self):
        matcher = FakeMatcher({"Noto Sans CJK SC", "Noto Sans CJK KR"})
        self.assertEqual(choose_families(matcher, "ko")[0], "Noto Sans CJK KR")

    def test_the_traditional_chinese_language_prefers_the_tc_family(self):
        matcher = FakeMatcher({"Noto Sans CJK SC", "Noto Sans CJK TC", "OPPO Sans 4.0 TC"})
        self.assertEqual(choose_families(matcher, "zh-Hant")[0], "Noto Sans CJK TC")

    def test_the_hong_kong_language_prefers_the_hk_family(self):
        matcher = FakeMatcher({"Noto Sans CJK TC", "Noto Sans CJK HK"})
        self.assertEqual(choose_families(matcher, "zh-Hant-HK")[0], "Noto Sans CJK HK")


class LanguageTagTest(unittest.TestCase):
    def test_simplified_chinese_tags(self):
        for value in ("zh-Hans-CN", "zh-CN", "zh", "zh_SG"):
            with self.subTest(value=value):
                self.assertEqual(language_tag(value), "zh-Hans")

    def test_traditional_chinese_tags(self):
        for value in ("zh-Hant-TW", "zh-TW", "zh_Hant"):
            with self.subTest(value=value):
                self.assertEqual(language_tag(value), "zh-Hant")

    def test_hong_kong_and_macao_tags(self):
        for value in ("zh-Hant-HK", "zh-HK", "zh-MO"):
            with self.subTest(value=value):
                self.assertEqual(language_tag(value), "zh-Hant-HK")

    def test_japanese_and_korean_tags(self):
        self.assertEqual(language_tag("ja-JP"), "ja")
        self.assertEqual(language_tag("ko-KR"), "ko")
        self.assertEqual(language_tag("en_US.UTF-8"), "en")

    def test_empty_and_invalid_values(self):
        for value in (None, "", "   ", "-"):
            with self.subTest(value=value):
                self.assertIsNone(language_tag(value))


class SystemLocaleTest(unittest.TestCase):
    def test_the_user_locale_property_is_read_first(self):
        run = FakeRun({"persist.sys.locale": "zh-Hans-CN"})
        self.assertEqual(read_system_locale(run=run, command="getprop"), "zh-Hans-CN")
        self.assertEqual(run.calls, [["getprop", "persist.sys.locale"]])

    def test_the_build_locale_is_the_fallback(self):
        run = FakeRun({"ro.product.locale": "ko-KR"})
        self.assertEqual(read_system_locale(run=run, command="getprop"), "ko-KR")
        self.assertEqual(
            run.calls,
            [["getprop", "persist.sys.locale"], ["getprop", "ro.product.locale"]],
        )

    def test_a_missing_getprop_gives_nothing(self):
        run = FakeRun({})
        self.assertIsNone(read_system_locale(run=run, command=None))

    def test_an_os_error_is_ignored(self):
        def run(command, **kwargs):
            raise OSError("no getprop")

        self.assertIsNone(read_system_locale(run=run, command="getprop"))

    def test_detected_language_uses_the_environment_when_getprop_is_empty(self):
        run = FakeRun({})
        self.assertEqual(
            detected_language(run=run, command="getprop", environ={"LANG": "ja_JP.UTF-8"}),
            "ja",
        )

    def test_detected_language_prefers_lc_all(self):
        run = FakeRun({})
        self.assertEqual(
            detected_language(
                run=run,
                command="getprop",
                environ={"LC_ALL": "ko_KR.UTF-8", "LANG": "ja_JP.UTF-8"},
            ),
            "ko",
        )

    def test_detected_language_without_any_source_is_none(self):
        run = FakeRun({})
        self.assertIsNone(detected_language(run=run, command="getprop", environ={}))


ALL_FAMILIES = {
    "zh-Hans": ("Noto Sans CJK SC", "Noto Serif CJK SC", "Sarasa Mono SC"),
    "zh-Hant": ("Noto Sans CJK TC", "Noto Serif CJK TC", "Sarasa Mono TC"),
    "zh-Hant-HK": ("Noto Sans CJK HK", "Noto Serif CJK HK", "Noto Sans Mono CJK HK"),
    "ja": ("Noto Sans CJK JP", "Noto Serif CJK JP", "Sarasa Mono JP"),
    "ko": ("Noto Sans CJK KR", "Noto Serif CJK KR", "Sarasa Mono KR"),
}


class TextTest(unittest.TestCase):
    def test_all_three_aliases_are_written(self):
        text = build_preference_text(
            language="zh-Hans",
            families={
                "zh-Hans": ("Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans CJK SC"),
            },
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
        text = build_preference_text(
            language="zh-Hans",
            families={"zh-Hans": ("Noto Sans CJK SC", None, None)},
        )
        self.assertNotIn("<family>serif</family>", text)
        self.assertNotIn("<family>monospace</family>", text)

    def test_language_rules_are_written_for_other_languages(self):
        text = build_preference_text(language="zh-Hans", families=ALL_FAMILIES)
        self.assertIn('<string>ja</string></test>', text)
        self.assertIn('<string>Noto Sans CJK JP</string></edit>', text)
        self.assertIn('<string>ko</string></test>', text)
        self.assertIn('<string>Noto Sans CJK KR</string></edit>', text)
        self.assertIn('<string>zh-tw</string></test>', text)
        self.assertIn('<string>Noto Sans CJK TC</string></edit>', text)
        self.assertIn('<string>zh-hk</string></test>', text)
        self.assertIn('<string>Noto Sans CJK HK</string></edit>', text)
        self.assertEqual(text.count("<match target=\"pattern\">"), 18)

    def test_a_specific_language_rule_comes_after_the_broader_rule(self):
        text = build_preference_text(language="ja", families=ALL_FAMILIES)
        broad = text.index('<string>zh</string></test>')
        self.assertGreater(text.index('<string>zh-tw</string></test>'), broad)
        self.assertGreater(text.index('<string>zh-hk</string></test>'), broad)

    def test_the_user_language_has_no_rule(self):
        text = build_preference_text(language="ja", families=ALL_FAMILIES)
        self.assertIn(
            "<alias><family>sans-serif</family>"
            "<prefer><family>Noto Sans CJK JP</family></prefer></alias>",
            text,
        )
        self.assertNotIn('<string>ja</string></test>', text)

    def test_a_language_that_was_not_found_contributes_no_rule(self):
        families = {
            "zh-Hans": ("Noto Sans CJK SC", None, None),
            "ja": ("Noto Sans CJK JP", None, None),
        }
        text = build_preference_text(language="zh-Hans", families=families)
        self.assertIn('<string>ja</string></test>', text)
        self.assertNotIn('<string>ko</string></test>', text)
        self.assertNotIn('<string>zh-tw</string></test>', text)

    def test_a_rule_skips_a_missing_serif_and_monospace(self):
        families = {
            "zh-Hans": ("Noto Sans CJK SC", None, None),
            "ja": ("Noto Sans CJK JP", None, None),
        }
        text = build_preference_text(language="zh-Hans", families=families)
        self.assertIn('<string>ja</string></test>', text)
        self.assertEqual(text.count("<family>serif</family>"), 0)
        self.assertEqual(text.count("<family>monospace</family>"), 0)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        self.directory.mkdir(parents=True)
        self.path = preference_path(self.workspace.paths)

    def test_the_preference_file_has_a_language_neutral_name(self):
        self.assertEqual(PREFERENCE_FILE_NAME, "63-cjk-prefer.conf")
        self.assertTrue(str(self.path).endswith("/63-cjk-prefer.conf"))

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
        self.path = self.directory / PREFERENCE_FILE_NAME

    def run_step(self, matcher, locale=None):
        output = io.StringIO()
        run_font_preference(
            self.workspace.paths, out=output, matcher=matcher, locale=locale
        )
        return output.getvalue()

    def test_the_file_is_written_for_the_user_language(self):
        text = self.run_step(
            FakeMatcher(
                {"Noto Sans CJK SC", "Noto Serif CJK SC", "Sarasa Mono SC"}
            ),
            locale="zh-Hans-CN",
        )
        self.assertTrue(self.path.is_file())
        content = self.path.read_text(encoding="utf-8")
        self.assertIn(
            "<family>sans-serif</family><prefer><family>Noto Sans CJK SC</family></prefer>",
            content,
        )
        self.assertIn("Font preference for zh-Hans: Noto Sans CJK SC (sans-serif)", text)
        self.assertIn("Wrote the font preference to", text)
        self.assertIn(str(self.path), text)

    def test_a_japanese_device_gets_the_japanese_family_first(self):
        self.run_step(
            FakeMatcher(
                {
                    "Noto Sans CJK SC",
                    "Noto Sans CJK JP",
                    "Noto Serif CJK JP",
                    "Noto Serif CJK SC",
                }
            ),
            locale="ja-JP",
        )
        content = self.path.read_text(encoding="utf-8")
        self.assertTrue(
            content.startswith(
                '<?xml version="1.0"?>\n'
                '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
                f"<!-- {GENERATED_MARK}."
            )
        )
        self.assertIn(
            "<alias><family>sans-serif</family>"
            "<prefer><family>Noto Sans CJK JP</family></prefer></alias>",
            content,
        )
        self.assertIn('<string>zh</string></test>', content)
        self.assertIn('<string>Noto Sans CJK SC</string></edit>', content)

    def test_a_second_run_reports_the_file_is_already_in_place(self):
        matcher = FakeMatcher({"Noto Sans CJK SC"})
        self.run_step(matcher, locale="zh-Hans-CN")
        text = self.run_step(matcher, locale="zh-Hans-CN")
        self.assertIn("already in place", text)

    def test_a_monospace_face_is_reported(self):
        text = self.run_step(
            FakeMatcher({"Noto Sans CJK SC", "Sarasa Mono SC"}),
            locale="zh-Hans-CN",
        )
        self.assertIn("Sarasa Mono SC (monospace)", text)

    def test_no_font_for_the_user_language_is_a_warning(self):
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}), locale="ja-JP")
        self.assertIn("no ja font was found", text)
        self.assertFalse(self.path.exists())

    def test_no_simplified_chinese_face_is_a_warning(self):
        text = self.run_step(FakeMatcher({"OPPO Serif SC"}), locale="zh-Hans-CN")
        self.assertIn("no Simplified Chinese font was found", text)
        self.assertFalse(self.path.exists())

    def test_a_kept_file_is_reported(self):
        self.path.write_text("other\n", encoding="utf-8")
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}), locale="zh-Hans-CN")
        self.assertIn("was not changed", text)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "other\n")

    def test_a_missing_fontconfig_directory_is_a_warning(self):
        self.directory.rmdir()
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}), locale="zh-Hans-CN")
        self.assertIn("fontconfig directory was not found", text)

    def test_missing_prefix_is_a_warning(self):
        paths = load_paths({"HOME": str(self.workspace.home)})
        output = io.StringIO()
        run_font_preference(
            paths,
            out=output,
            matcher=FakeMatcher({"Noto Sans CJK SC"}),
            locale="zh-Hans-CN",
        )
        self.assertIn("PREFIX is not set", output.getvalue())

    def test_a_legacy_file_with_the_marker_is_removed(self):
        legacy = self.directory / LEGACY_PREFERENCE_FILE_NAMES[0]
        legacy.write_text(f"<!-- {GENERATED_MARK}. -->\n", encoding="utf-8")
        text = self.run_step(FakeMatcher({"Noto Sans CJK SC"}), locale="zh-Hans-CN")
        self.assertFalse(legacy.exists())
        self.assertTrue(self.path.is_file())
        self.assertIn("Removed the old font preference file", text)

    def test_a_legacy_file_with_other_content_is_kept(self):
        legacy = self.directory / LEGACY_PREFERENCE_FILE_NAMES[0]
        legacy.write_text("other\n", encoding="utf-8")
        self.run_step(FakeMatcher({"Noto Sans CJK SC"}), locale="zh-Hans-CN")
        self.assertTrue(legacy.is_file())


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

    def test_a_legacy_file_with_the_marker_is_removed_too(self):
        legacy = self.directory / LEGACY_PREFERENCE_FILE_NAMES[0]
        legacy.write_text(f"<!-- {GENERATED_MARK}. -->\n", encoding="utf-8")
        output = io.StringIO()
        remove_preference(self.workspace.paths, out=output)
        self.assertFalse(legacy.exists())
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


class FontStatusTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        self.directory.mkdir(parents=True)
        self.path = self.directory / PREFERENCE_FILE_NAME

    def status(self, families, *, locale="zh-Hans-CN", present=True, mark=True):
        if present:
            self.path.write_text(
                f"{GENERATED_MARK}\n" if mark else "<fontconfig/>\n",
                encoding="utf-8",
            )
        output = io.StringIO()
        exit_code = run_font_status(
            self.workspace.paths,
            out=output,
            matcher=FakeMatcher(families),
            locale=locale,
        )
        return exit_code, output.getvalue()

    def test_reports_the_file_and_installed_families(self):
        exit_code, output = self.status(
            {"Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans Mono CJK SC"}
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Preference file:", output)
        self.assertIn("(written by desk setup)", output)
        self.assertIn("Detected language: zh-Hans", output)
        self.assertIn("sans-serif: Noto Sans CJK SC", output)
        self.assertIn("serif: Noto Serif CJK SC", output)
        self.assertIn("monospace: Noto Sans Mono CJK SC", output)

    def test_reports_a_missing_file(self):
        exit_code, output = self.status({"Noto Sans CJK SC"}, present=False)

        self.assertEqual(exit_code, 0)
        self.assertIn("(not present)", output)

    def test_keeps_foreign_content_visible(self):
        exit_code, output = self.status({"Noto Sans CJK SC"}, mark=False)

        self.assertIn("(not written by desk setup)", output)

    def test_falls_back_to_the_default_language(self):
        exit_code, output = self.status({"Noto Sans CJK SC"}, locale="en_US.UTF-8")

        self.assertIn("Detected language: en", output)
        self.assertIn("Font preference language: zh-Hans", output)

    def test_reports_that_fontconfig_is_missing(self):
        output = io.StringIO()
        exit_code = run_font_status(self.workspace.paths, out=output, locale="zh-Hans")

        self.assertEqual(exit_code, 0)
        self.assertIn("Font families: not probed", output.getvalue())
