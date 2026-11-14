"""Tests for running the container package manager through desk pkg."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import RecordingRunner, Workspace, make_result

from termux_desk import menu
from termux_desk.container import PROOT_DISTRO_PROGRAM
from termux_desk.pkg import PkgError, run_pkg

UBUNTU_RELEASE = """\
NAME="Ubuntu"
ID=ubuntu
ID_LIKE=debian
"""

ALPINE_RELEASE = """\
NAME="Alpine Linux"
ID=alpine
"""

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Test App
Exec=testapp
"""


class FakePackageManager:
    """Answer proot-distro list, the os-release read, apt, and the menu scans."""

    def __init__(
        self,
        *,
        containers=("ubuntu",),
        os_release=UBUNTU_RELEASE,
        os_release_returncode=0,
        apt_returncode=0,
        entries=None,
    ):
        self.containers = list(containers)
        self.os_release = os_release
        self.os_release_returncode = os_release_returncode
        self.apt_returncode = apt_returncode
        self.entries = entries or {}
        self.apt_calls: list[list[str]] = []
        self.runner = RecordingRunner(self.handle)

    def handle(self, command, **kwargs):
        parts = list(command[1:])
        if parts[0] == "list":
            names = "".join(f"  * {name}\n" for name in self.containers)
            return make_result(
                command,
                stderr=f"\nInstalled containers:\n\n{names}\nLog in with: x\n",
            )
        inside = parts[parts.index("--") + 1 :]
        program = inside[0]
        if program == "cat":
            return make_result(
                command,
                returncode=self.os_release_returncode,
                stdout=self.os_release,
            )
        if program == "apt":
            self.apt_calls.append(inside)
            return make_result(command, returncode=self.apt_returncode)
        if program == "sh":
            container = parts[1]
            text = "".join(
                f"\x1e{name}\x1e\n{content}\n"
                for name, content in self.entries.get(container, {}).items()
            )
            return make_result(command, stdout=text)
        raise AssertionError(f"unexpected command: {command}")


class RunPkgTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)

    def run_package_manager(self, fake, container="ubuntu", arguments=("install", "gimp")):
        output = io.StringIO()
        exit_code = run_pkg(
            self.workspace.paths,
            container,
            list(arguments),
            runner=fake.runner,
            environ=self.workspace.environ,
            out=output,
        )
        return exit_code, output.getvalue()

    def test_arguments_are_forwarded_unchanged(self):
        fake = FakePackageManager()
        arguments = ("install", "gimp", "-y", "--no-install-recommends")
        exit_code, output = self.run_package_manager(fake, arguments=arguments)
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            fake.apt_calls,
            [["apt", "install", "gimp", "-y", "--no-install-recommends"]],
        )
        self.assertIn(
            "Running in ubuntu: apt install gimp -y --no-install-recommends", output
        )

    def test_desk_adds_no_flags_of_its_own(self):
        fake = FakePackageManager()
        self.run_package_manager(fake, arguments=("install", "gimp"))
        self.assertEqual(fake.apt_calls, [["apt", "install", "gimp"]])

    def test_add_and_rm_are_shortcuts(self):
        fake = FakePackageManager()
        self.run_package_manager(fake, arguments=("add", "gimp"))
        self.run_package_manager(fake, arguments=("rm", "gimp"))
        self.assertEqual(
            fake.apt_calls,
            [["apt", "install", "gimp"], ["apt", "remove", "gimp"]],
        )

    def test_the_menu_is_refreshed_after_a_successful_run(self):
        fake = FakePackageManager(
            entries={"ubuntu": {"test.desktop": DESKTOP_ENTRY}}
        )
        exit_code, output = self.run_package_manager(fake)
        self.assertEqual(exit_code, 0)
        self.assertIn("Menu entries for ubuntu: 1", output)
        menu_dir = self.workspace.paths.menu_dir
        self.assertTrue((menu_dir / "desk-ubuntu-test.desktop").is_file())

    def test_the_menu_is_refreshed_even_when_the_manager_fails(self):
        fake = FakePackageManager(apt_returncode=100)
        exit_code, output = self.run_package_manager(fake)
        self.assertEqual(exit_code, 100)
        self.assertIn("Scanned 1 containers", output)
        self.assertIn("Menu entries for ubuntu: 0", output)

    def test_menu_entries_are_counted_per_container(self):
        fake = FakePackageManager(
            containers=("ubuntu", "ubuntu-desktop-test"),
            entries={
                "ubuntu": {"test.desktop": DESKTOP_ENTRY},
                "ubuntu-desktop-test": {"test.desktop": DESKTOP_ENTRY},
            },
        )
        exit_code, output = self.run_package_manager(fake)
        self.assertEqual(exit_code, 0)
        self.assertIn("Menu entries for ubuntu: 1", output)

    def test_menu_failure_is_a_warning(self):
        fake = FakePackageManager()
        failure = menu.MenuError("the menu could not be written")
        with mock.patch.object(menu, "run_refresh", side_effect=failure):
            exit_code, output = self.run_package_manager(fake)
        self.assertEqual(exit_code, 0)
        self.assertIn("the menu was not refreshed", output)

    def test_missing_container_is_reported_with_the_init_hint(self):
        fake = FakePackageManager(containers=("alpine",))
        with self.assertRaises(PkgError) as raised:
            self.run_package_manager(fake)
        self.assertIn("desk container init ubuntu", str(raised.exception))
        self.assertEqual(fake.apt_calls, [])

    def test_invalid_container_name_is_rejected(self):
        fake = FakePackageManager()
        with self.assertRaises(PkgError):
            self.run_package_manager(fake, container="--help")

    def test_empty_arguments_are_rejected(self):
        fake = FakePackageManager()
        with self.assertRaises(PkgError) as raised:
            self.run_package_manager(fake, arguments=())
        self.assertIn("desk pkg ubuntu install gimp", str(raised.exception))

    def test_unknown_family_is_reported(self):
        fake = FakePackageManager(
            containers=("alpine",), os_release=ALPINE_RELEASE
        )
        with self.assertRaises(PkgError) as raised:
            self.run_package_manager(fake, container="alpine")
        self.assertIn("alpine", str(raised.exception))
        self.assertEqual(fake.apt_calls, [])

    def test_os_release_failure_is_reported(self):
        fake = FakePackageManager(os_release_returncode=1)
        with self.assertRaises(PkgError) as raised:
            self.run_package_manager(fake)
        self.assertIn("/etc/os-release", str(raised.exception))

    def test_missing_proot_distro_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        fake = FakePackageManager()
        with self.assertRaises(PkgError) as raised:
            self.run_package_manager(fake)
        self.assertIn("proot-distro", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
