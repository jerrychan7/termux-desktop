"""Tests for container creation and preparation."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import RecordingRunner, Workspace, make_result
from termux_desk.container import (
    DEFAULT_IMAGE,
    MOUNT_POINTS,
    PROOT_DISTRO_PROGRAM,
    ContainerError,
    container_command,
    family_for,
    install_packages,
    parse_container_list,
    parse_installed_packages,
    parse_os_release,
    run_container_init,
)

UBUNTU_RELEASE = """\
PRETTY_NAME="Ubuntu 26.04.1 LTS"
NAME="Ubuntu"
ID=ubuntu
ID_LIKE=debian
"""

ALPINE_RELEASE = """\
NAME="Alpine Linux"
ID=alpine
VERSION_ID=3.24.3
"""

LIST_WITH_UBUNTU = """
Installed containers:

  * alpine
  * ubuntu

Log in with: proot-distro login <name>
"""

LIST_WITHOUT_CONTAINERS = """
No containers installed.

Install a container with: proot-distro install <image>
"""


class FakeContainers:
    """Answer proot-distro calls and record them."""

    def __init__(
        self,
        *,
        containers=("ubuntu",),
        os_release=UBUNTU_RELEASE,
        installed=(),
        fail=(),
        verify_ok=True,
        list_on_stderr=False,
    ):
        self.containers = list(containers)
        self.os_release = os_release
        self.installed = set(installed)
        self.fail = set(fail)
        self.verify_ok = verify_ok
        self.list_on_stderr = list_on_stderr
        self.runner = RecordingRunner(self.handle)

    def handle(self, command, **kwargs):
        parts = list(command[1:])
        if parts[0] == "list":
            names = "".join(f"  * {name}\n" for name in self.containers)
            text = f"\nInstalled containers:\n\n{names}\nLog in with: x\n"
            if self.list_on_stderr:
                return make_result(command, stderr=text)
            return make_result(command, stdout=text)
        if parts[0] == "install":
            if "install" in self.fail:
                return make_result(command, returncode=1)
            name = parts[parts.index("--name") + 1]
            self.containers.append(name)
            return make_result(command)
        if parts[0] != "sh":
            raise AssertionError(f"unexpected command: {command}")
        inside = parts[parts.index("--") + 1 :]
        program = inside[0]
        if program == "cat":
            return make_result(command, stdout=self.os_release)
        if program in self.fail:
            return make_result(command, returncode=1)
        if program == "mkdir":
            return make_result(command)
        if program == "dpkg-query":
            lines = "".join(
                f"{package}=install ok installed\n"
                for package in inside[3:]
                if package in self.installed
            )
            return make_result(command, stdout=lines, returncode=0 if lines else 1)
        if program == "apt-get":
            if inside[1] == "install":
                self.installed.update(
                    name for name in inside[2:] if not name.startswith("-")
                )
            return make_result(command)
        if program == "sh" and inside[1] == "-c":
            return make_result(command, returncode=0 if self.verify_ok else 1)
        raise AssertionError(f"unexpected command: {command}")

    def install_calls(self):
        return [call for call in self.runner.calls if call[1] == "install"]

    def inside_calls(self, program):
        """Return the arguments after '--' for container commands."""
        calls = []
        for call in self.runner.calls:
            parts = list(call[1:])
            if parts[0] != "sh" or program not in parts:
                continue
            calls.append(parts[parts.index("--") + 1 :])
        return calls


class ParseTest(unittest.TestCase):
    def test_container_list_reads_the_installed_section(self):
        self.assertEqual(
            parse_container_list(LIST_WITH_UBUNTU), ("alpine", "ubuntu")
        )

    def test_container_list_without_containers_is_empty(self):
        self.assertEqual(parse_container_list(LIST_WITHOUT_CONTAINERS), ())
        self.assertEqual(parse_container_list(""), ())

    def test_os_release_values_are_unquoted(self):
        facts = parse_os_release(UBUNTU_RELEASE)
        self.assertEqual(facts["ID"], "ubuntu")
        self.assertEqual(facts["ID_LIKE"], "debian")
        self.assertEqual(facts["PRETTY_NAME"], "Ubuntu 26.04.1 LTS")

    def test_family_uses_id_then_id_like(self):
        self.assertEqual(family_for(parse_os_release(UBUNTU_RELEASE)), "debian")
        self.assertIsNone(family_for(parse_os_release(ALPINE_RELEASE)))
        self.assertEqual(
            family_for({"ID": "linuxmint", "ID_LIKE": "ubuntu debian"}), "debian"
        )

    def test_installed_packages_reads_the_status_line(self):
        output = "xterm=install ok installed\nxfonts-base=deinstall ok config-files\n"
        self.assertEqual(parse_installed_packages(output), frozenset({"xterm"}))

    def test_container_command_is_isolated(self):
        command = container_command("pd", "ubuntu", "mkdir", "-p", "/termux")
        self.assertEqual(
            command, ("pd", "sh", "ubuntu", "--isolated", "--", "mkdir", "-p", "/termux")
        )

    def test_container_command_passes_environment(self):
        command = container_command(
            "pd", "ubuntu", "apt-get", "update", environment=(("LANG", "C"),)
        )
        self.assertEqual(
            command,
            ("pd", "sh", "ubuntu", "--isolated", "-e", "LANG=C", "--", "apt-get", "update"),
        )


class InitTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)

    def run_init(self, fake, name="ubuntu-desktop-test", **kwargs):
        output = io.StringIO()
        kwargs.setdefault("image", DEFAULT_IMAGE)
        run_container_init(
            self.workspace.paths,
            name,
            runner=fake.runner,
            environ=self.workspace.environ,
            out=output,
            **kwargs,
        )
        return output.getvalue()

    def test_new_container_is_created_and_prepared(self):
        fake = FakeContainers()
        output = self.run_init(fake)

        self.assertEqual(len(fake.install_calls()), 1)
        install = fake.install_calls()[0]
        self.assertEqual(install[2], DEFAULT_IMAGE)
        self.assertEqual(install[install.index("--name") + 1], "ubuntu-desktop-test")
        self.assertEqual(
            fake.inside_calls("mkdir"), [["mkdir", "-p", *MOUNT_POINTS]]
        )
        self.assertEqual(len(fake.inside_calls("apt-get")), 2)
        self.assertEqual(fake.inside_calls("apt-get")[0], ["apt-get", "update", "-qq"])
        install_inside = fake.inside_calls("apt-get")[1]
        self.assertIn("--no-install-recommends", install_inside)
        self.assertIn("xterm", install_inside)
        self.assertIn("Creating the container", output)
        self.assertIn("ready", output)

    def test_existing_container_is_not_created_again(self):
        fake = FakeContainers()
        self.run_init(fake)
        self.run_init(fake)
        self.assertEqual(len(fake.install_calls()), 1)

    def test_container_list_on_stderr_is_still_read(self):
        fake = FakeContainers(containers=("ubuntu-desktop-test",), list_on_stderr=True)
        output = self.run_init(fake)
        self.assertEqual(fake.install_calls(), [])
        self.assertIn("already exists", output)

    def test_installed_packages_skip_the_package_manager(self):
        fake = FakeContainers()
        self.run_init(fake)
        fake.runner.calls.clear()
        fake.installed.update(
            {"xterm", "x11-utils", "xfonts-base", "fonts-dejavu-core"}
        )
        output = self.run_init(fake)
        self.assertEqual(fake.inside_calls("apt-get"), [])
        self.assertIn("already installed", output)

    def test_unknown_distribution_stops_before_the_mount_points(self):
        fake = FakeContainers(os_release=ALPINE_RELEASE)
        with self.assertRaises(ContainerError) as raised:
            self.run_init(fake)
        self.assertIn("alpine", str(raised.exception))
        self.assertEqual(fake.inside_calls("mkdir"), [])

    def test_invalid_name_is_rejected(self):
        fake = FakeContainers()
        with self.assertRaises(ContainerError):
            self.run_init(fake, name="--image")
        self.assertEqual(fake.runner.calls, [])

    def test_missing_proot_distro_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        fake = FakeContainers()
        with self.assertRaises(ContainerError) as raised:
            self.run_init(fake)
        self.assertIn("proot-distro", str(raised.exception))

    def test_failed_creation_is_reported(self):
        fake = FakeContainers(containers=(), fail={"install"})
        with self.assertRaises(ContainerError) as raised:
            self.run_init(fake)
        self.assertIn("create the container", str(raised.exception))

    def test_failed_verification_is_reported(self):
        fake = FakeContainers(verify_ok=False)
        with self.assertRaises(ContainerError) as raised:
            self.run_init(fake)
        self.assertIn("verify the container", str(raised.exception))


class InstallPackagesTest(unittest.TestCase):
    def test_the_package_list_is_updated_before_the_install(self):
        fake = FakeContainers(containers=("ubuntu",))
        output = io.StringIO()
        install_packages("pd", "ubuntu", ("gimp",), runner=fake.runner, out=output)
        calls = fake.inside_calls("apt-get")
        self.assertEqual(calls[0][:3], ["apt-get", "update", "-qq"])
        self.assertEqual(
            calls[1],
            ["apt-get", "install", "-y", "--no-install-recommends", "gimp"],
        )
        self.assertIn("Installing: gimp", output.getvalue())

    def test_the_package_manager_gets_the_noninteractive_environment(self):
        fake = FakeContainers(containers=("ubuntu",))
        install_packages("pd", "ubuntu", ("xterm",), runner=fake.runner, out=io.StringIO())
        apt_calls = [call for call in fake.runner.calls if "apt-get" in call]
        self.assertEqual(len(apt_calls), 2)
        for call in apt_calls:
            self.assertIn("DEBIAN_FRONTEND=noninteractive", call)

    def test_unknown_family_is_reported(self):
        fake = FakeContainers(containers=("alpine",), os_release=ALPINE_RELEASE)
        with self.assertRaises(ContainerError) as raised:
            install_packages(
                "pd", "alpine", ("xterm",), runner=fake.runner, out=io.StringIO()
            )
        self.assertIn("alpine", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
