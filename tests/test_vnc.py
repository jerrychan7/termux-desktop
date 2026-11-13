"""Tests for the VNC backend helpers."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_setup import FakePackages

from termux_desk import vnc
from termux_desk.config import load_paths
from termux_desk.session import BACKEND_VNC


class XvncArgumentTest(unittest.TestCase):
    def test_default_arguments_bind_localhost_without_authentication(self):
        arguments = vnc.build_xvnc_arguments()
        self.assertIn("-geometry", arguments)
        self.assertEqual(arguments[arguments.index("-geometry") + 1], vnc.VNC_GEOMETRY)
        self.assertIn("-localhost", arguments)
        self.assertEqual(arguments[arguments.index("-UseBlacklist") + 1], "0")
        self.assertEqual(arguments[arguments.index("-SecurityTypes") + 1], "None")
        self.assertNotIn("-PasswordFile", arguments)

    def test_password_file_selects_vnc_auth(self):
        arguments = vnc.build_xvnc_arguments(password_file=Path("/tmp/passwd"))
        self.assertEqual(arguments[arguments.index("-PasswordFile") + 1], "/tmp/passwd")
        self.assertEqual(arguments[arguments.index("-SecurityTypes") + 1], "VncAuth")

    def test_invalid_geometry_is_rejected(self):
        for geometry in ("", "1280", "1280x", "x720", "0x720", "1280x0", "a x b"):
            with self.subTest(geometry=geometry):
                with self.assertRaises(vnc.VncError):
                    vnc.build_xvnc_arguments(geometry=geometry)

    def test_rfb_port_follows_the_display_number(self):
        self.assertEqual(vnc.rfb_port(":1"), 5901)
        self.assertEqual(vnc.rfb_port(":12"), 5912)
        with self.assertRaises(vnc.VncError):
            vnc.rfb_port(":x")


class StartTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = load_paths(
            {"HOME": str(self.root / "home"), "PREFIX": str(self.root / "usr")}
        )

    def test_start_uses_the_vnc_backend_on_the_requested_display(self):
        with mock.patch.object(vnc, "start_session") as start:
            start.return_value = "state"
            result = vnc.run_vnc_start(
                self.paths, display=":2", geometry="800x600", out=mock.Mock()
            )
        self.assertEqual(result, "state")
        target = start.call_args.args[0]
        self.assertEqual(target.display, ":2")
        kwargs = start.call_args.kwargs
        self.assertEqual(kwargs["backend"], BACKEND_VNC)
        self.assertIn("800x600", kwargs["vnc_arguments"])
        self.assertNotIn("-PasswordFile", kwargs["vnc_arguments"])

    def test_missing_password_file_is_reported_before_anything_starts(self):
        with mock.patch.object(vnc, "start_session") as start:
            with self.assertRaises(vnc.VncError):
                vnc.run_vnc_start(
                    self.paths, password_file=self.root / "missing", out=mock.Mock()
                )
        start.assert_not_called()

    def test_status_and_stop_use_the_requested_display(self):
        with mock.patch.object(vnc, "session_status") as status:
            status.return_value = mock.Mock(running=True, lines=("Session is running.",))
            report = vnc.run_vnc_status(self.paths, display=":3")
        self.assertTrue(report.running)
        self.assertEqual(status.call_args.args[0].display, ":3")

        with mock.patch.object(vnc, "stop_session") as stop, \
                mock.patch("termux_desk.webproxy.read_web_state", return_value=None), \
                mock.patch("termux_desk.webproxy.run_web_stop") as web_stop:
            vnc.run_vnc_stop(self.paths, display=":4", out=io.StringIO())
        self.assertEqual(stop.call_args.args[0].display, ":4")
        web_stop.assert_not_called()

    def test_stop_also_stops_a_running_web_proxy(self):
        with mock.patch.object(vnc, "stop_session") as stop, \
                mock.patch("termux_desk.webproxy.read_web_state", return_value=mock.Mock()), \
                mock.patch("termux_desk.webproxy.run_web_stop") as web_stop:
            vnc.run_vnc_stop(self.paths, display=":4", out=io.StringIO())
        stop.assert_called_once()
        web_stop.assert_called_once()
        self.assertEqual(web_stop.call_args.kwargs["display"], ":4")
        self.assertTrue(web_stop.call_args.kwargs["session_stopped"])



class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = load_paths(
            {"HOME": str(self.root / "home"), "PREFIX": str(self.root / "usr")}
        )
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        self.out = io.StringIO()

    def test_arguments_mirror_display_zero_without_authentication(self):
        arguments = vnc.build_x0vncserver_arguments()
        self.assertEqual(arguments[arguments.index("-display") + 1], ":0")
        self.assertEqual(arguments[arguments.index("-rfbport") + 1], "5900")
        self.assertIn("-localhost", arguments)
        self.assertEqual(arguments[arguments.index("-SecurityTypes") + 1], "None")
        self.assertEqual(arguments[arguments.index("-UseBlacklist") + 1], "0")
        self.assertIn("-AlwaysShared", arguments)
        self.assertEqual(arguments[arguments.index("-desktop") + 1], "host :0")

    def test_arguments_reject_a_display_other_than_zero(self):
        with self.assertRaises(vnc.VncError):
            vnc.build_x0vncserver_arguments(display=":1")

    def test_state_round_trip_and_removal(self):
        state = vnc.MirrorState(display=":0", pid=4242, port=5900, started_at=1.5)
        vnc.write_mirror_state(self.paths, state)
        self.assertEqual(vnc.read_mirror_state(self.paths, ":0"), state)
        vnc.remove_mirror_state(self.paths, ":0")
        self.assertIsNone(vnc.read_mirror_state(self.paths, ":0"))

    def test_start_requires_display_zero(self):
        with self.assertRaises(vnc.VncError):
            vnc.run_mirror_start(self.paths, display=":2", out=self.out)

    def test_start_requires_a_running_x_server(self):
        with mock.patch.object(vnc, "display_socket_ready", return_value=False):
            with self.assertRaises(vnc.VncError) as caught:
                vnc.run_mirror_start(self.paths, out=self.out)
        self.assertIn("desk start", str(caught.exception))

    def test_start_reports_an_existing_mirror(self):
        state = vnc.MirrorState(display=":0", pid=4242, port=5900)
        with mock.patch.object(vnc, "read_mirror_state", return_value=state), \
                mock.patch.object(vnc, "pid_alive", return_value=True):
            with self.assertRaises(vnc.VncError) as caught:
                vnc.run_mirror_start(self.paths, out=self.out)
        self.assertIn("already running", str(caught.exception))

    def test_start_rejects_a_busy_port(self):
        with mock.patch.object(vnc, "display_socket_ready", return_value=True), \
                mock.patch.object(vnc, "read_mirror_state", return_value=None), \
                mock.patch.object(vnc, "resolve_program", return_value="/usr/bin/x0vncserver"), \
                mock.patch.object(vnc, "_port_free", return_value=False):
            with self.assertRaises(vnc.VncError) as caught:
                vnc.run_mirror_start(self.paths, out=self.out)
        self.assertIn("already in use", str(caught.exception))

    def test_start_spawns_x0vncserver_and_records_the_state(self):
        spawn = mock.Mock(return_value=mock.Mock(pid=4242))
        with mock.patch.object(vnc, "display_socket_ready", return_value=True), \
                mock.patch.object(vnc, "read_mirror_state", return_value=None), \
                mock.patch.object(vnc, "resolve_program", return_value="/usr/bin/x0vncserver"), \
                mock.patch.object(vnc, "_port_free", return_value=True), \
                mock.patch.object(vnc, "_wait_for_accept", return_value=True):
            state = vnc.run_mirror_start(self.paths, environ={}, out=self.out, spawn=spawn)
        command = spawn.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/x0vncserver")
        self.assertIn(":0", command)
        self.assertEqual(state.pid, 4242)
        self.assertEqual(vnc.read_mirror_state(self.paths, ":0").pid, 4242)
        self.assertIn("Mirroring the desktop on :0", self.out.getvalue())

    def test_start_fails_when_x0vncserver_never_listens(self):
        spawn = mock.Mock(return_value=mock.Mock(pid=4242))
        with mock.patch.object(vnc, "display_socket_ready", return_value=True), \
                mock.patch.object(vnc, "read_mirror_state", return_value=None), \
                mock.patch.object(vnc, "resolve_program", return_value="/usr/bin/x0vncserver"), \
                mock.patch.object(vnc, "_port_free", return_value=True), \
                mock.patch.object(vnc, "_wait_for_accept", return_value=False), \
                mock.patch.object(vnc.os, "kill") as kill:
            with self.assertRaises(vnc.VncError):
                vnc.run_mirror_start(self.paths, environ={}, out=self.out, spawn=spawn)
        kill.assert_called_once_with(4242, 15)

    def test_stop_signals_the_pid_and_removes_the_state(self):
        vnc.write_mirror_state(
            self.paths, vnc.MirrorState(display=":0", pid=4242, port=5900)
        )
        with mock.patch.object(vnc, "pid_alive", side_effect=[True, False, False]), \
                mock.patch.object(vnc.os, "kill") as kill:
            stopped = vnc.run_mirror_stop(self.paths, out=self.out)
        self.assertTrue(stopped)
        kill.assert_called_once_with(4242, 15)
        self.assertIsNone(vnc.read_mirror_state(self.paths, ":0"))
        self.assertIn("Stopped the VNC mirror on :0.", self.out.getvalue())

    def test_stop_without_a_state_says_so(self):
        self.assertFalse(vnc.run_mirror_stop(self.paths, out=self.out))

    def test_status_reports_running_and_absent(self):
        self.assertFalse(vnc.run_mirror_status(self.paths, out=self.out))
        vnc.write_mirror_state(
            self.paths, vnc.MirrorState(display=":0", pid=4242, port=5900)
        )
        with mock.patch.object(vnc, "pid_alive", return_value=True):
            self.assertTrue(vnc.run_mirror_status(self.paths, out=self.out))

class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        prefix = self.root / "usr"
        (prefix / "bin").mkdir(parents=True)
        self.paths = load_paths({"HOME": str(self.root / "home"), "PREFIX": str(prefix)})
        self.patcher = mock.patch.object(
            vnc.setup, "resolve_setup_program", side_effect=lambda name, paths, env: name
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def setup_vnc(self, runner, *, programs=("/usr/bin/Xvnc", "/usr/bin/vncpasswd", "/usr/bin/x0vncserver", "/usr/bin/darkhttpd")):
        with mock.patch.object(vnc, "resolve_program", side_effect=list(programs * 4)):
            return vnc.run_vnc_setup(self.paths, runner, environ={}, out=io.StringIO())

    def test_missing_packages_are_installed_and_checked(self):
        packages = FakePackages()
        self.setup_vnc(packages.runner)
        self.assertEqual(packages.installed, {"x11-repo", "tigervnc", "darkhttpd"})
        self.assertEqual(
            packages.package_commands(),
            [
                ("pkg", "install", "-y", "x11-repo"),
                ("pkg", "update", "-y"),
                ("pkg", "install", "-y", "tigervnc", "darkhttpd"),
            ],
        )

    def test_installed_packages_need_no_package_commands(self):
        packages = FakePackages({"x11-repo", "tigervnc", "darkhttpd"})
        self.setup_vnc(packages.runner)
        self.assertEqual(packages.package_commands(), [])

    def test_missing_program_after_setup_is_reported(self):
        packages = FakePackages()
        with mock.patch.object(vnc, "resolve_program", return_value=None):
            with self.assertRaises(vnc.VncError):
                vnc.run_vnc_setup(self.paths, packages.runner, environ={}, out=io.StringIO())


if __name__ == "__main__":
    unittest.main()
