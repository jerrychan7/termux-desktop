"""Tests for launching a container application on the desk display."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import Workspace, make_state
from termux_desk.app import (
    AppError,
    build_launch_command,
    launch_bindings,
    parse_target,
    run_app_launch,
)
from termux_desk.container import PROOT_DISTRO_PROGRAM
from test_container import FakeContainers


class ParseTargetTest(unittest.TestCase):
    def test_container_and_app_are_split_at_the_first_slash(self):
        target = parse_target("ubuntu-desktop-test/xterm")
        self.assertEqual(target.container, "ubuntu-desktop-test")
        self.assertEqual(target.app, "xterm")
        self.assertEqual(target.spec, "ubuntu-desktop-test/xterm")

    def test_app_may_be_a_path_inside_the_container(self):
        self.assertEqual(parse_target("ubuntu/usr/bin/xterm").app, "usr/bin/xterm")

    def test_missing_parts_are_rejected(self):
        for text in ("ubuntu", "ubuntu/", "/xterm", ""):
            with self.subTest(text=text):
                with self.assertRaises(AppError):
                    parse_target(text)

    def test_invalid_container_name_is_rejected(self):
        with self.assertRaises(AppError):
            parse_target("--image/xterm")

    def test_option_like_program_is_rejected(self):
        with self.assertRaises(AppError):
            parse_target("ubuntu/-xterm")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)
        self.workspace.write_state(make_state())
        self.workspace.create_socket(self.workspace.paths.x11_socket)

    def run_launch(self, text="ubuntu-desktop-test/xterm", **kwargs):
        output = io.StringIO()
        calls = []

        def record(path, argv):
            calls.append((path, argv))

        kwargs.setdefault("exec_func", record)
        run_app_launch(
            self.workspace.paths,
            text,
            environ=self.workspace.environ,
            out=output,
            **kwargs,
        )
        return output.getvalue(), calls

    def test_bindings_use_the_project_mount_points(self):
        paths = self.workspace.paths
        self.assertEqual(
            launch_bindings(paths, self.workspace.environ),
            (
                (str(paths.termux_data_dir), "/termux"),
                (str(paths.home), "/home/termux"),
                ("/storage/emulated/0", "/sdcard"),
            ),
        )

    def test_external_storage_can_be_overridden(self):
        environ = dict(self.workspace.environ, EXTERNAL_STORAGE="/storage/abcd-1234")
        self.assertEqual(
            launch_bindings(self.workspace.paths, environ)[2],
            ("/storage/abcd-1234", "/sdcard"),
        )

    def test_launch_command_shape(self):
        target = parse_target("ubuntu-desktop-test/xterm")
        binds = launch_bindings(self.workspace.paths, self.workspace.environ)
        command = build_launch_command("/usr/bin/proot-distro", target, ":0", binds)
        self.assertEqual(command[0], "/usr/bin/proot-distro")
        self.assertEqual(command[1:4], ("sh", "ubuntu-desktop-test", "--isolated"))
        self.assertIn("--shared-tmp", command)
        self.assertEqual(command[-4:], ("-e", "DISPLAY=:0", "--", "xterm"))

    def test_arguments_replace_the_program_name(self):
        target = parse_target("ubuntu/Alacritty")
        binds = launch_bindings(self.workspace.paths, self.workspace.environ)
        command = build_launch_command(
            "/usr/bin/proot-distro", target, ":0", binds, ("alacritty", "-e", "sh")
        )
        self.assertEqual(command[-4:], ("--", "alacritty", "-e", "sh"))

    def test_launch_replaces_the_desk_process(self):
        output, calls = self.run_launch()
        self.assertEqual(len(calls), 1)
        program, argv = calls[0]
        self.assertTrue(program.endswith("proot-distro"))
        self.assertEqual(argv[-1], "xterm")
        self.assertIn("--bind", argv)
        self.assertIn("Launching ubuntu-desktop-test/xterm on :0.", output)

    def test_launch_without_a_session_is_refused(self):
        self.workspace.paths.session_file.unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("not running", str(raised.exception))

    def test_launch_without_the_x11_socket_is_refused(self):
        self.workspace.paths.x11_socket.unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("socket", str(raised.exception))

    def test_missing_proot_distro_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("proot-distro", str(raised.exception))

    def test_exec_failure_is_reported(self):
        def fail(path, argv):
            raise OSError("no such file")

        with self.assertRaises(AppError) as raised:
            self.run_launch(exec_func=fail)
        self.assertIn("could not launch", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
