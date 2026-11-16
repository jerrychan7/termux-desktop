"""Tests for the optional ibus input method support."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from termux_desk import ime
from termux_desk.config import load_paths

HOST_ENVIRONMENT = {
    "GTK_IM_MODULE": "ibus",
    "QT_IM_MODULE": "ibus",
    "XMODIFIERS": "@im=ibus",
}
CONTAINER_ENVIRONMENT = (("GTK_IM_MODULE", "xim"), ("XMODIFIERS", "@im=ibus"))


class ImeTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.prefix = self.root / "usr"
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "libexec").mkdir()
        self.environ = {
            "HOME": str(self.root / "home"),
            "PREFIX": str(self.prefix),
            "PATH": "",
        }

    def paths(self):
        return load_paths(self.environ)

    def install(self):
        daemon = self.prefix / "bin" / "ibus-daemon"
        daemon.write_text("#!/bin/sh\n", encoding="utf-8")
        daemon.chmod(0o755)
        panel = self.prefix / "libexec" / "ibus-ui-gtk3"
        panel.write_text("#!/bin/sh\n", encoding="utf-8")
        panel.chmod(0o755)

    def test_missing_packages_are_not_available(self):
        paths = self.paths()
        self.assertFalse(ime.ime_available(paths))
        self.assertEqual(ime.host_environment(paths), {})
        self.assertEqual(ime.container_environment(paths), ())
        self.assertEqual(
            ime.xfce_launch_command(paths),
            "dbus-launch --exit-with-session startxfce4",
        )

    def test_panel_without_daemon_is_not_available(self):
        (self.prefix / "libexec" / "ibus-ui-gtk3").write_text("", encoding="utf-8")
        self.assertFalse(ime.ime_available(self.paths()))

    def test_daemon_without_panel_is_not_available(self):
        daemon = self.prefix / "bin" / "ibus-daemon"
        daemon.write_text("", encoding="utf-8")
        daemon.chmod(0o755)
        self.assertFalse(ime.ime_available(self.paths()))

    def test_installed_ime_exposes_the_host_environment(self):
        self.install()
        self.assertEqual(ime.host_environment(self.paths()), HOST_ENVIRONMENT)

    def test_installed_ime_exposes_the_container_environment(self):
        self.install()
        self.assertEqual(ime.container_environment(self.paths()), CONTAINER_ENVIRONMENT)

    def test_launch_command_starts_the_daemon_with_the_panel(self):
        self.install()
        command = ime.xfce_launch_command(self.paths())
        self.assertTrue(command.startswith("dbus-launch --exit-with-session sh -c "))
        self.assertIn("ibus-daemon -drx --panel=", command)
        self.assertIn(str(self.prefix / "libexec" / "ibus-ui-gtk3"), command)
        self.assertIn("startxfce4", command)

    def test_missing_prefix_is_not_available(self):
        paths = load_paths({"HOME": str(self.root / "home")})
        self.assertFalse(ime.ime_available(paths))


if __name__ == "__main__":
    unittest.main()


class ConfiguredModeTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.prefix = self.root / "usr"
        (self.prefix / "bin").mkdir(parents=True)
        (self.prefix / "libexec").mkdir()
        for name in ("ibus-daemon",):
            program = self.prefix / "bin" / name
            program.write_text("#!/bin/sh\n", encoding="utf-8")
            program.chmod(0o755)
        panel = self.prefix / "libexec" / "ibus-ui-gtk3"
        panel.write_text("#!/bin/sh\n", encoding="utf-8")
        panel.chmod(0o755)
        self.environ = {
            "HOME": str(self.root / "home"),
            "PREFIX": str(self.prefix),
            "PATH": "",
        }

    def paths(self):
        return load_paths(self.environ)

    def write_config(self, text):
        from termux_desk.settings import config_path

        path = config_path(self.paths(), self.environ)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_installed_and_auto_enables_the_input_method(self):
        self.assertTrue(ime.enabled(self.paths(), self.environ))

    def test_false_disables_an_installed_input_method(self):
        self.write_config("version: 1\nime:\n  enabled: false\n")
        self.assertFalse(ime.enabled(self.paths(), self.environ))
        self.assertEqual(ime.host_environment(self.paths(), self.environ), {})

    def test_true_uses_the_installed_input_method(self):
        self.write_config("version: 1\nime:\n  enabled: true\n")
        self.assertTrue(ime.enabled(self.paths(), self.environ))

    def test_a_broken_configuration_falls_back_to_auto(self):
        self.write_config("version: 1\nime:\n  enabled: sometimes\n")
        self.assertTrue(ime.enabled(self.paths(), self.environ))
