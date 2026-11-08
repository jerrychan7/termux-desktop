"""Tests for the path and constant definitions."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from termux_desk import config


class PathsTest(unittest.TestCase):
    def test_paths_follow_home_and_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            prefix = root / "usr"
            paths = config.load_paths({"HOME": str(home), "PREFIX": str(prefix)})

            self.assertEqual(paths.home, home)
            self.assertEqual(paths.prefix, prefix)
            self.assertEqual(paths.state_dir, home / ".local" / "state" / "termux-desktop")
            self.assertEqual(paths.session_file, paths.state_dir / "session.json")
            self.assertEqual(paths.desk_log, paths.state_dir / "desk.log")
            self.assertEqual(paths.session_log, paths.state_dir / "session.log")
            self.assertEqual(paths.session_script, paths.state_dir / "session.sh")
            self.assertEqual(paths.prefix_tmp, prefix / "tmp")
            self.assertEqual(paths.runtime_dir, prefix / "tmp")
            self.assertEqual(paths.system_config_dir, prefix / "etc" / "xdg")
            self.assertEqual(paths.x11_socket_dir, prefix / "tmp" / ".X11-unix")
            self.assertEqual(paths.x11_socket, prefix / "tmp" / ".X11-unix" / "X0")
            self.assertEqual(paths.x11_lock, prefix / "tmp" / ".X0-lock")

    def test_default_template_is_in_the_repository(self):
        paths = config.load_paths({"HOME": "/tmp/desk-home", "PREFIX": "/tmp/desk-usr"})
        self.assertEqual(paths.template_file.name, "session.sh")
        self.assertEqual(paths.template_file.parent.name, "templates")
        self.assertTrue(paths.template_file.is_file())

    def test_missing_prefix_leaves_x11_paths_unavailable(self):
        paths = config.load_paths({"HOME": "/tmp/desk-home"})
        self.assertIsNone(paths.prefix)
        with self.assertRaises(config.ConfigError):
            _ = paths.x11_socket

    def test_missing_home_is_reported(self):
        with self.assertRaises(config.ConfigError) as raised:
            config.load_paths({})
        self.assertIn("HOME", str(raised.exception))

    def test_empty_prefix_is_treated_as_missing(self):
        paths = config.load_paths({"HOME": "/tmp/desk-home", "PREFIX": "  "})
        self.assertIsNone(paths.prefix)


class ConstantsTest(unittest.TestCase):
    def test_display_is_zero(self):
        self.assertEqual(config.DISPLAY_NAME, ":0")

    def test_fixed_package_list_is_the_m0_list(self):
        self.assertEqual(
            config.FIXED_PACKAGES,
            ("x11-repo", "termux-x11-nightly", "xfce4", "dbus", "termux-am"),
        )
        self.assertEqual(config.REPO_PACKAGE, "x11-repo")

    def test_termux_x11_activity_and_download_url(self):
        self.assertEqual(config.TERMUX_X11_ACTIVITY, "com.termux.x11/com.termux.x11.MainActivity")
        self.assertTrue(config.TERMUX_X11_DOWNLOAD_URL.startswith("https://"))


class ResolveProgramTest(unittest.TestCase):
    def test_prefix_bin_is_checked_before_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "usr"
            (prefix / "bin").mkdir(parents=True)
            program = prefix / "bin" / "termux-x11"
            program.write_text("#!/bin/sh\n", encoding="utf-8")
            program.chmod(0o755)
            path_directory = root / "path"
            path_directory.mkdir()
            other = path_directory / "termux-x11"
            other.write_text("#!/bin/sh\n", encoding="utf-8")
            other.chmod(0o755)

            found = config.resolve_program("termux-x11", prefix, str(path_directory))
            self.assertEqual(found, str(program))

    def test_path_is_used_when_the_prefix_has_no_program(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path_directory = root / "path"
            path_directory.mkdir()
            program = path_directory / "am"
            program.write_text("#!/bin/sh\n", encoding="utf-8")
            program.chmod(0o755)

            found = config.resolve_program("am", root / "usr", str(path_directory))
            self.assertEqual(found, str(program))

    def test_missing_program_returns_none(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path_directory = root / "path"
            path_directory.mkdir()
            self.assertIsNone(config.resolve_program("dbus-launch", None, str(path_directory)))

    def test_program_without_execute_permission_is_not_found(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = root / "usr"
            (prefix / "bin").mkdir(parents=True)
            program = prefix / "bin" / "startxfce4"
            program.write_text("#!/bin/sh\n", encoding="utf-8")
            program.chmod(0o644)
            self.assertIsNone(config.resolve_program("startxfce4", prefix, None))


if __name__ == "__main__":
    unittest.main()
