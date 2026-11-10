"""Tests for the read-only environment checks."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import RecordingRunner, Workspace, make_result, make_state
from termux_desk.config import load_paths
from termux_desk.doctor import (
    COLOR_ALWAYS,
    COLOR_AUTO,
    COLOR_NEVER,
    FAILED,
    OK,
    WARNING,
    PlatformFacts,
    check_android_system,
    check_runtime_directory,
    check_termux_environment,
    color_enabled,
    package_is_listed,
    read_platform_facts,
    run_doctor,
)
from termux_desk.session import (
    ProcessScanError,
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    VERIFICATION_UNVERIFIABLE,
    Verification,
)

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


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


class TerminalStream(io.StringIO):
    """A text stream that claims to be a terminal, or fails the question."""

    def __init__(self, *, isatty_result=True, error=None):
        super().__init__()
        self.isatty_result = isatty_result
        self.error = error

    def isatty(self):
        if self.error is not None:
            raise self.error
        return self.isatty_result


class WriteOnlyStream:
    """A stream without isatty that records what is written to it."""

    def __init__(self):
        self.parts = []

    def write(self, text):
        self.parts.append(text)

    def getvalue(self):
        return "".join(self.parts)


class DoctorTestCase(unittest.TestCase):
    """Shared setup for tests that run the complete doctor report."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_required_programs()
        self.workspace.paths.x11_socket_dir.mkdir(parents=True, exist_ok=True)

    def run_checks(self, **kwargs):
        stream = kwargs.pop("stream", None)
        output = io.StringIO() if stream is None else stream
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
            color=kwargs.pop("color", COLOR_AUTO),
        )
        return exit_code, output.getvalue()


