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


class TemplateTest(unittest.TestCase):
    def setUp(self):
        self.template = (REPOSITORY_ROOT / "templates" / "session.sh").read_text(encoding="utf-8")

    def test_template_shebang_is_filled_with_the_prefix_bash(self):
        first_line = self.template.splitlines()[0]
        self.assertEqual(first_line, "#!@BASH@")

    def test_template_starts_the_expected_parts(self):
        self.assertIn("termux-x11 \"$DISPLAY\"", self.template)
        self.assertIn("am start --user 0 -n @TERMUX_X11_ACTIVITY@", self.template)
        self.assertIn("dbus-launch --exit-with-session startxfce4", self.template)
        self.assertIn("XDG_DATA_DIRS", self.template)

    def test_template_keeps_a_trap_instead_of_exec(self):
        self.assertIn("trap 'stop_termux_x11' EXIT", self.template)
        for line in self.template.splitlines():
            self.assertFalse(line.strip().startswith("exec "), "the template must not use exec")

    def test_template_sets_the_x11_environment(self):
        self.assertIn("XDG_RUNTIME_DIR", self.template)
        self.assertIn("XDG_CONFIG_DIRS", self.template)
        self.assertIn("DISPLAY", self.template)

    def test_template_starts_no_extra_service(self):
        for word in ("pulseaudio", "vnc", "termux-wake-lock", "systemctl", "wayland"):
            self.assertNotIn(word, self.template.lower())


class EntryTest(unittest.TestCase):
    def test_entry_shebang_is_the_termux_python(self):
        first_line = (REPOSITORY_ROOT / "desk").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "#!/data/data/com.termux/files/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
