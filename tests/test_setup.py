"""Tests for the fixed package setup plan and the install loop."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import RecordingRunner, command_name, make_result
from termux_desk.config import FIXED_PACKAGES, load_paths
from termux_desk.setup import (
    SetupError,
    build_setup_commands,
    find_missing_packages,
    package_is_installed,
    resolve_setup_program,
    run_setup,
)


class FakePackages:
    """Answer dpkg-query and apply pkg install commands to a package set."""

    def __init__(self, installed=(), *, apply_install=True, fail_commands=()):
        self.installed = set(installed)
        self.apply_install = apply_install
        self.fail_commands = set(fail_commands)
        self.runner = RecordingRunner(self.handle)

    def handle(self, command, **kwargs):
        program = command_name(command)
        key = f"{program} {command[1]}"
        if key in self.fail_commands:
            return make_result(command, returncode=1)
        if program == "dpkg-query":
            name = command[-1]
            if name in self.installed:
                return make_result(command, stdout="install ok installed\n")
            return make_result(command, returncode=1)
        if program == "pkg" and command[1] == "install":
            if self.apply_install:
                self.installed.update(name for name in command[2:] if name != "-y")
            return make_result(command)
        if program == "pkg" and command[1] == "update":
            return make_result(command)
        raise AssertionError(f"unexpected command: {command}")

    def package_commands(self):
        """Return the pkg commands with the resolved program name normalised."""
        result = []
        for command in self.runner.calls:
            if command_name(command) == "pkg":
                result.append(("pkg", *command[1:]))
        return result


class PlanTest(unittest.TestCase):
    def test_nothing_missing_means_no_commands(self):
        self.assertEqual(build_setup_commands(()), ())

    def test_everything_missing_installs_repo_then_updates_then_the_rest(self):
        commands = build_setup_commands(FIXED_PACKAGES)
        self.assertEqual(
            commands,
            (
                ("pkg", "install", "-y", "x11-repo"),
                ("pkg", "update", "-y"),
                ("pkg", "install", "-y", "termux-x11-nightly", "xfce4", "dbus", "termux-am"),
            ),
        )

    def test_only_repo_missing_installs_repo_then_updates(self):
        commands = build_setup_commands(("x11-repo",))
        self.assertEqual(
            commands,
            (("pkg", "install", "-y", "x11-repo"), ("pkg", "update", "-y")),
        )

    def test_repo_present_updates_before_other_installs(self):
        commands = build_setup_commands(("xfce4", "dbus"))
        self.assertEqual(
            commands,
            (("pkg", "update", "-y"), ("pkg", "install", "-y", "xfce4", "dbus")),
        )

    def test_plan_keeps_the_fixed_package_order(self):
        commands = build_setup_commands(("termux-am", "xfce4", "dbus"))
        self.assertEqual(commands[-1], ("pkg", "install", "-y", "xfce4", "dbus", "termux-am"))

    def test_plan_never_upgrades(self):
        for command in build_setup_commands(FIXED_PACKAGES):
            self.assertNotIn("upgrade", command)
            self.assertNotIn("full-upgrade", command)

    def test_unknown_names_are_ignored_by_the_plan(self):
        self.assertEqual(build_setup_commands(("firefox",)), ())

    def test_resolved_package_manager_path_is_used(self):
        commands = build_setup_commands(("xfce4",), package_manager="/data/usr/bin/pkg")
        self.assertEqual(
            commands,
            (("/data/usr/bin/pkg", "update", "-y"), ("/data/usr/bin/pkg", "install", "-y", "xfce4")),
        )


class MissingPackageTest(unittest.TestCase):
    def test_installed_package_is_detected(self):
        packages = FakePackages(installed=("xfce4",))
        self.assertTrue(package_is_installed("xfce4", packages.runner))
        self.assertFalse(package_is_installed("dbus", packages.runner))

    def test_missing_packages_keep_the_fixed_order(self):
        packages = FakePackages(installed=("x11-repo", "dbus"))
        missing = find_missing_packages(packages.runner)
        self.assertEqual(missing, ("termux-x11-nightly", "xfce4", "termux-am"))

    def test_dpkg_query_failure_counts_as_missing(self):
        runner = RecordingRunner(lambda command, **kwargs: make_result(command, returncode=1))
        self.assertFalse(package_is_installed("xfce4", runner))

    def test_missing_dpkg_query_asks_for_a_termux_shell(self):
        def handler(command, **kwargs):
            raise FileNotFoundError("dpkg-query")

        runner = RecordingRunner(handler)
        with self.assertRaises(SetupError) as raised:
            package_is_installed("xfce4", runner)
        self.assertIn("inside Termux", str(raised.exception))


class ResolveSetupProgramTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "usr"
        (self.prefix / "bin").mkdir(parents=True)
        self.path_directory = self.root / "path"
        self.path_directory.mkdir()
        self.environ = {"PATH": str(self.path_directory)}
        self.paths = load_paths({"HOME": str(self.root / "home"), "PREFIX": str(self.prefix)})

    def add_program(self, directory, name):
        program = directory / name
        program.write_text("#!/bin/sh\n", encoding="utf-8")
        program.chmod(0o755)
        return program

    def test_prefix_bin_is_preferred_over_path(self):
        in_prefix = self.add_program(self.prefix / "bin", "pkg")
        self.add_program(self.path_directory, "pkg")
        found = resolve_setup_program("pkg", self.paths, self.environ)
        self.assertEqual(found, str(in_prefix))

    def test_path_is_used_when_the_prefix_has_no_program(self):
        in_path = self.add_program(self.path_directory, "dpkg-query")
        found = resolve_setup_program("dpkg-query", self.paths, self.environ)
        self.assertEqual(found, str(in_path))

    def test_missing_program_is_reported(self):
        with self.assertRaises(SetupError) as raised:
            resolve_setup_program("pkg", self.paths, self.environ)
        self.assertIn("desk setup must run inside Termux", str(raised.exception))


class RunSetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_paths(self, *, prefix=True, programs=("pkg", "dpkg-query")):
        environ = {"HOME": str(self.root / "home")}
        if prefix:
            prefix_path = self.root / "usr"
            (prefix_path / "bin").mkdir(parents=True, exist_ok=True)
            for name in programs:
                program = prefix_path / "bin" / name
                program.write_text("#!/bin/sh\n", encoding="utf-8")
                program.chmod(0o755)
            environ["PREFIX"] = str(prefix_path)
        return load_paths(environ)

    def test_setup_requires_an_existing_prefix(self):
        paths = self.make_paths(prefix=False)
        with self.assertRaises(SetupError) as raised:
            run_setup(paths, RecordingRunner(), out=io.StringIO())
        self.assertIn("PREFIX", str(raised.exception))

    def test_setup_reports_a_missing_package_manager(self):
        paths = self.make_paths(programs=("dpkg-query",))
        with self.assertRaises(SetupError) as raised:
            run_setup(paths, RecordingRunner(), environ={"PATH": ""}, out=io.StringIO())
        self.assertIn("pkg", str(raised.exception))
        self.assertIn("inside Termux", str(raised.exception))

    def test_setup_reports_a_missing_dpkg_query(self):
        paths = self.make_paths(programs=("pkg",))
        with self.assertRaises(SetupError) as raised:
            run_setup(paths, RecordingRunner(), environ={"PATH": ""}, out=io.StringIO())
        self.assertIn("dpkg-query", str(raised.exception))

    def test_nothing_to_do_runs_no_package_command(self):
        packages = FakePackages(installed=FIXED_PACKAGES)
        output = io.StringIO()
        run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=output)
        self.assertEqual(
            [command_name(command) for command in packages.runner.calls],
            ["dpkg-query"] * 5,
        )
        self.assertIn("All fixed packages are already installed.", output.getvalue())

    def test_setup_runs_the_planned_commands_in_order(self):
        packages = FakePackages(installed=("x11-repo", "dbus"))
        output = io.StringIO()

        run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=output)

        self.assertEqual(
            packages.package_commands(),
            [
                ("pkg", "update", "-y"),
                ("pkg", "install", "-y", "termux-x11-nightly", "xfce4", "termux-am"),
            ],
        )
        self.assertIn("Running:", output.getvalue())
        self.assertIn("All fixed packages are installed.", output.getvalue())
        self.assertIn("https://github.com/termux/termux-x11/releases", output.getvalue())

    def test_setup_installs_repo_before_the_index_is_refreshed(self):
        packages = FakePackages()
        output = io.StringIO()

        run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=output)

        self.assertEqual(
            packages.package_commands()[:2],
            [("pkg", "install", "-y", "x11-repo"), ("pkg", "update", "-y")],
        )

    def test_setup_stops_at_a_failed_command(self):
        packages = FakePackages(installed=("x11-repo",), fail_commands={"pkg update"})
        output = io.StringIO()

        with self.assertRaises(SetupError) as raised:
            run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=output)

        self.assertIn("pkg update", str(raised.exception))
        self.assertEqual(packages.package_commands(), [("pkg", "update", "-y")])

    def test_setup_reports_packages_still_missing_after_install(self):
        packages = FakePackages(apply_install=False)
        with self.assertRaises(SetupError) as raised:
            run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=io.StringIO())
        self.assertIn("still missing", str(raised.exception))

    def test_setup_does_not_install_the_android_app_or_other_packages(self):
        packages = FakePackages()
        run_setup(self.make_paths(), packages.runner, environ={"PATH": ""}, out=io.StringIO())
        for command in packages.runner.calls:
            if command_name(command) == "pkg":
                for name in command[2:]:
                    if name == "-y":
                        continue
                    self.assertIn(name, FIXED_PACKAGES)


class SetupPrefixTest(unittest.TestCase):
    def test_prefix_missing_produces_a_direct_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = load_paths({"HOME": temporary})
            with self.assertRaises(SetupError):
                run_setup(paths, RecordingRunner(), environ={"PATH": ""}, out=io.StringIO())


if __name__ == "__main__":
    unittest.main()