class DoctorTest(DoctorTestCase):
    def test_healthy_environment_passes(self):
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertNotIn("[fail]", output)
        self.assertIn("[ok]   termux environment", output)
        self.assertIn("[ok]   android system", output)
        for name in (
            "PREFIX",
            "termux-x11",
            "startxfce4",
            "dbus-launch",
            "am",
            "Termux:X11 Android app",
            "X11 socket directory",
            "XDG runtime directory",
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
        self.assertIn("[ok]   am", output)

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
        self.assertIn("[ok]   termux environment", output)

    def test_termux_not_detected_is_a_failure(self):
        # A temporary directory can live under $PREFIX/tmp on Termux, which
        # would make the path look like a Termux prefix. Use a path that has
        # no Termux marker.
        facts = PlatformFacts(
            system="Linux",
            python_platform="linux",
            kernel_release="6.8.0-45-generic",
            termux_version="",
            prefix="/opt/not-termux/usr",
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
        self.assertIn("[ok]   display :0", output)

    def test_running_session_is_reported(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[ok]   desk session", output)
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

    def test_verifier_error_is_reported_as_a_warning(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))

        def broken(state):
            raise ProcessScanError("cannot read /proc")

        exit_code, output = self.run_checks(session_verifier=broken)
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] desk session", output)
        self.assertIn("could not verify", output)

    def test_broken_state_is_reported_as_a_warning(self):
        self.workspace.paths.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_file.write_text("{not json", encoding="utf-8")
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertIn("[warn] desk session", output)
        self.assertIn("not valid JSON", output)
        self.assertTrue(self.workspace.paths.session_file.exists())


class ColorTest(DoctorTestCase):
    """Tests for the color option and the ANSI output of the report."""

    def test_auto_enables_color_on_a_terminal(self):
        self.assertTrue(
            color_enabled(COLOR_AUTO, TerminalStream(), {"TERM": "xterm-256color"})
        )

    def test_auto_disables_color_without_a_terminal(self):
        self.assertFalse(color_enabled(COLOR_AUTO, io.StringIO(), {"TERM": "xterm"}))

    def test_auto_disables_color_for_term_dumb(self):
        self.assertFalse(
            color_enabled(COLOR_AUTO, TerminalStream(), {"TERM": "dumb"})
        )

    def test_auto_disables_color_when_no_color_is_set(self):
        self.assertFalse(
            color_enabled(
                COLOR_AUTO, TerminalStream(), {"TERM": "xterm", "NO_COLOR": "1"}
            )
        )

    def test_auto_keeps_color_when_no_color_is_empty(self):
        self.assertTrue(
            color_enabled(
                COLOR_AUTO, TerminalStream(), {"TERM": "xterm", "NO_COLOR": ""}
            )
        )

    def test_auto_disables_color_for_a_stream_without_isatty(self):
        self.assertFalse(color_enabled(COLOR_AUTO, WriteOnlyStream(), {"TERM": "xterm"}))

    def test_auto_disables_color_when_isatty_raises(self):
        stream = TerminalStream(error=OSError("the stream is closed"))
        self.assertFalse(color_enabled(COLOR_AUTO, stream, {"TERM": "xterm"}))

    def test_always_enables_color_on_a_plain_stream_with_no_color(self):
        environ = {"TERM": "dumb", "NO_COLOR": "1"}
        self.assertTrue(color_enabled(COLOR_ALWAYS, io.StringIO(), environ))

    def test_never_disables_color_on_a_terminal(self):
        self.assertFalse(color_enabled(COLOR_NEVER, TerminalStream(), {"TERM": "xterm"}))

    def test_default_doctor_output_has_no_ansi(self):
        exit_code, output = self.run_checks()
        self.assertEqual(exit_code, 0)
        self.assertNotIn("\033[", output)

    def test_forced_color_marks_all_three_tags_and_the_failure_summary(self):
        self.workspace.paths.prefix.joinpath("bin", "startxfce4").unlink()
        exit_code, output = self.run_checks(
            runner=RecordingRunner(pm_without_package),
            color=COLOR_ALWAYS,
        )
        self.assertEqual(exit_code, 1)
        self.assertIn(f"{RED}[fail]{RESET} startxfce4", output)
        self.assertIn(f"{YELLOW}[warn]{RESET} Termux:X11 Android app", output)
        self.assertIn(f"{GREEN}[ok]{RESET}   termux environment", output)
        self.assertIn(f"{RED}Summary: 10 ok, 1 warning, 1 failure.{RESET}", output)

    def test_warning_summary_is_yellow(self):
        exit_code, output = self.run_checks(
            runner=RecordingRunner(pm_without_package),
            color=COLOR_ALWAYS,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn(f"{YELLOW}Summary: ", output)
        self.assertNotIn(RED, output)

    def test_healthy_summary_is_green(self):
        exit_code, output = self.run_checks(color=COLOR_ALWAYS)
        self.assertEqual(exit_code, 0)
        self.assertIn(f"{GREEN}Summary: 12 ok, 0 warning, 0 failure.{RESET}", output)

    def test_auto_colors_a_terminal(self):
        _, output = self.run_checks(stream=TerminalStream())
        self.assertIn(f"{GREEN}[ok]{RESET}   termux environment", output)

    def test_auto_keeps_a_terminal_plain_for_term_dumb(self):
        environ = dict(self.workspace.environ, TERM="dumb")
        _, output = self.run_checks(stream=TerminalStream(), environ=environ)
        self.assertNotIn("\033[", output)

    def test_auto_keeps_a_terminal_plain_when_no_color_is_set(self):
        environ = dict(self.workspace.environ, TERM="xterm", NO_COLOR="1")
        _, output = self.run_checks(stream=TerminalStream(), environ=environ)
        self.assertNotIn("\033[", output)

    def test_auto_colors_a_terminal_when_no_color_is_empty(self):
        environ = dict(self.workspace.environ, TERM="xterm", NO_COLOR="")
        _, output = self.run_checks(stream=TerminalStream(), environ=environ)
        self.assertIn(GREEN, output)

    def test_auto_keeps_a_stream_without_isatty_plain(self):
        _, output = self.run_checks(stream=WriteOnlyStream())
        self.assertNotIn("\033[", output)

    def test_auto_keeps_a_terminal_plain_when_isatty_raises(self):
        stream = TerminalStream(error=OSError("the stream is closed"))
        _, output = self.run_checks(stream=stream)
        self.assertNotIn("\033[", output)

    def test_never_keeps_a_terminal_plain(self):
        _, output = self.run_checks(stream=TerminalStream(), color=COLOR_NEVER)
        self.assertNotIn("\033[", output)

    def test_always_colors_a_plain_stream_despite_the_environment(self):
        environ = dict(self.workspace.environ, TERM="dumb", NO_COLOR="1")
        _, output = self.run_checks(environ=environ, color=COLOR_ALWAYS)
        self.assertIn(GREEN, output)

    def test_only_the_tags_and_the_summary_carry_color(self):
        self.workspace.paths.prefix.joinpath("bin", "startxfce4").unlink()
        _, output = self.run_checks(color=COLOR_ALWAYS)
        lines = output.splitlines()
        self.assertFalse(output.startswith("desk doctor"))
        for line in lines:
            for color, tag in ((GREEN, "[ok]"), (YELLOW, "[warn]"), (RED, "[fail]")):
                prefix = f"{color}{tag}{RESET}"
                if line.startswith(prefix):
                    rest = line[len(prefix):]
                    self.assertTrue(rest.startswith(" "), line)
                    self.assertNotIn("\033[", rest, line)
        hint_lines = [line for line in lines if line.startswith("       hint: ")]
        self.assertTrue(hint_lines)
        for line in hint_lines:
            self.assertNotIn("\033[", line)
        self.assertTrue(lines[-1].startswith(RED))
        self.assertTrue(lines[-1].endswith(RESET))


class LayoutTest(DoctorTestCase):
    """Tests for the line layout of the doctor report."""

    def test_the_report_starts_with_the_first_check(self):
        _, output = self.run_checks()
        self.assertNotIn("desk doctor", output)
        self.assertTrue(output.startswith("[ok]   termux environment: "))

    def test_check_lines_have_no_leading_spaces(self):
        lines = self.run_checks()[1].splitlines()
        for line in lines:
            if not line:
                continue
            if line.startswith(" "):
                self.assertTrue(line.startswith("       hint: "), line)
            else:
                self.assertTrue(
                    line.startswith("[") or line.startswith("Summary: "), line
                )

    def test_names_start_in_the_same_column_for_every_tag(self):
        self.workspace.paths.prefix.joinpath("bin", "startxfce4").unlink()
        _, output = self.run_checks(runner=RecordingRunner(pm_without_package))
        lines = output.splitlines()
        for name in ("termux environment", "Termux:X11 Android app", "startxfce4"):
            line = next(line for line in lines if name in line)
            with self.subTest(name=name):
                self.assertTrue(line.startswith("["), line)
                self.assertEqual(line.index(name), 7, line)
        self.assertIn("[ok]   termux environment:", output)
        self.assertIn("[warn] Termux:X11 Android app:", output)
        self.assertIn("[fail] startxfce4:", output)

    def test_hint_lines_align_with_the_name_column(self):
        self.workspace.paths.prefix.joinpath("bin", "startxfce4").unlink()
        _, output = self.run_checks()
        hint_lines = [line for line in output.splitlines() if "hint: " in line]
        self.assertTrue(hint_lines)
        for line in hint_lines:
            with self.subTest(line=line):
                self.assertEqual(line.index("hint: "), 7)
                self.assertEqual(len(line) - len(line.lstrip(" ")), 7)

    def test_summary_has_no_leading_spaces(self):
        _, output = self.run_checks()
        summary = output.splitlines()[-1]
        self.assertTrue(summary.startswith("Summary: "))
        self.assertFalse(summary.startswith(" "))


class RuntimeDirectoryTest(DoctorTestCase):
    """Tests for the XDG runtime directory check."""

    def runtime_dir(self):
        return self.workspace.paths.runtime_dir

    def test_missing_directory_is_ok(self):
        result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, OK)
        self.assertIn("desk start creates", result.message)

    def test_valid_directory_is_ok(self):
        directory = self.runtime_dir()
        directory.mkdir(mode=0o700)
        result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, OK)
        self.assertIn("mode 0700", result.message)

    def test_symlink_is_a_warning(self):
        directory = self.runtime_dir()
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.symlink_to(self.workspace.path)
        result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, WARNING)
        self.assertIn("symbolic link", result.message)
        self.assertTrue(directory.is_symlink())

    def test_file_is_a_warning(self):
        directory = self.runtime_dir()
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.write_text("not a directory", encoding="utf-8")
        result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, WARNING)
        self.assertIn("not a directory", result.message)

    def test_wrong_owner_is_a_warning(self):
        directory = self.runtime_dir()
        directory.mkdir(mode=0o700)
        with mock.patch(
            "termux_desk.doctor.os.getuid", return_value=os.getuid() + 1
        ):
            result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, WARNING)
        self.assertIn("owned by UID", result.message)
        self.assertIn("does not modify", result.hint or "")

    def test_wrong_mode_is_a_warning_and_is_not_changed(self):
        directory = self.runtime_dir()
        directory.mkdir()
        directory.chmod(0o755)
        result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, WARNING)
        self.assertIn("0755", result.message)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o755)

    def test_lstat_failure_is_a_warning(self):
        with mock.patch(
            "termux_desk.doctor.os.lstat", side_effect=OSError("permission denied")
        ):
            result = check_runtime_directory(self.workspace.paths)
        self.assertEqual(result.status, WARNING)
        self.assertIn("could not inspect", result.message)

    def test_missing_prefix_is_a_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = load_paths({"HOME": temporary})
            result = check_runtime_directory(paths)
        self.assertEqual(result.status, FAILED)

    def test_check_order_is_after_x11_socket_and_before_display(self):
        _, output = self.run_checks()
        x11 = output.index("X11 socket directory")
        runtime = output.index("XDG runtime directory")
        display = output.index("display :0")
        self.assertLess(x11, runtime)
        self.assertLess(runtime, display)


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
