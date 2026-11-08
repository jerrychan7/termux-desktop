"""Tests for the read-only environment checks."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import RecordingRunner, Workspace, make_result, make_state
from termux_desk.config import load_paths
from termux_desk.doctor import (
    FAILED,
    OK,
    PlatformFacts,
    check_android_system,
    check_termux_environment,
    package_is_listed,
    read_platform_facts,
    run_doctor,
)
from termux_desk.session import (
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    VERIFICATION_UNVERIFIABLE,
    Verification,
)


def android_facts(workspace) -> PlatformFacts:
    return PlatformFacts(
        system="Android",
        python_platform="linux",
        kernel_release="6.12.23-android16-5-g1234567",
        termux_version="0.118.3",
        prefix=str(workspace.prefix),
    )


def pm_with_package(command, **kwargs):
    if command[0].endswith("pm") and command[1:3] == ("list", "packages"):
        return make_result(command, stdout="package:com.termux.x11\n")
    return make_result(command, returncode=1, stderr="not found")


def pm_without_package(command, **kwargs):
    if command[0].endswith("pm") and command[1:3] == ("list", "packages"):
        return make_result(command, stdout="package:com.termux\n")
    return make_result(command, returncode=1, stderr="not found")


def pm_that_raises(command, **kwargs):
    raise FileNotFoundError(command[0])


def running_session(state):
    return Verification(VERIFICATION_RUNNING, f"process group {state.pgid} belongs to session")


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_required_programs()
        self.workspace.paths.x11_socket_dir.mkdir(parents=True, exist_ok=True)

    def run_checks(self, **kwargs):
        output = io.StringIO()
        runner = kwargs.pop("runner", None) or RecordingRunner(pm_with_package)
        environ = kwargs.pop("environ", None) or self.workspace.environ
        platform_facts = kwargs.pop("platform_facts", None) or android_facts(self.workspace)
        socket_connect = kwargs.pop("socket_connect", None) or (lambda path: False)
        session_verifier = kwargs.pop("session_verifier", None) or running_session
        exit_code = run_doctor(
            self.workspace.paths,
            runner,
            environ=environ,
            platform_facts=platform_facts,
            socket_connect=socket_connect,
            session_verifier=session_verifier,
            out=output,
        )
        return exit_code, output.getvalue()

    def test_healthy_environment_passes(self):
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertNotIn("[fail]", output)
        self.assertIn("[ok] termux environment", output)
        self.assertIn("[ok] android system", output)
        for name in (
            "PREFIX",
            "termux-x11",
            "startxfce4",
            "dbus-launch",
            "am",
            "Termux:X11 Android app",
            "X11 socket directory",
            "display :0",
            "desk session",
        ):
            self.assertIn(name, output)
        self.assertIn("0 failure", output)

    def test_doctor_does_not_create_the_state_directory(self):
        self.assertFalse(self.workspace.paths.state_dir.exists())
        self.run_checks()
        self.assertFalse(self.workspace.paths.state_dir.exists())

    def test_missing_termux_x11_is_a_failure_with_a_setup_hint(self):
        self.workspace.paths.prefix.joinpath("bin", "termux-x11").unlink()
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] termux-x11", output)
        self.assertIn("desk setup", output)

    def test_missing_startxfce4_is_a_failure(self):
        self.workspace.paths.prefix.joinpath("bin", "startxfce4").unlink()
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] startxfce4", output)

    def test_missing_dbus_launch_is_a_failure(self):
        self.workspace.paths.prefix.joinpath("bin", "dbus-launch").unlink()
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] dbus-launch", output)

    def test_am_can_be_found_on_path(self):
        self.workspace.paths.prefix.joinpath("bin", "am").unlink()
        self.workspace.add_program("am", self.workspace.path)
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[ok] am", output)

    def test_missing_am_is_a_failure(self):
        self.workspace.paths.prefix.joinpath("bin", "am").unlink()
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] am", output)
        self.assertIn("termux-am", output)

    def test_missing_android_app_entry_is_a_warning(self):
        exit_code, output = self.run_checks(runner=RecordingRunner(pm_without_package))
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] Termux:X11 Android app", output)
        self.assertIn("not proof", output)
        self.assertIn("https://github.com/termux/termux-x11/releases", output)
        self.assertNotIn("[fail] Termux:X11 Android app", output)

    def test_unknown_android_package_list_is_a_warning(self):
        exit_code, output = self.run_checks(runner=RecordingRunner(pm_that_raises))
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] Termux:X11 Android app", output)
        self.assertIn("could not ask Android", output)

    def test_android_not_detected_is_only_a_warning(self):
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="4.14.190-perf+",
            termux_version="0.118.3",
            prefix=str(self.workspace.prefix),
        )
        exit_code, output = self.run_checks(platform_facts=facts)
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] android system", output)
        self.assertIn("[ok] termux environment", output)

    def test_termux_not_detected_is_a_failure(self):
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="6.8.0-45-generic",
            termux_version="",
            prefix=str(self.workspace.prefix),
        )
        exit_code, output = self.run_checks(platform_facts=facts)
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] termux environment", output)

    def test_python_310_termux_facts_pass_the_termux_check(self):
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="4.14.190-perf+",
            termux_version="0.118.3",
            prefix="/data/data/com.termux/files/usr",
        )
        self.assertEqual(check_termux_environment(facts).status, OK)
        self.assertEqual(check_android_system(facts).status, "warn")

    def test_missing_prefix_is_reported_without_a_crash(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = load_paths({"HOME": temporary})
            output = io.StringIO()
            facts = PlatformFacts(
                system="Linux",
                python_platform="linux",
                kernel_release="6.8.0-45-generic",
                termux_version="0.118.3",
                prefix="",
            )
            exit_code = run_doctor(
                paths,
                RecordingRunner(pm_that_raises),
                environ={"HOME": temporary},
                platform_facts=facts,
                socket_connect=lambda path: False,
                session_verifier=running_session,
                out=output,
            )
        text = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("[fail] PREFIX", text)
        self.assertIn("[fail] X11 socket directory", text)

    def test_missing_socket_directory_is_a_warning(self):
        self.workspace.paths.x11_socket_dir.rmdir()
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] X11 socket directory", output)

    def test_live_x_server_is_a_warning(self):
        self.workspace.create_socket(self.workspace.paths.x11_socket)
        exit_code, output = self.run_checks(socket_connect=lambda path: True)
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] display :0", output)
        self.assertIn("already accepts connections", output)

    def test_stale_x_server_files_are_a_warning(self):
        self.workspace.paths.x11_socket.write_text("stale", encoding="utf-8")
        self.workspace.paths.x11_lock.write_text("999999\n", encoding="utf-8")
        exit_code, output = self.run_checks(socket_connect=lambda path: False)
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] display :0", output)
        self.assertIn("leftover files", output)

    def test_no_x_server_files_is_ok(self):
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[ok] display :0", output)

    def test_running_session_is_reported(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[ok] desk session", output)
        self.assertIn("4242", output)

    def test_stale_state_is_reported_as_a_warning(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        exit_code, output = self.run_checks(
            session_verifier=lambda state: Verification(
                VERIFICATION_STALE, "process group 4242 has no live processes"
            )
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] desk session", output)
        self.assertIn("no live processes", output)

    def test_unverifiable_state_is_reported_as_a_warning(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        exit_code, output = self.run_checks(
            session_verifier=lambda state: Verification(
                VERIFICATION_UNVERIFIABLE,
                "process group 4242 has processes, but none carry the session id",
            )
        )
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] desk session", output)
        self.assertIn("cannot verify", output)

    def test_broken_state_is_reported_as_a_warning(self):
        self.workspace.paths.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_file.write_text("{not json", encoding="utf-8")
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] desk session", output)
        self.assertIn("not valid JSON", output)
        self.assertTrue(self.workspace.paths.session_file.exists())


class PlatformFactsTest(unittest.TestCase):
    def test_read_platform_facts_uses_each_source(self):
        fake_uname = SimpleNamespace(release="sentinel-kernel-release")
        with mock.patch(
            "termux_desk.doctor.platform.system", return_value="SentinelSystem"
        ), mock.patch("termux_desk.doctor.os.uname", return_value=fake_uname):
            facts = read_platform_facts(
                {"TERMUX_VERSION": "9.9.9", "PREFIX": "/sentinel/prefix"}
            )
        self.assertEqual(
            facts,
            PlatformFacts(
                system="SentinelSystem",
                python_platform=sys.platform,
                kernel_release="sentinel-kernel-release",
                termux_version="9.9.9",
                prefix="/sentinel/prefix",
            ),
        )

    def test_python_310_style_facts_are_detected_by_termux_facts(self):
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="4.19.113-g1234",
            termux_version="",
            prefix="/data/data/com.termux/files/usr",
        )
        self.assertEqual(check_termux_environment(facts).status, OK)

    def test_plain_android_version_is_not_android(self):
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="16",
            termux_version="",
            prefix="/usr",
        )
        self.assertEqual(check_android_system(facts).status, "warn")
        self.assertEqual(check_termux_environment(facts).status, FAILED)


class PackageListingTest(unittest.TestCase):
    def test_exact_package_line_is_recognized(self):
        self.assertTrue(package_is_listed("package:com.termux.x11\n", "com.termux.x11"))

    def test_similar_package_names_are_not_recognized(self):
        output = "package:com.termux.x11.debug\npackage:com.termux\n"
        self.assertFalse(package_is_listed(output, "com.termux.x11"))

    def test_empty_output_is_not_recognized(self):
        self.assertFalse(package_is_listed("", "com.termux.x11"))


if __name__ == "__main__":
    unittest.main()
