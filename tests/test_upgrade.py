"""Tests for desk upgrade: package selection, the upgrade loop, and errors."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import RecordingRunner, Workspace, command_name, make_result
from termux_desk.config import load_paths
from termux_desk.upgrade import (
    UpgradeError,
    parse_upgradable_x11,
    resolve_upgrade_program,
    run_upgrade,
)

SIMULATION_TEXT = """\
Inst libvte [2:0.84.1] (2:0.84.1-1 termux-x11 x11:x11 [aarch64])
Inst zlib [1.3.2] (1.4 termux-main stable:stable [aarch64])
Inst xfce4 [4.20.0] (4.20.1 termux-x11 x11:x11 [all]) []
The following packages have been kept back:
  gsettings-desktop-schemas [51.0] (51.0-1 termux-x11 x11:x11 [all])
"""


class ParseTest(unittest.TestCase):
    def test_only_packages_from_the_x11_origin_are_selected(self):
        self.assertEqual(parse_upgradable_x11(SIMULATION_TEXT), ("libvte", "xfce4"))

    def test_kept_back_packages_are_not_selected(self):
        text = (
            "The following packages have been kept back:\n"
            "  libvte [1] (2 termux-x11 x11:x11 [aarch64])\n"
        )
        self.assertEqual(parse_upgradable_x11(text), ())

    def test_duplicate_lines_are_reported_once(self):
        line = "Inst libvte [1] (2 termux-x11 x11:x11 [aarch64])\n"
        self.assertEqual(parse_upgradable_x11(line + line), ("libvte",))

    def test_empty_output_selects_nothing(self):
        self.assertEqual(parse_upgradable_x11(""), ())


class ResolveUpgradeProgramTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_prefix_bin_is_preferred_over_path(self):
        in_prefix = self.workspace.add_program("apt-get")
        in_path = self.workspace.add_program("apt-get", directory=self.workspace.path)
        found = resolve_upgrade_program(
            "apt-get", self.workspace.paths, self.workspace.environ
        )
        self.assertEqual(found, str(in_prefix))
        self.assertNotEqual(found, str(in_path))

    def test_missing_program_asks_for_a_termux_shell(self):
        with self.assertRaises(UpgradeError) as raised:
            resolve_upgrade_program(
                "apt-get", self.workspace.paths, self.workspace.environ
            )
        self.assertIn("apt-get", str(raised.exception))
        self.assertIn("desk upgrade must run inside Termux", str(raised.exception))


class FakeApt:
    """Answer pkg and apt-get commands and record the upgrade requests."""

    def __init__(self, simulation="", after_install=""):
        self.simulation = simulation
        self.after_install = after_install
        self.installation_done = False
        self.installs: list[tuple[str, ...]] = []
        self.runner = RecordingRunner(self.handle)

    def handle(self, command, **kwargs):
        program = command_name(command)
        if program == "pkg" and command[1] == "update":
            return make_result(command)
        if program == "apt-get" and command[1] == "-s":
            text = self.after_install if self.installation_done else self.simulation
            return make_result(command, stdout=text)
        if program == "pkg" and command[1] == "install":
            self.installation_done = True
            self.installs.append(("pkg", *command[1:]))
            return make_result(command)
        raise AssertionError(f"unexpected command: {command}")


class RunUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program("pkg")
        self.workspace.add_program("apt-get")

    def run_upgrade(self, fake, *, environ=None):
        output = io.StringIO()
        run_upgrade(
            self.workspace.paths,
            fake.runner,
            environ=self.workspace.environ if environ is None else environ,
            out=output,
        )
        return output.getvalue()

    def test_only_the_x11_packages_are_installed(self):
        fake = FakeApt(SIMULATION_TEXT)
        text = self.run_upgrade(fake)
        self.assertEqual(fake.installs, [("pkg", "install", "-y", "libvte", "xfce4")])
        self.assertIn("X11 packages to upgrade: libvte, xfce4", text)
        self.assertIn("The installed X11 packages are up to date.", text)

    def test_the_index_is_refreshed_before_the_simulation(self):
        fake = FakeApt(SIMULATION_TEXT)
        self.run_upgrade(fake)
        calls = [(command_name(command), *command[1:]) for command in fake.runner.calls]
        self.assertEqual(
            [command[0] for command in calls],
            ["pkg", "apt-get", "pkg", "apt-get"],
        )
        self.assertEqual(calls[0], ("pkg", "update", "-y"))

    def test_nothing_to_upgrade_skips_the_install(self):
        fake = FakeApt("Inst zlib [1] (2 termux-main stable:stable [aarch64])\n")
        text = self.run_upgrade(fake)
        self.assertEqual(fake.installs, [])
        self.assertIn("The installed X11 packages are up to date.", text)

    def test_a_package_that_stays_upgradable_is_reported(self):
        fake = FakeApt(SIMULATION_TEXT, after_install=SIMULATION_TEXT)
        with self.assertRaises(UpgradeError) as raised:
            self.run_upgrade(fake)
        self.assertIn("still upgradable", str(raised.exception))
        self.assertIn("libvte", str(raised.exception))

    def test_a_failed_index_refresh_is_reported(self):
        def handler(command, **kwargs):
            return make_result(command, returncode=1)

        fake = FakeApt()
        fake.runner = RecordingRunner(handler)
        with self.assertRaises(UpgradeError) as raised:
            self.run_upgrade(fake)
        self.assertIn("status 1", str(raised.exception))

    def test_a_failed_install_is_reported(self):
        def handler(command, **kwargs):
            program = command_name(command)
            if program == "apt-get":
                return make_result(command, stdout=SIMULATION_TEXT)
            if program == "pkg" and command[1] == "update":
                return make_result(command)
            return make_result(command, returncode=1)

        fake = FakeApt()
        fake.runner = RecordingRunner(handler)
        with self.assertRaises(UpgradeError) as raised:
            self.run_upgrade(fake)
        self.assertIn("pkg install", str(raised.exception))
        self.assertIn("status 1", str(raised.exception))

    def test_a_failed_simulation_is_reported(self):
        def handler(command, **kwargs):
            if command_name(command) == "apt-get":
                return make_result(command, returncode=1)
            return make_result(command)

        fake = FakeApt()
        fake.runner = RecordingRunner(handler)
        with self.assertRaises(UpgradeError) as raised:
            self.run_upgrade(fake)
        self.assertIn("apt-get -s upgrade", str(raised.exception))

    def test_a_missing_apt_get_asks_for_a_termux_shell(self):
        (self.workspace.prefix / "bin" / "apt-get").unlink()
        fake = FakeApt(SIMULATION_TEXT)
        with self.assertRaises(UpgradeError) as raised:
            self.run_upgrade(fake, environ={"PATH": ""})
        self.assertIn("apt-get", str(raised.exception))

    def test_upgrade_requires_an_existing_prefix(self):
        paths = load_paths({"HOME": str(self.workspace.home)})
        with self.assertRaises(UpgradeError) as raised:
            run_upgrade(paths, RecordingRunner(), out=io.StringIO())
        self.assertIn("PREFIX", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
