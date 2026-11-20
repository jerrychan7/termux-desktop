"""Tests that guard the fixed configuration against accidental drift."""

from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRECTORIES = (REPOSITORY_ROOT / "src", REPOSITORY_ROOT / "templates")
ENTRY_FILES = (REPOSITORY_ROOT / "desk",)


def source_files():
    """Return every desk source file that these tests inspect."""
    files = list(ENTRY_FILES)
    for directory in SOURCE_DIRECTORIES:
        files.extend(path for path in sorted(directory.rglob("*")) if path.is_file())
    return [path for path in files if path.suffix in (".py", ".sh", "") or path.name == "desk"]


class SourceScanTest(unittest.TestCase):
    def test_sources_never_use_pkill(self):
        for path in source_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("pkill", text, f"pkill appears in {path}")

    def test_sources_never_touch_shell_startup_files(self):
        for path in source_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".bashrc", text, f".bashrc appears in {path}")
            self.assertNotIn(".zshrc", text, f".zshrc appears in {path}")

    def test_setup_never_runs_a_package_upgrade(self):
        for path in source_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"pkg", "upgrade"', text)
            self.assertNotIn("pkg upgrade", text)

    def test_sources_hold_no_hardcoded_root_home(self):
        for path in source_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("'/root", text, f"a hardcoded root path appears in {path}")
            self.assertNotIn('"/root', text, f"a hardcoded root path appears in {path}")


class ReleaseTest(unittest.TestCase):
    def test_the_version_appears_consistently(self):
        package = REPOSITORY_ROOT / "src" / "termux_desk" / "__init__.py"
        version = ""
        for line in package.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                version = line.split("=", 1)[1].strip().strip('"')
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"当前是 `{version}`", readme)
        roadmap = (REPOSITORY_ROOT / "docs" / "roadmap.md").read_text(encoding="utf-8")
        released = [
            line
            for line in roadmap.splitlines()
            if line.startswith("- ") and f"{version}" in line and not line.endswith("已发布")
        ]
        self.assertTrue(
            any(line.startswith(f"- {version}：") for line in released),
            f"the roadmap has no completed entry for {version}",
        )
        self.assertNotIn(f"{version}（未发布）", roadmap)


class PresetTest(unittest.TestCase):
    def test_the_shipped_presets_exist(self):
        package = REPOSITORY_ROOT / "src" / "termux_desk"
        self.assertTrue((package / "presets" / "appearance" / "stock.yaml").is_file())
        self.assertTrue((package / "presets" / "layout" / "compact.yaml").is_file())

    def test_the_skin_catalog_is_shipped(self):
        package = REPOSITORY_ROOT / "src" / "termux_desk" / "presets"
        for skin, layout in (("nord", "panel-nord"), ("materia", "panel-materia"), ("win95", "panel-win95")):
            self.assertTrue((package / "skins" / skin / "skin.yaml").is_file(), skin)
            self.assertTrue((package / "layout" / f"{layout}.yaml").is_file(), layout)
        for build in ("materia", "tela"):
            self.assertTrue((package / "builds" / f"{build}.yaml").is_file(), build)


class LicenseTest(unittest.TestCase):
    def test_the_repository_ships_the_mit_license(self):
        text = (REPOSITORY_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 jerrychan7", text)

    def test_the_readme_points_at_the_license(self):
        text = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[LICENSE](LICENSE)", text)


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.template = (REPOSITORY_ROOT / "templates" / "session.sh").read_text(encoding="utf-8")

    def test_template_shebang_is_filled_with_the_prefix_bash(self):
        first_line = self.template.splitlines()[0]
        self.assertEqual(first_line, "#!@BASH@")

    def test_template_starts_the_expected_parts(self):
        self.assertIn("termux-x11 \"$DISPLAY\"", self.template)
        self.assertIn("am start --user 0 -n @TERMUX_X11_ACTIVITY@", self.template)
        self.assertIn("@XFCE_LAUNCH@", self.template)
        self.assertIn("XDG_DATA_DIRS", self.template)

    def test_template_keeps_a_trap_instead_of_exec(self):
        self.assertIn("trap 'stop_x_server' EXIT", self.template)
        for line in self.template.splitlines():
            self.assertFalse(line.strip().startswith("exec "), "the template must not use exec")

    def test_template_sets_the_x11_environment(self):
        self.assertIn("XDG_RUNTIME_DIR", self.template)
        self.assertIn("XDG_CONFIG_DIRS", self.template)
        self.assertIn("DISPLAY", self.template)

    def test_template_starts_no_extra_service(self):
        for word in ("pulseaudio", "termux-wake-lock", "systemctl", "wayland"):
            self.assertNotIn(word, self.template.lower())


class EntryTest(unittest.TestCase):
    def test_entry_shebang_is_the_termux_python(self):
        first_line = (REPOSITORY_ROOT / "desk").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "#!/data/data/com.termux/files/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
