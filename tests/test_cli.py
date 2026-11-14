"""Tests for the command line wiring and exit codes."""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from support import Workspace
from termux_desk import cli, session, vnc, webproxy
from termux_desk.config import ConfigError, DeskError
from termux_desk.session import StartError, StartInterrupted, StatusReport

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
CYAN = "\x1b[36m"
GRAY = "\x1b[90m"
HEADER = "\x1b[1;3;4;97m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"
STAMP = "2026-11-09T01:30:00+08:00"


class TerminalStream(io.StringIO):
    """A text stream that claims to be a terminal."""

    def isatty(self):
        return True


class CliHarness(unittest.TestCase):
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

    def run_cli_with_output(self, argv, output):
        errors = io.StringIO()
        with redirect_stderr(errors):
            exit_code = cli.main(argv, out=output)
        return exit_code, output.getvalue(), errors.getvalue()

class CliTest(CliHarness):
    def test_help_lists_every_command(self):
        help_text = cli.build_parser().format_help()
        for command in (
            "setup",
            "upgrade",
            "font",
            "doctor",
            "help",
            "container",
            "app",
            "pkg",
            "start",
            "status",
            "stop",
            "logs",
        ):
            self.assertIn(command, help_text)
        self.assertRegex(help_text, r"(?m)^    upgrade\s+")
        self.assertRegex(help_text, r"(?m)^    font\s+")
        self.assertRegex(help_text, r"(?m)^    help\s+")

    def test_help_exits_with_zero(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["--help"])
        self.assertEqual(raised.exception.code, 0)

    def test_help_command_prints_the_same_text_as_dash_h(self):
        dash_h_output = io.StringIO()
        with redirect_stdout(dash_h_output):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["-h"])
        self.assertEqual(raised.exception.code, 0)

        exit_code, output, errors = self.run_cli(["help"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, dash_h_output.getvalue())
        self.assertEqual(errors, "")

    def test_help_command_does_not_load_paths(self):
        with mock.patch("termux_desk.cli.load_paths") as load_paths:
            exit_code, output, errors = self.run_cli(["help"])
        self.assertEqual(exit_code, 0)
        load_paths.assert_not_called()
        self.assertIn("usage: desk", output)
        self.assertEqual(errors, "")

    def test_help_command_rejects_extra_arguments(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["help", "doctor"])
        self.assertEqual(raised.exception.code, 2)

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
        self.assertNotIn("\x1b[", output)

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

    def test_logs_default_has_no_ansi(self):
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(
            "[desk] note\nerror: broken\nwarning: odd\nfinished with status 1\n",
            encoding="utf-8",
        )
        exit_code, output, errors = self.run_cli(["logs"])
        self.assertEqual(exit_code, 0)
        self.assertNotIn("\x1b[", output)
        self.assertEqual(errors, "")

    def write_desk_log(self, text):
        self.workspace.paths.desk_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.desk_log.write_text(text, encoding="utf-8")

    def test_logs_color_always_colors_the_session_log(self):
        raw = (
            "[desk] session script\n"
            "=== desk start: error ===\n"
            "plain line\n"
            "error: broken\n"
            "warning: odd\n"
            "setup: finished with status 0\n"
            "[desk] error: broken\n"
        )
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        exit_code, output, errors = self.run_cli(["logs", "--color", "always"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            output,
            f"{CYAN}[desk]{RESET} session script\n"
            f"{HEADER}=== desk start: error ==={RESET}\n"
            "plain line\n"
            f"{RED}error{RESET}: broken\n"
            f"{YELLOW}warning{RESET}: odd\n"
            "setup: finished with status 0\n"
            f"{CYAN}[desk]{RESET} {RED}error{RESET}: broken\n",
        )
        self.assertNotIn(GREEN, output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)
        self.assertEqual(output.count("\n"), raw.count("\n"))
        self.assertTrue(output.endswith("\n"))

    def test_logs_color_uses_the_header_style_for_desk_headers(self):
        raw = (
            "=== desk start: finished with status 0 ===\n"
            "=== desk warning: error ===\n"
            "[desk] normal note\n"
        )
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        _, output, _ = self.run_cli(["logs", "--color", "always"])
        self.assertEqual(
            output,
            f"{HEADER}=== desk start: finished with status 0 ==={RESET}\n"
            f"{HEADER}=== desk warning: error ==={RESET}\n"
            f"{CYAN}[desk]{RESET} normal note\n",
        )
        self.assertNotIn("\x1b[35m", output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_session_logs_color_only_error_words_and_phrases(self):
        cases = (
            ("error: one", f"{RED}error{RESET}: one"),
            ("failed: two", f"{RED}failed{RESET}: two"),
            ("failure: three", f"{RED}failure{RESET}: three"),
            ("fatal: four", f"{RED}fatal{RESET}: four"),
            ("traceback: five", f"{RED}traceback{RESET}: five"),
            ("not found: six", f"{RED}not found{RESET}: six"),
            ("exited before: seven", f"{RED}exited before{RESET}: seven"),
            ("did not appear: eight", f"{RED}did not appear{RESET}: eight"),
            ("could not: nine", f"{RED}could not{RESET}: nine"),
            ("finished with status 7", f"{RED}finished with status 7{RESET}"),
            ("exited with status 0", "exited with status 0"),
        )
        raw = "".join(f"{text}\n" for text, _ in cases)
        expected = "".join(f"{colored}\n" for _, colored in cases)
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        _, output, _ = self.run_cli(["logs", "--color", "always"])
        self.assertEqual(output, expected)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_session_logs_color_only_warning_words(self):
        cases = (
            ("warning: one", f"{YELLOW}warning{RESET}: one"),
            ("warn: two", f"{YELLOW}warn{RESET}: two"),
            ("interrupted by signal 15", f"{YELLOW}interrupted{RESET} by signal 15"),
        )
        raw = "".join(f"{text}\n" for text, _ in cases)
        expected = "".join(f"{colored}\n" for _, colored in cases)
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        _, output, _ = self.run_cli(["logs", "--color", "always"])
        self.assertEqual(output, expected)
        self.assertNotIn(RED, output)
        self.assertNotIn(GREEN, output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_session_logs_highlight_keywords_without_whole_line_color(self):
        raw = "error: warning finished with status 0\n"
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        _, output, _ = self.run_cli(["logs", "--color", "always"])
        self.assertEqual(
            output,
            f"{RED}error{RESET}: {YELLOW}warning{RESET} finished with status 0\n",
        )
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_desk_logs_normal_lines_only_have_a_gray_timestamp(self):
        raw = (
            f"{STAMP} start: started\n"
            f"{STAMP} setup: finished with status 0\n"
            f"{STAMP} stop: unexpected thing\n"
        )
        self.write_desk_log(raw)

        exit_code, output, errors = self.run_cli(
            ["logs", "--desk", "--color", "always"]
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            output,
            f"{GRAY}{STAMP}{RESET} start: started\n"
            f"{GRAY}{STAMP}{RESET} setup: finished with status 0\n"
            f"{GRAY}{STAMP}{RESET} stop: unexpected thing\n",
        )
        for color in (GREEN, CYAN, RED, YELLOW):
            self.assertNotIn(color, output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_desk_logs_color_only_the_failed_word(self):
        raw = f"{STAMP} start: failed: boom\n"
        self.write_desk_log(raw)

        _, output, _ = self.run_cli(["logs", "--desk", "--color", "always"])
        self.assertEqual(
            output,
            f"{GRAY}{STAMP}{RESET} start: {RED}failed{RESET}: boom\n",
        )
        self.assertNotIn(f"{RED}start", output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_desk_logs_color_only_warning_words(self):
        raw = (
            f"{STAMP} start: interrupted by signal 15\n"
            f"{STAMP} start: warn: caution\n"
            f"{STAMP} start: warning: caution\n"
        )
        self.write_desk_log(raw)

        _, output, _ = self.run_cli(["logs", "--desk", "--color", "always"])
        self.assertEqual(
            output,
            f"{GRAY}{STAMP}{RESET} start: {YELLOW}interrupted{RESET} by signal 15\n"
            f"{GRAY}{STAMP}{RESET} start: {YELLOW}warn{RESET}: caution\n"
            f"{GRAY}{STAMP}{RESET} start: {YELLOW}warning{RESET}: caution\n",
        )
        self.assertNotIn(f"{YELLOW}start", output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_desk_logs_color_only_nonzero_status_phrases(self):
        raw = (
            f"{STAMP} start: finished with status 7\n"
            f"{STAMP} stop: exited with status 2\n"
        )
        self.write_desk_log(raw)

        _, output, _ = self.run_cli(["logs", "--desk", "--color", "always"])
        self.assertEqual(
            output,
            f"{GRAY}{STAMP}{RESET} start: {RED}finished with status 7{RESET}\n"
            f"{GRAY}{STAMP}{RESET} stop: {RED}exited with status 2{RESET}\n",
        )
        self.assertNotIn(f"{RED}start", output)
        self.assertNotIn(f"{RED}stop", output)
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)

    def test_desk_logs_never_has_no_ansi(self):
        raw = (
            f"{STAMP} start: started\n"
            f"{STAMP} start: failed: boom\n"
            f"{STAMP} start: finished with status 1\n"
        )
        self.write_desk_log(raw)

        exit_code, output, errors = self.run_cli_with_output(
            ["logs", "--desk", "--color", "never"], TerminalStream()
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, raw)
        self.assertNotIn("\x1b[", output)
        self.assertEqual(errors, "")

    def test_logs_never_keeps_a_terminal_plain(self):
        raw = "error: broken\nplain line\n"
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        exit_code, output, errors = self.run_cli_with_output(
            ["logs", "--color", "never"], TerminalStream()
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, raw)
        self.assertEqual(errors, "")

    def test_logs_auto_colors_a_terminal(self):
        raw = "error: broken\n"
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        exit_code, output, errors = self.run_cli_with_output(
            ["logs"], TerminalStream()
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(output.startswith(RED))
        self.assertEqual(ANSI_ESCAPE.sub("", output), raw)
        self.assertEqual(errors, "")

    def test_logs_color_preserves_a_missing_final_newline(self):
        raw = "error: broken"
        self.workspace.paths.session_log.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_log.write_text(raw, encoding="utf-8")

        _, colored, _ = self.run_cli(["logs", "--color", "always"])
        _, plain, _ = self.run_cli(["logs", "--color", "never"])
        self.assertEqual(plain, raw + "\n")
        self.assertEqual(ANSI_ESCAPE.sub("", colored), plain)
        self.assertEqual(colored.count("\n"), plain.count("\n"))
        self.assertTrue(colored.endswith("\n"))

    def test_logs_color_defaults_to_auto(self):
        parsed = cli.build_parser().parse_args(["logs"])
        self.assertEqual(parsed.color, "auto")

    def test_logs_color_accepts_each_value(self):
        for value in ("auto", "always", "never"):
            with self.subTest(value=value):
                parsed = cli.build_parser().parse_args(["logs", "--color", value])
                self.assertEqual(parsed.color, value)

    def test_logs_color_rejects_an_unknown_value(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["logs", "--color", "rainbow"])
        self.assertEqual(raised.exception.code, 2)

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

    def test_upgrade_calls_the_upgrade_module(self):
        with mock.patch("termux_desk.cli.upgrade.run_upgrade") as run_upgrade:
            exit_code, output, errors = self.run_cli(["upgrade"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        run_upgrade.assert_called_once()
        self.assertEqual(run_upgrade.call_args.args[0], self.workspace.paths)
        log_text = self.workspace.paths.desk_log.read_text(encoding="utf-8")
        self.assertIn("upgrade: started", log_text)
        self.assertIn("upgrade: finished with status 0", log_text)



    def test_font_remove_calls_the_fonts_module(self):
        with mock.patch("termux_desk.cli.fonts.remove_preference") as remove:
            exit_code, output, errors = self.run_cli(["font", "remove"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        remove.assert_called_once()
        self.assertEqual(remove.call_args.args[0], self.workspace.paths)
        log_text = self.workspace.paths.desk_log.read_text(encoding="utf-8")
        self.assertIn("font: started", log_text)
        self.assertIn("font: finished with status 0", log_text)

    def test_font_requires_an_action(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["font"])
        self.assertEqual(raised.exception.code, 2)

    def test_start_calls_the_session_module(self):
        with mock.patch("termux_desk.cli.session.start_session") as start:
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        start.assert_called_once()
        self.assertIs(start.call_args.kwargs["color"], False)

    def test_start_refreshes_the_menu_before_the_session(self):
        manager = mock.Mock()
        with mock.patch(
            "termux_desk.cli.menu.refresh_for_session", manager.refresh
        ), mock.patch("termux_desk.cli.session.start_session", manager.start):
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            [call[0] for call in manager.mock_calls], ["refresh", "start"]
        )
        self.assertEqual(manager.refresh.call_args.args[0], self.workspace.paths)

    def test_start_color_is_forwarded(self):
        with mock.patch("termux_desk.cli.session.start_session") as start:
            exit_code, output, errors = self.run_cli(["start", "--color", "always"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertIs(start.call_args.kwargs["color"], True)

    def test_start_auto_uses_stdout_for_progress(self):
        with mock.patch("termux_desk.cli.session.start_session") as start:
            exit_code, output, errors = self.run_cli_with_output(
                ["start"], TerminalStream()
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertIs(start.call_args.kwargs["color"], True)

    def test_start_error_is_reported_and_logged(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartError("the desktop did not start"),
        ):
            exit_code, output, errors = self.run_cli(["start"])
        self.assertEqual(exit_code, 1)
        self.assertIn("desk: error: the desktop did not start", errors)
        self.assertNotIn("\x1b[", errors)
        log_text = self.workspace.paths.desk_log.read_text(encoding="utf-8")
        self.assertIn("start: failed: the desktop did not start", log_text)

    def test_start_error_with_color_colors_only_the_error_prefix(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartError("the desktop did not start"),
        ):
            exit_code, output, errors = self.run_cli(["start", "--color", "always"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(errors, f"desk: {RED}error:{RESET} the desktop did not start\n")

    def test_start_error_with_color_never_has_no_ansi(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartError("the desktop did not start"),
        ):
            exit_code, output, errors = self.run_cli(["start", "--color", "never"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(errors, "desk: error: the desktop did not start\n")

    def test_start_error_auto_uses_stderr_not_stdout(self):
        stdout = io.StringIO()
        stderr = TerminalStream()
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartError("the desktop did not start"),
        ), redirect_stderr(stderr):
            exit_code = cli.main(["start"], out=stdout)
        self.assertEqual(exit_code, 1)
        self.assertEqual(
            stderr.getvalue(),
            f"desk: {RED}error:{RESET} the desktop did not start\n",
        )

    def test_start_interrupted_with_color_colors_only_interrupted(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=StartInterrupted(15),
        ):
            exit_code, output, errors = self.run_cli(["start", "--color", "always"])
        self.assertEqual(exit_code, 128 + 15)
        self.assertEqual(errors, f"desk: {YELLOW}interrupted{RESET} by signal 15.\n")

    def test_start_keyboard_interrupt_with_color_colors_only_interrupted(self):
        with mock.patch(
            "termux_desk.cli.session.start_session",
            side_effect=KeyboardInterrupt,
        ):
            exit_code, output, errors = self.run_cli(["start", "--color", "always"])
        self.assertEqual(exit_code, 130)
        self.assertEqual(errors, f"desk: {YELLOW}interrupted{RESET}.\n")

    def test_other_command_errors_stay_plain(self):
        with mock.patch(
            "termux_desk.cli.session.stop_session",
            side_effect=DeskError("the session did not stop"),
        ):
            exit_code, output, errors = self.run_cli(["stop"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(errors, "desk: error: the session did not stop\n")
        self.assertNotIn("\x1b[", errors)

    def test_doctor_exit_code_is_forwarded(self):
        with mock.patch("termux_desk.cli.doctor.run_doctor", return_value=1) as run_doctor:
            exit_code, output, errors = self.run_cli(["doctor"])
        self.assertEqual(exit_code, 1)
        run_doctor.assert_called_once()
        self.assertEqual(errors, "")

    def test_doctor_vnc_flag_narrows_to_vnc(self):
        with mock.patch(
            "termux_desk.cli.doctor.run_doctor", return_value=0
        ) as run_doctor:
            exit_code, output, errors = self.run_cli(["doctor", "--vnc"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(run_doctor.call_args.kwargs["vnc_only"])
        self.assertEqual(errors, "")

    def test_doctor_color_defaults_to_auto(self):
        parsed = cli.build_parser().parse_args(["doctor"])
        self.assertEqual(parsed.color, "auto")

    def test_doctor_color_accepts_each_value(self):
        for value in ("auto", "always", "never"):
            with self.subTest(value=value):
                parsed = cli.build_parser().parse_args(["doctor", "--color", value])
                self.assertEqual(parsed.color, value)

    def test_doctor_color_rejects_an_unknown_value(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["doctor", "--color", "rainbow"])
        self.assertEqual(raised.exception.code, 2)

    def test_doctor_color_is_forwarded(self):
        with mock.patch(
            "termux_desk.cli.doctor.run_doctor", return_value=0
        ) as run_doctor:
            exit_code, output, errors = self.run_cli(["doctor", "--color", "always"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(run_doctor.call_args.kwargs["color"], "always")
        self.assertEqual(errors, "")

    def test_start_color_defaults_to_auto(self):
        parsed = cli.build_parser().parse_args(["start"])
        self.assertEqual(parsed.color, "auto")

    def test_start_color_accepts_each_value(self):
        for value in ("auto", "always", "never"):
            with self.subTest(value=value):
                parsed = cli.build_parser().parse_args(["start", "--color", value])
                self.assertEqual(parsed.color, value)

    def test_start_color_rejects_an_unknown_value(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli(["start", "--color", "rainbow"])
        self.assertEqual(raised.exception.code, 2)

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


class StopScopeTest(CliHarness):
    """desk stop reaches the web proxy, the :0 mirror, and the session."""

    def _write_states(self):
        paths = self.workspace.paths
        webproxy.write_web_state(
            paths,
            webproxy.WebProxyState(
                display=":0",
                backend="rs",
                host="127.0.0.1",
                port=6080,
                web_pid=11,
                static_pid=12,
            ),
        )
        webproxy.write_web_state(
            paths,
            webproxy.WebProxyState(
                display=":1",
                backend="py",
                host="127.0.0.1",
                port=6090,
                web_pid=21,
            ),
        )
        vnc.write_mirror_state(
            paths,
            vnc.MirrorState(display=":0", pid=31, port=5900),
        )
        for target in (paths, paths.with_display(":1")):
            target.session_file.parent.mkdir(parents=True, exist_ok=True)
            target.session_file.write_text("{}", encoding="utf-8")

    def test_stop_all_stops_web_mirror_and_sessions_in_order(self):
        self._write_states()
        events = []

        def web_stop(paths, *, display, out, **kwargs):
            events.append(("web", display))

        def mirror_stop(paths, *, display, out):
            events.append(("mirror", display))

        def session_stop(target, *, out, **kwargs):
            events.append(("session", target.display))

        with mock.patch("termux_desk.cli.webproxy.run_web_stop", side_effect=web_stop), mock.patch(
            "termux_desk.cli.vnc.run_mirror_stop", side_effect=mirror_stop
        ), mock.patch("termux_desk.cli.session.stop_session", side_effect=session_stop):
            exit_code, output, errors = self.run_cli(["stop", "--all"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            events,
            [
                ("web", ":0"),
                ("web", ":1"),
                ("mirror", ":0"),
                ("session", ":0"),
                ("session", ":1"),
            ],
        )

    def test_stop_display_0_stops_only_that_display(self):
        self._write_states()
        events = []

        with mock.patch(
            "termux_desk.cli.webproxy.run_web_stop",
            side_effect=lambda paths, *, display, out, **kwargs: events.append(("web", display)),
        ), mock.patch(
            "termux_desk.cli.vnc.run_mirror_stop",
            side_effect=lambda paths, *, display, out, **kwargs: events.append(("mirror", display)),
        ), mock.patch(
            "termux_desk.cli.session.stop_session",
            side_effect=lambda target, *, out, **kwargs: events.append(("session", target.display)),
        ):
            exit_code, output, errors = self.run_cli(["stop", "--display", ":0"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(
            events,
            [("web", ":0"), ("mirror", ":0"), ("session", ":0")],
        )

    def test_stop_all_with_only_a_web_proxy(self):
        paths = self.workspace.paths
        webproxy.write_web_state(
            paths,
            webproxy.WebProxyState(
                display=":0",
                backend="rs",
                host="127.0.0.1",
                port=6080,
                web_pid=11,
            ),
        )
        with mock.patch("termux_desk.cli.webproxy.run_web_stop") as web_stop, mock.patch(
            "termux_desk.cli.session.stop_session"
        ) as session_stop:
            exit_code, output, errors = self.run_cli(["stop", "--all"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        web_stop.assert_called_once()
        session_stop.assert_not_called()
        self.assertNotIn("No desktop session is recorded.", output)

    def test_stop_all_without_states_reports_and_exits_zero(self):
        exit_code, output, errors = self.run_cli(["stop", "--all"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertIn("No desktop session is recorded.", output)

    def test_a_failing_step_does_not_hide_the_rest(self):
        self._write_states()
        events = []

        def session_stop(target, *, out, **kwargs):
            raise session.StopError(f"boom on {target.display}")

        with mock.patch(
            "termux_desk.cli.webproxy.run_web_stop",
            side_effect=lambda paths, *, display, out, **kwargs: events.append(("web", display)),
        ), mock.patch(
            "termux_desk.cli.vnc.run_mirror_stop",
            side_effect=lambda paths, *, display, out, **kwargs: events.append(("mirror", display)),
        ), mock.patch(
            "termux_desk.cli.session.stop_session", side_effect=session_stop
        ):
            exit_code, output, errors = self.run_cli(["stop", "--all"])

        self.assertEqual(exit_code, 1)
        self.assertIn("desk: error:", errors)
        self.assertIn("boom on :0", errors)
        self.assertIn("boom on :1", errors)
        self.assertEqual(events, [("web", ":0"), ("web", ":1"), ("mirror", ":0")])


class VersionTest(CliHarness):
    def test_version_keeps_the_first_line_and_adds_the_vnc_section(self):
        with mock.patch(
            "termux_desk.cli.vnc.vnc_components_installed", return_value=True
        ), mock.patch(
            "termux_desk.cli.vnc.version_lines", return_value=("Xvnc: fake 1.0",)
        ):
            exit_code, output, errors = self.run_cli(["version"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertTrue(output.splitlines()[0].startswith("desk "))
        self.assertIn("Xvnc: fake 1.0", output)

    def test_version_reports_vnc_as_optional_when_absent(self):
        with mock.patch(
            "termux_desk.cli.vnc.vnc_components_installed", return_value=False
        ):
            exit_code, output, errors = self.run_cli(["version"])
        self.assertEqual(exit_code, 0)
        self.assertIn("VNC: not installed (optional).", output)

    def test_version_vnc_prints_only_the_vnc_components(self):
        with mock.patch("termux_desk.cli.vnc.run_vnc_version") as run_version:
            exit_code, output, errors = self.run_cli(["version", "--vnc"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        run_version.assert_called_once()

    def test_version_vnc_flag_defaults_to_false(self):
        parsed = cli.build_parser().parse_args(["version"])
        self.assertFalse(parsed.vnc)


class VncAggregateTest(CliHarness):
    """The parent VNC commands aggregate the server, mirror, and web proxy."""

    def _write_web(self, display, port):
        webproxy.write_web_state(
            self.workspace.paths,
            webproxy.WebProxyState(
                display=display,
                backend="rs",
                host="127.0.0.1",
                port=port,
                web_pid=11,
                static_pid=12,
            ),
        )

    def test_vnc_doctor_runs_the_vnc_checks(self):
        with mock.patch(
            "termux_desk.cli.vnc.run_vnc_doctor", return_value=1
        ) as run_vnc_doctor:
            exit_code, output, errors = self.run_cli(["vnc", "doctor"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(errors, "")
        run_vnc_doctor.assert_called_once()

    def test_vnc_doctor_color_defaults_to_auto(self):
        parsed = cli.build_parser().parse_args(["vnc", "doctor"])
        self.assertEqual(parsed.color, "auto")

    def test_vnc_status_reports_every_display(self):
        paths = self.workspace.paths
        vnc.write_mirror_state(paths, vnc.MirrorState(display=":0", pid=31, port=5900))
        self._write_web(":1", 6080)
        calls = []

        def run_status(paths, *, display):
            calls.append(display)
            return StatusReport(running=True, lines=(f"fake status for {display}",))

        def run_web_status(paths, *, display, out):
            print(f"web status for {display}", file=out)
            return True

        with mock.patch(
            "termux_desk.cli.vnc.run_vnc_status", side_effect=run_status
        ), mock.patch(
            "termux_desk.cli.webproxy.run_web_status", side_effect=run_web_status
        ):
            exit_code, output, errors = self.run_cli(["vnc", "status"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertEqual(calls, [":0", ":1"])
        self.assertIn("Display :0:", output)
        self.assertIn("fake status for :0", output)
        self.assertIn("The web proxy for :0 is not running.", output)
        self.assertIn("Display :1:", output)
        self.assertIn("fake status for :1", output)
        self.assertIn("web status for :1", output)

    def test_vnc_status_with_display_only_reports_it(self):
        self._write_web(":1", 6080)
        with mock.patch(
            "termux_desk.cli.vnc.run_vnc_status",
            return_value=StatusReport(running=False, lines=("not running",)),
        ) as run_status, mock.patch(
            "termux_desk.cli.webproxy.run_web_status", return_value=False
        ) as run_web_status:
            exit_code, output, errors = self.run_cli(
                ["vnc", "status", "--display", ":1"]
            )

        self.assertEqual(exit_code, 1)
        run_status.assert_called_once()
        run_web_status.assert_called_once()
        self.assertIn("not running", output)
        self.assertNotIn("Display :0:", output)

    def test_vnc_status_without_any_state(self):
        exit_code, output, errors = self.run_cli(["vnc", "status"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(errors, "")
        self.assertIn("No VNC display is recorded.", output)

    def test_vnc_logs_aggregate_the_mirror_and_sessions(self):
        paths = self.workspace.paths
        vnc.write_mirror_state(paths, vnc.MirrorState(display=":0", pid=31, port=5900))
        self._write_web(":1", 6080)
        paths.with_display(":1").session_log.parent.mkdir(parents=True, exist_ok=True)
        paths.with_display(":1").session_log.write_text("session line\n", encoding="utf-8")
        vnc.mirror_log_path(paths, ":0").write_text("mirror line\n", encoding="utf-8")

        exit_code, output, errors = self.run_cli(["vnc", "logs", "-n", "5"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        self.assertIn("== :0 mirror log ==", output)
        self.assertIn("mirror line", output)
        self.assertIn("== :1 session log ==", output)
        self.assertIn("session line", output)

    def test_vnc_logs_web_reads_the_proxy_log(self):
        self._write_web(":0", 6080)
        webproxy.log_path(self.workspace.paths, ":0").write_text(
            "web line\n", encoding="utf-8"
        )

        exit_code, output, errors = self.run_cli(["vnc", "logs", "--web"])

        self.assertEqual(exit_code, 0)
        self.assertIn("web line", output)
        self.assertNotIn("== :0 web log ==", output)

    def test_vnc_version_dispatches(self):
        with mock.patch("termux_desk.cli.vnc.run_vnc_version") as run_version:
            exit_code, output, errors = self.run_cli(["vnc", "version"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(errors, "")
        run_version.assert_called_once()

    def test_vnc_setup_web_installs_the_page(self):
        with mock.patch("termux_desk.cli.vnc.run_vnc_setup") as setup, mock.patch(
            "termux_desk.cli.webproxy.run_web_setup"
        ) as web_setup:
            exit_code, output, errors = self.run_cli(["vnc", "setup", "--web"])

        self.assertEqual(exit_code, 0)
        setup.assert_called_once()
        web_setup.assert_called_once()

    def test_vnc_setup_without_web_skips_the_page(self):
        with mock.patch("termux_desk.cli.vnc.run_vnc_setup") as setup, mock.patch(
            "termux_desk.cli.webproxy.run_web_setup"
        ) as web_setup:
            exit_code, output, errors = self.run_cli(["vnc", "setup"])

        self.assertEqual(exit_code, 0)
        setup.assert_called_once()
        web_setup.assert_not_called()
