"""Tests for the command line wiring and exit codes."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from support import Workspace
from termux_desk import cli
from termux_desk.config import ConfigError
from termux_desk.session import StartError, StartInterrupted, StatusReport


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        patched = mock.patch.dict(os.environ, self.workspace.environ, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def run_cli(self, argv):
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = cli.main(argv)
        return exit_code, output.getvalue(), errors.getvalue()

    def test_help_lists_every_command(self):
        help_text = cli.build_parser().format_help()
        for command in ("setup", "doctor", "start", "status", "stop", "logs"):
            self.assertIn(command, help_text)

    def test_help_exits_with_zero(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_unknown_command_exits_with_two(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["launch"])
        self.assertEqual(raised.exception.code, 2)

    def test_status_without_state_exits_with_one(self):
        exit_code, output, errors = self.run_cli(["status"])
        self.assertEqual(exit_code, 1)
        self.assertIn("Session is not running.", output)
        self.assertEqual(errors, "")

    def test_status_with_running_session_exits_with_zero(self):
        with mock.patch(
            "termux_desk.cli.session.session_status",
            return_value=StatusReport(True, ("Session is running.",)),
        ):
            exit_code, output, errors = self.run_cli(["status"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Session is running.", output)
        self.assertEqual(errors, "")

    def test_status_with_broken_state_exits_with_one(self):
        self.workspace.paths.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_file.write_text("{oops", encoding="utf-8")
        exit_code, output, errors = self.run_cli(["status"])
        self.assertEqual(exit_code, 1)
        self.assertIn("desk: error:", errors)
        self.assertIn("not valid JSON", errors)

    def test_stop_without_session_succeeds(self):
        exit_code, output, errors = self.run_cli(["stop"])
        self.assertEqual(exit_code, 0)
        self.assertIn("No desk session is running.", output)
        self.assertEqual(errors, "")
        self.assertIn("stop: started", self.workspace.paths.desk_log.read_text(encoding="utf-8"))

    def test_logs_default_prints_the_last_hundred_lines(self):
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(
            "".join(f"line {index}\n" for index in range(1, 151)),
            encoding="utf-8",
        )
        exit_code, output, errors = self.run_cli(["logs"])
        self.assertEqual(exit_code, 0)
        lines = output.splitlines()
        self.assertEqual(len(lines), 100)
        self.assertEqual(lines[0], "line 51")
        self.assertEqual(lines[-1], "line 150")
        self.assertEqual(errors, "")

    def test_logs_honours_the_line_count(self):
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text("one\ntwo\nthree\n", encoding="utf-8")
        exit_code, output, _ = self.run_cli(["logs", "--lines", "2"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "two\nthree\n")

    def test_logs_desk_flag_reads_the_command_log(self):
        self.workspace.paths.desk_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.desk_log.write_text("2026-11-09 start: started\n", encoding="utf-8")
        exit_code, output, _ = self.run_cli(["logs", "--desk"])
        self.assertEqual(exit_code, 0)
        self.assertIn("start: started", output)

    def test_logs_without_a_file_succeeds_with_a_message(self):
        exit_code, output, errors = self.run_cli(["logs"])
        self.assertEqual(exit_code, 0)
        self.assertIn("No log file at", output)
        self.assertEqual(errors, "")

    def test_logs_negative_line_count_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["logs", "--lines", "-1"])
        self.assertEqual(raised.exception.code, 2)

    def test_logs_zero_lines_prints_nothing(self):
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text("line\n", encoding="utf-8")
        exit_code, output, errors = self.run_cli(["logs", "--lines", "0"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "")
        self.assertEqual(errors, "")

    def test_start_interrupted_signal_is_reported(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartInterrupted(15),
        ):
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 128 + 15)
        self.assertIn("interrupted by signal 15", errors)

    def test_setup_calls_the_setup_module(self):
        with mock.patch("termux_desk.cli.setup.run_setup") as run_setup:
            exit_code, output, errors = self.run_cli(["setup"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        run_setup.assert_called_once()
        self.assertEqual(run_setup.call_args.args[0], self.workspace.paths)
        log_text = self.workspace.paths.desk_log.read_text(encoding="utf-8")
        self.assertIn("setup: started", log_text)
        self.assertIn("setup: finished with status 0", log_text)

    def test_start_calls_the_session_module(self):
        with mock.patch("termux_desk.cli.session.start_session") as start:
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        start.assert_called_once()

    def test_start_error_is_reported_and_logged(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartError("the desktop did not start"),
        ):
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 1)
        self.assertIn("desk: error: the desktop did not start", errors)
        log_text = self.workspace.paths.desk_log.read_text(encoding="utf-8")
        self.assertIn("start: failed: the desktop did not start", log_text)

    def test_doctor_exit_code_is_forwarded(self):
        with mock.patch("termux_desk.cli.doctor.run_doctor", return_value=1) as run_doctor:
            exit_code, output, errors = self.run_cli(["doctor"])
        self.assertEqual(exit_code, 1)
        run_doctor.assert_called_once()
        self.assertEqual(errors, "")

    def test_missing_home_is_reported(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            exit_code, output, errors = self.run_cli(["status"])
        self.assertEqual(exit_code, 1)
        self.assertIn("HOME", errors)

    def test_config_error_type_is_caught(self):
        with mock.patch("termux_desk.cli.load_paths", side_effect=ConfigError("no home")):
            exit_code, output, errors = self.run_cli(["status"])
        self.assertEqual(exit_code, 1)
        self.assertIn("desk: error: no home", errors)


if __name__ == "__main__":
    unittest.main()
