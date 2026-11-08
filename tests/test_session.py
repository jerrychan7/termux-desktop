"""Tests for session startup, stop, status, verification, and the script."""

from __future__ import annotations

import io
import os
import re
import signal
import socket
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from support import (
    BOOT_ID,
    LEADER_STARTTIME,
    SESSION_ID,
    FakeRuntime,
    Workspace,
    make_state,
)
from termux_desk import session
from termux_desk.config import DISPLAY_NAME
from termux_desk.session import (
    VERIFICATION_RUNNING,
    VERIFICATION_STALE,
    VERIFICATION_UNVERIFIABLE,
    ProcessScanError,
    SessionError,
    StartError,
    StartInterrupted,
    StopError,
    Verification,
    clear_stale_display_files,
    process_group_members,
    process_start_time,
    render_session_script,
    session_environment,
    session_in_group,
    session_status,
    start_session,
    stop_session,
    verify_session,
    write_session_script,
    x11_socket_ready,
)
from termux_desk.state import LockError, StateError, read_session_state, state_lock


def stat_line(pid, pgid, *, state="S", starttime=LEADER_STARTTIME, comm="bash"):
    """Build a /proc/<pid>/stat line with the fields that desk reads."""
    fields = [
        state,
        "1",
        str(pgid),
        str(pgid),
        "0",
        "-1",
        "4194560",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "20",
        "0",
        "1",
        "0",
        str(starttime),
        "0",
        "0",
    ]
    return f"{pid} ({comm}) " + " ".join(fields) + "\n"


class TemplateRenderingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_render_fills_every_placeholder(self):
        text = render_session_script(self.workspace.paths)
        self.assertIsNone(re.search(r"@[A-Z][A-Z0-9_]*@", text))
        self.assertIn(str(self.workspace.paths.prefix), text)

    def test_rendered_script_starts_with_the_prefix_bash(self):
        text = render_session_script(self.workspace.paths)
        first_line = text.splitlines()[0]
        self.assertEqual(first_line, f"#!{self.workspace.paths.prefix}/bin/bash")

    def test_rendered_script_has_the_m0_commands(self):
        text = render_session_script(self.workspace.paths)
        self.assertIn("export DISPLAY=:0", text)
        self.assertIn("termux-x11 \"$DISPLAY\"", text)
        self.assertIn(
            "am start --user 0 -n com.termux.x11/com.termux.x11.MainActivity", text
        )
        self.assertIn("dbus-launch --exit-with-session startxfce4", text)
        self.assertIn('while [ ! -S "$X11_SOCKET" ]', text)
        self.assertIn(f"X11_SOCKET={self.workspace.paths.x11_socket}", text)
        self.assertIn("trap 'stop_termux_x11' EXIT", text)

    def test_rendered_script_sets_the_required_environment(self):
        text = render_session_script(self.workspace.paths)
        prefix = self.workspace.paths.prefix
        self.assertIn(f"export PREFIX={prefix}", text)
        self.assertIn(f"export HOME={self.workspace.paths.home}", text)
        self.assertIn(f'export XDG_RUNTIME_DIR={prefix / "tmp"}', text)
        self.assertIn(f'export XDG_CONFIG_DIRS={prefix / "etc" / "xdg"}', text)
        self.assertIn(f'export XDG_DATA_DIRS={prefix / "share"}', text)
        self.assertIn('export PATH="$PREFIX/bin${PATH:+:$PATH}"', text)

    def test_rendered_script_starts_no_extra_services(self):
        text = render_session_script(self.workspace.paths)
        for word in ("pulseaudio", "vnc", "termux-wake-lock", "systemctl", "pkill"):
            self.assertNotIn(word, text)
        self.assertNotIn("\nexec ", text)

    def test_template_can_use_a_different_bash_prefix(self):
        text = render_session_script(self.workspace.paths, "#!@BASH@\n# done\n")
        self.assertEqual(text.splitlines()[0], f"#!{self.workspace.paths.prefix}/bin/bash")

    def test_replacement_runs_in_one_pass(self):
        paths = replace(self.workspace.paths, home=Path("/tmp/home-@DISPLAY@"))
        text = render_session_script(paths, "# home @HOME@\n# display @DISPLAY@\n")
        self.assertIn("/tmp/home-@DISPLAY@", text)
        self.assertIn("# display :0", text)

    def test_unknown_placeholder_is_rejected(self):
        with self.assertRaises(SessionError) as raised:
            render_session_script(self.workspace.paths, "line @NOT_FILLED@\n")
        self.assertIn("@NOT_FILLED@", str(raised.exception))

    def test_render_requires_a_prefix(self):
        paths = replace(self.workspace.paths, prefix=None)
        with self.assertRaises(SessionError):
            render_session_script(paths)

    def test_write_session_script_sets_the_executable_bit(self):
        script = write_session_script(self.workspace.paths)
        self.assertEqual(script, self.workspace.paths.session_script)
        self.assertTrue(script.is_file())
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertEqual(script.parent, self.workspace.paths.state_dir)
        self.assertEqual(list(script.parent.glob("session.sh.*.tmp")), [])

    def test_session_environment_sets_the_fixed_values(self):
        environment = session_environment(
            self.workspace.paths, SESSION_ID, {"CUSTOM": "kept", "PATH": "/bin"}
        )
        self.assertEqual(environment["DISPLAY"], DISPLAY_NAME)
        self.assertEqual(environment["PREFIX"], str(self.workspace.paths.prefix))
        self.assertEqual(environment["HOME"], str(self.workspace.paths.home))
        self.assertEqual(environment["XDG_RUNTIME_DIR"], str(self.workspace.paths.prefix / "tmp"))
        self.assertEqual(
            environment["XDG_CONFIG_DIRS"],
            str(self.workspace.paths.prefix / "etc" / "xdg"),
        )
        self.assertEqual(environment["XDG_DATA_DIRS"], str(self.workspace.paths.prefix / "share"))
        self.assertEqual(environment["DESK_SESSION_ID"], SESSION_ID)
        self.assertEqual(environment["CUSTOM"], "kept")


class StartTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_required_programs()

    def start(self, runtime, **kwargs):
        output = io.StringIO()
        result = start_session(
            self.workspace.paths,
            runtime=runtime.runtime,
            environ=self.workspace.environ,
            out=output,
            **kwargs,
        )
        return result, output.getvalue()

    def test_missing_programs_ask_for_desk_setup_and_start_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(Path(temporary))
            workspace.add_program("termux-x11")
            runtime = FakeRuntime()
            with self.assertRaises(StartError) as raised:
                start_session(
                    workspace.paths,
                    runtime=runtime.runtime,
                    environ=workspace.environ,
                    out=io.StringIO(),
                )
        message = str(raised.exception)
        self.assertIn("missing programs", message)
        self.assertIn("desk setup", message)
        self.assertEqual(runtime.spawned, [])
        self.assertFalse(workspace.paths.session_script.exists())
        self.assertIsNone(read_session_state(workspace.paths.session_file))

    def test_missing_am_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", "am").unlink()
        runtime = FakeRuntime()
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("am", str(raised.exception))
        self.assertEqual(runtime.spawned, [])

    def test_running_session_is_not_started_twice(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), verified=True)
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("already running", str(raised.exception))
        self.assertEqual(runtime.spawned, [])

    def test_stale_state_from_an_earlier_boot_is_removed(self):
        self.workspace.write_state(make_state(boot_id="earlier-boot"))
        runtime = FakeRuntime(members=(4242,), verified=False)
        state, output = self.start(runtime)
        self.assertIn("Removed the stale state file", output)
        self.assertEqual(state.pgid, 4242)
        self.assertEqual(read_session_state(self.workspace.paths.session_file).status, "running")

    def test_stale_state_without_processes_is_removed(self):
        self.workspace.write_state(make_state(pgid=9999, pid=9999))
        runtime = FakeRuntime(members=())
        state, output = self.start(runtime)
        self.assertIn("Removed the stale state file", output)
        self.assertEqual(state.pgid, 4242)

    def test_stale_state_removal_failure_is_reported(self):
        self.workspace.write_state(make_state(boot_id="earlier-boot"))
        runtime = FakeRuntime(members=(4242,), verified=False)
        with mock.patch(
            "termux_desk.session.remove_session_state", side_effect=OSError("read only")
        ):
            with self.assertRaises(StartError) as raised:
                self.start(runtime)
        self.assertIn("could not remove the stale state file", str(raised.exception))
        self.assertTrue(self.workspace.paths.session_file.exists())
        self.assertEqual(runtime.spawned, [])

    def test_unverifiable_state_stops_start(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), verified=False)
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("cannot verify", str(raised.exception))
        self.assertIn("desk start refuses", str(raised.exception))
        self.assertEqual(runtime.spawned, [])
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_display_in_use_by_a_listener_is_refused(self):
        runtime = FakeRuntime(socket_in_use=True)
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("already in use", str(raised.exception))
        self.assertEqual(runtime.spawned, [])

    def test_display_in_use_by_a_live_lock_file_is_refused(self):
        lock_path = self.workspace.paths.x11_lock
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("777\n", encoding="utf-8")
        runtime = FakeRuntime(process_alive=True)
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("lock file", str(raised.exception))
        self.assertEqual(runtime.spawned, [])

    def test_stale_display_files_are_removed_before_start(self):
        socket_path = self.workspace.paths.x11_socket
        lock_path = self.workspace.paths.x11_lock
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("stale socket file", encoding="utf-8")
        lock_path.write_text("999999\n", encoding="utf-8")
        runtime = FakeRuntime(members=(4242,))
        state, output = self.start(runtime)
        self.assertIn("Removed the stale X11 socket", output)
        self.assertIn("Removed the stale X lock file", output)
        self.assertFalse(socket_path.exists())
        self.assertFalse(lock_path.exists())
        self.assertEqual(state.status, "running")

    def test_success_records_the_verification_facts(self):
        runtime = FakeRuntime(members=(4242,))
        state, output = self.start(runtime)

        self.assertEqual(state.status, "running")
        self.assertEqual(state.pid, 4242)
        self.assertEqual(state.pgid, 4242)
        self.assertEqual(state.display, DISPLAY_NAME)
        self.assertEqual(state.boot_id, BOOT_ID)
        self.assertEqual(state.leader_starttime, LEADER_STARTTIME)
        self.assertEqual(len(state.session_id), 32)
        self.assertEqual(read_session_state(self.workspace.paths.session_file), state)

        self.assertEqual(len(runtime.spawned), 1)
        spawned = runtime.spawned[0]
        self.assertEqual(spawned["script"], self.workspace.paths.session_script)
        self.assertEqual(spawned["session_id"], state.session_id)
        self.assertEqual(spawned["log_path"], self.workspace.paths.session_log)
        self.assertEqual(spawned["env"]["DISPLAY"], DISPLAY_NAME)
        self.assertEqual(spawned["env"]["DESK_SESSION_ID"], state.session_id)
        self.assertIn("Session is running.", output)
        self.assertIn("4242", output)
        self.assertTrue(self.workspace.paths.session_script.is_file())

    def test_readiness_requires_the_socket_the_x11_process_and_xfce(self):
        for runtime in (
            FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,), socket_ready=False),
            FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,), x11=False),
            FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,), xfce=False),
        ):
            with self.subTest(runtime=runtime):
                with self.assertRaises(StartError) as raised:
                    self.start(
                        runtime,
                        ready_timeout=0,
                        terminate_timeout=0,
                        kill_timeout=0,
                    )
                self.assertIn("not ready after 0 seconds", str(raised.exception))
                self.assertEqual(runtime.group.signals, [signal.SIGTERM])
                self.assertIsNone(read_session_state(self.workspace.paths.session_file))

    def test_script_exit_before_readiness_is_reported(self):
        runtime = FakeRuntime(
            members=(),
            socket_ready=False,
            x11=False,
            xfce=False,
            exit_code=1,
        )
        with self.assertRaises(StartError) as raised:
            self.start(runtime, ready_timeout=0, terminate_timeout=0, kill_timeout=0)
        message = str(raised.exception)
        self.assertIn("exited with status 1", message)
        self.assertIn("Termux:X11 app", message)
        self.assertEqual(runtime.group.signals, [])
        self.assertIsNone(read_session_state(self.workspace.paths.session_file))

    def test_failed_cleanup_keeps_the_state_and_reports_the_rest(self):
        runtime = FakeRuntime(members=(4242,), socket_ready=False, x11=False, xfce=False)
        with self.assertRaises(StartError) as raised:
            self.start(
                runtime,
                ready_timeout=0,
                terminate_timeout=0,
                kill_timeout=0,
            )
        message = str(raised.exception)
        self.assertIn("still has processes", message)
        self.assertIn("4242", message)
        self.assertIn("desk stop", message)
        self.assertEqual(runtime.group.signals, [signal.SIGTERM, signal.SIGKILL])
        state = read_session_state(self.workspace.paths.session_file)
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "failed")

    def test_state_write_failure_stops_the_group(self):
        runtime = FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,))
        with mock.patch(
            "termux_desk.session.write_session_state",
            side_effect=OSError("disk is full"),
        ):
            with self.assertRaises(StartError) as raised:
                self.start(runtime, ready_timeout=0, terminate_timeout=0, kill_timeout=0)
        self.assertIn("could not write the state file", str(raised.exception))
        self.assertEqual(runtime.group.signals, [signal.SIGTERM])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_unusable_spawn_result_is_rejected(self):
        runtime = FakeRuntime(pgid=1, members=(1,))
        with self.assertRaises(StartError) as raised:
            self.start(runtime)
        self.assertIn("unusable PID", str(raised.exception))
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_missing_leader_start_time_stops_the_start(self):
        runtime = FakeRuntime(
            members=(4242,), clear_on=(signal.SIGTERM,), start_time=None
        )
        with self.assertRaises(StartError) as raised:
            self.start(runtime, ready_timeout=0, terminate_timeout=0, kill_timeout=0)
        self.assertIn("could not read the start time", str(raised.exception))
        self.assertEqual(runtime.group.signals, [signal.SIGTERM])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_boot_id_read_failure_stops_before_spawn(self):
        def unreadable():
            raise SessionError("could not read the boot id")

        runtime = FakeRuntime(read_boot_id_callable=unreadable)
        with self.assertRaises(SessionError):
            self.start(runtime)
        self.assertEqual(runtime.spawned, [])

    def test_keyboard_interrupt_after_spawn_cleans_the_group(self):
        def interrupt():
            raise KeyboardInterrupt()

        runtime = FakeRuntime(
            members=(4242,),
            clear_on=(signal.SIGTERM,),
            now_callable=interrupt,
        )
        with self.assertRaises(KeyboardInterrupt):
            self.start(runtime, ready_timeout=0, terminate_timeout=0, kill_timeout=0)
        self.assertEqual(runtime.group.signals, [signal.SIGTERM])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_start_signal_handlers_are_installed_and_restored(self):
        installed = []
        restored = []

        def fake_getsignal(signum):
            return ("previous", signum)

        def fake_signal(signum, handler):
            if handler is session._raise_start_interrupted:
                installed.append(signum)
            else:
                restored.append((signum, handler))

        with mock.patch(
            "termux_desk.session.signal.getsignal", side_effect=fake_getsignal
        ), mock.patch("termux_desk.session.signal.signal", side_effect=fake_signal):
            self.start(FakeRuntime(members=(4242,)))

        self.assertEqual(installed, [signal.SIGHUP, signal.SIGTERM])
        self.assertEqual(
            restored,
            [
                (signal.SIGHUP, ("previous", signal.SIGHUP)),
                (signal.SIGTERM, ("previous", signal.SIGTERM)),
            ],
        )

    def test_start_signal_handler_raises_start_interrupted(self):
        with self.assertRaises(StartInterrupted) as raised:
            session._raise_start_interrupted(signal.SIGTERM, None)
        self.assertEqual(raised.exception.signum, signal.SIGTERM)

    def test_lock_contention_stops_start_before_spawn(self):
        runtime = FakeRuntime(members=(4242,))
        with state_lock(self.workspace.paths):
            with self.assertRaises(LockError):
                self.start(runtime, lock_timeout=0)
        self.assertEqual(runtime.spawned, [])
        self.assertFalse(self.workspace.paths.session_file.exists())


class VerificationTest(unittest.TestCase):
    def setUp(self):
        self.state = make_state()

    def test_matching_boot_and_session_id_means_running(self):
        runtime = FakeRuntime(members=(4242,), verified=True)
        result = verify_session(self.state, runtime.runtime)
        self.assertEqual(result.state, VERIFICATION_RUNNING)

    def test_other_boot_means_stale(self):
        runtime = FakeRuntime(members=(4242,), boot_id="another-boot", verified=False)
        result = verify_session(self.state, runtime.runtime)
        self.assertEqual(result.state, VERIFICATION_STALE)
        self.assertIn("earlier boot", result.detail)

    def test_empty_group_means_stale(self):
        runtime = FakeRuntime(members=(), verified=False)
        result = verify_session(self.state, runtime.runtime)
        self.assertEqual(result.state, VERIFICATION_STALE)
        self.assertIn("no live processes", result.detail)

    def test_group_without_session_evidence_is_unverifiable(self):
        runtime = FakeRuntime(members=(4242,), verified=False)
        result = verify_session(self.state, runtime.runtime)
        self.assertEqual(result.state, VERIFICATION_UNVERIFIABLE)
        self.assertIn(SESSION_ID, result.detail)

    def test_scan_error_is_raised(self):
        def broken(pgid):
            raise ProcessScanError("cannot read /proc")

        runtime = FakeRuntime(members=(4242,))
        result = replace(runtime.runtime, group_members=broken)
        with self.assertRaises(ProcessScanError):
            verify_session(self.state, result)


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_missing_state_is_not_running(self):
        report = session_status(self.workspace.paths, runtime=FakeRuntime().runtime)
        self.assertFalse(report.running)
        self.assertIn("Session is not running.", report.lines[0])

    def test_live_verified_group_is_running(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        report = session_status(
            self.workspace.paths, runtime=FakeRuntime(members=(4242,), verified=True).runtime
        )
        self.assertTrue(report.running)
        self.assertTrue(any("pgid: 4242" in line for line in report.lines))

    def test_boot_mismatch_is_stale(self):
        self.workspace.write_state(make_state(boot_id="another-boot"))
        report = session_status(
            self.workspace.paths, runtime=FakeRuntime(members=(4242,), verified=False).runtime
        )
        self.assertFalse(report.running)
        self.assertIn("earlier boot", " ".join(report.lines))

    def test_group_without_processes_is_stale(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        report = session_status(
            self.workspace.paths, runtime=FakeRuntime(members=(), verified=False).runtime
        )
        self.assertFalse(report.running)
        self.assertIn("has no live processes", " ".join(report.lines))

    def test_unverifiable_group_is_reported(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        report = session_status(
            self.workspace.paths, runtime=FakeRuntime(members=(4242,), verified=False).runtime
        )
        self.assertFalse(report.running)
        self.assertIn("cannot verify", " ".join(report.lines))
        self.assertIn("kept", " ".join(report.lines))

    def test_scan_error_is_raised(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))

        def broken(pgid):
            raise ProcessScanError("cannot read /proc")

        runtime = replace(FakeRuntime().runtime, group_members=broken)
        with self.assertRaises(ProcessScanError):
            session_status(self.workspace.paths, runtime=runtime)

    def test_broken_state_file_raises(self):
        self.workspace.paths.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_file.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StateError):
            session_status(self.workspace.paths, runtime=FakeRuntime().runtime)


class StopTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def stop(self, runtime, **kwargs):
        output = io.StringIO()
        stop_session(
            self.workspace.paths,
            runtime=runtime.runtime,
            out=output,
            **kwargs,
        )
        return output.getvalue()

    def test_no_session_is_idempotent_success(self):
        runtime = FakeRuntime()
        output = self.stop(runtime)
        self.assertIn("No desk session is running.", output)
        self.assertEqual(runtime.group.signals, [])

    def test_stale_state_is_removed(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=())
        output = self.stop(runtime)
        self.assertIn("removed the stale state file", output)
        self.assertFalse(self.workspace.paths.session_file.exists())
        self.assertEqual(runtime.group.signals, [])

    def test_state_from_an_earlier_boot_is_stale(self):
        self.workspace.write_state(make_state(boot_id="earlier-boot"))
        runtime = FakeRuntime(members=(4242,), verified=False)
        output = self.stop(runtime)
        self.assertIn("removed the stale state file", output)
        self.assertEqual(runtime.group.signals, [])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_unverifiable_group_is_not_signalled(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), verified=False)
        with self.assertRaises(StopError) as raised:
            self.stop(runtime)
        self.assertIn("cannot verify", str(raised.exception))
        self.assertIn("refuses to signal", str(raised.exception))
        self.assertEqual(runtime.group.signals, [])
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_live_verified_group_is_terminated_and_state_is_removed(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,), verified=True)
        output = self.stop(runtime, terminate_timeout=10, kill_timeout=10)
        self.assertIn("Session stopped.", output)
        self.assertEqual(runtime.group.signals, [signal.SIGTERM])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_group_that_ignores_sigterm_gets_sigkill(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), clear_on=(signal.SIGKILL,), verified=True)
        output = self.stop(runtime, terminate_timeout=0, kill_timeout=0)
        self.assertIn("Session stopped.", output)
        self.assertEqual(runtime.group.signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertFalse(self.workspace.paths.session_file.exists())

    def test_remaining_processes_keep_the_state_and_raise(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), verified=True)
        with self.assertRaises(StopError) as raised:
            self.stop(runtime, terminate_timeout=0, kill_timeout=0)
        message = str(raised.exception)
        self.assertIn("still has live processes: 4242", message)
        self.assertIn("does not search for processes by name", message)
        self.assertEqual(runtime.group.signals, [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_broken_state_file_is_reported_and_kept(self):
        self.workspace.paths.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.workspace.paths.session_file.write_text("not json", encoding="utf-8")
        with self.assertRaises(StateError):
            self.stop(FakeRuntime())
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_scan_error_is_raised_and_state_kept(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))

        def broken(pgid):
            raise ProcessScanError("cannot read /proc")

        runtime = replace(FakeRuntime(members=(4242,)).runtime, group_members=broken)
        with self.assertRaises(ProcessScanError):
            stop_session(self.workspace.paths, runtime=runtime, out=io.StringIO())
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_state_with_desks_own_process_group_is_refused(self):
        own_group = os.getpgrp()
        self.workspace.write_state(make_state(pgid=own_group, pid=own_group))
        runtime = FakeRuntime(members=(own_group,), pgid=own_group, verified=True)
        with self.assertRaises(SessionError) as raised:
            self.stop(runtime)
        self.assertIn("desk itself", str(raised.exception))
        self.assertEqual(runtime.group.signals, [])
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_state_removal_failure_is_reported(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), clear_on=(signal.SIGTERM,), verified=True)
        with mock.patch(
            "termux_desk.session.remove_session_state", side_effect=OSError("read only")
        ):
            with self.assertRaises(StopError) as raised:
                self.stop(runtime)
        self.assertIn("could not be removed", str(raised.exception))
        self.assertTrue(self.workspace.paths.session_file.exists())

    def test_lock_contention_is_reported(self):
        self.workspace.write_state(make_state(pgid=4242, pid=4242))
        runtime = FakeRuntime(members=(4242,), verified=True)
        with state_lock(self.workspace.paths):
            with self.assertRaises(LockError):
                self.stop(runtime, lock_timeout=0)
        self.assertEqual(runtime.group.signals, [])


class ProcessTableTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.proc_root = Path(self.temporary.name) / "proc"
        self.proc_root.mkdir()

    def add_process(self, pid, pgid, arguments, *, state="S", starttime=LEADER_STARTTIME, comm="bash", environment=None):
        directory = self.proc_root / str(pid)
        directory.mkdir()
        (directory / "stat").write_text(
            stat_line(pid, pgid, state=state, starttime=starttime, comm=comm),
            encoding="utf-8",
        )
        (directory / "cmdline").write_bytes(b"\0".join(arguments) + b"\0")
        if environment is not None:
            encoded = b"\0".join(
                f"{name}={value}".encode("utf-8") for name, value in environment.items()
            )
            (directory / "environ").write_bytes(encoded + b"\0")

    def test_process_group_members_are_found_by_group_number(self):
        self.add_process(100, 100, (b"/usr/bin/bash", b"session.sh"))
        self.add_process(101, 100, (b"/usr/bin/termux-x11", b":0"))
        self.add_process(200, 200, (b"/usr/bin/unrelated",))
        self.assertEqual(process_group_members(100, self.proc_root), (100, 101))

    def test_zombie_members_do_not_count(self):
        self.add_process(100, 100, (b"/usr/bin/bash",))
        self.add_process(101, 100, (b"/usr/bin/termux-x11",), state="Z")
        self.add_process(102, 100, (b"/usr/bin/leftover",), state="X")
        self.assertEqual(process_group_members(100, self.proc_root), (100,))

    def test_parse_process_group_survives_parentheses_in_the_name(self):
        self.add_process(300, 300, (b"/usr/bin/bash",), comm="weird) name")
        self.assertEqual(process_group_members(300, self.proc_root), (300,))

    def test_start_time_is_read_from_the_stat_file(self):
        self.add_process(100, 100, (b"/usr/bin/bash",), starttime=987654)
        self.assertEqual(process_start_time(100, self.proc_root), 987654)

    def test_missing_process_group_is_empty(self):
        self.add_process(100, 100, (b"/usr/bin/bash",))
        self.assertEqual(process_group_members(555, self.proc_root), ())

    def test_xfce_process_detection(self):
        self.add_process(100, 100, (b"/usr/bin/bash", b"session.sh"))
        self.assertFalse(session.has_xfce_process(100, self.proc_root))
        self.add_process(101, 100, (b"/usr/bin/xfce4-session",))
        self.assertTrue(session.has_xfce_process(100, self.proc_root))

    def test_termux_x11_process_detection(self):
        self.add_process(100, 100, (b"/usr/bin/bash", b"session.sh"))
        self.assertFalse(session.has_termux_x11_process(100, self.proc_root))
        self.add_process(101, 100, (b"/usr/bin/termux-x11", b":0"))
        self.assertTrue(session.has_termux_x11_process(100, self.proc_root))

    def test_session_in_group_finds_the_session_id_in_arguments(self):
        self.add_process(100, 100, (b"/usr/bin/bash", b"/state/session.sh", SESSION_ID.encode()))
        self.assertTrue(session_in_group(100, SESSION_ID, 100, LEADER_STARTTIME, self.proc_root))

    def test_session_in_group_finds_the_session_id_in_the_environment(self):
        self.add_process(
            100,
            100,
            (b"/usr/bin/xfce4-session",),
            environment={"DESK_SESSION_ID": SESSION_ID},
        )
        self.assertTrue(session_in_group(100, SESSION_ID, 999, 1, self.proc_root))

    def test_session_in_group_uses_the_leader_start_time(self):
        self.add_process(100, 100, (b"/usr/bin/bash",), starttime=424242)
        self.assertFalse(session_in_group(100, SESSION_ID, 100, 111111, self.proc_root))
        self.assertTrue(session_in_group(100, SESSION_ID, 100, 424242, self.proc_root))

    def test_session_in_group_without_evidence_is_false(self):
        self.add_process(100, 100, (b"/usr/bin/bash",))
        self.assertFalse(session_in_group(100, SESSION_ID, 100, LEADER_STARTTIME + 1, self.proc_root))

    def test_process_group_of_a_missing_process_is_none(self):
        self.assertIsNone(session.process_group_of(999, self.proc_root))

    def test_read_process_arguments_returns_empty_for_missing_processes(self):
        self.assertEqual(session.read_process_arguments(999, self.proc_root), ())

    def test_unreadable_proc_directory_raises_a_scan_error(self):
        with mock.patch(
            "termux_desk.session.os.listdir", side_effect=PermissionError("denied")
        ):
            with self.assertRaises(ProcessScanError):
                process_group_members(100, self.proc_root)

    def test_unreadable_stat_file_raises_a_scan_error(self):
        directory = self.proc_root / "123"
        (directory / "stat").mkdir(parents=True)
        with self.assertRaises(ProcessScanError):
            process_group_members(123, self.proc_root)


class DisplayHelperTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))

    def test_socket_without_a_listener_is_not_ready(self):
        self.workspace.create_socket(self.workspace.paths.x11_socket)
        self.assertTrue(session.display_socket_ready(self.workspace.paths.x11_socket))
        self.assertFalse(session.unix_socket_accepts_connection(self.workspace.paths.x11_socket))
        self.assertFalse(x11_socket_ready(self.workspace.paths.x11_socket))

    def test_listening_socket_is_ready(self):
        path = self.workspace.paths.x11_socket
        path.parent.mkdir(parents=True, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(path))
            listener.listen(1)
            self.assertTrue(x11_socket_ready(path))
        finally:
            listener.close()

    def test_regular_file_is_not_a_display_socket(self):
        path = self.workspace.paths.x11_socket
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a socket", encoding="utf-8")
        self.assertFalse(session.display_socket_ready(path))
        self.assertFalse(x11_socket_ready(path))

    def test_lock_file_pid_is_read(self):
        path = self.workspace.paths.x11_lock
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("  4242 \n", encoding="utf-8")
        self.assertEqual(session.read_lock_pid(path), 4242)

    def test_lock_file_without_a_number_has_no_pid(self):
        path = self.workspace.paths.x11_lock
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage", encoding="utf-8")
        self.assertIsNone(session.read_lock_pid(path))

    def test_clear_stale_display_files_removes_dead_files(self):
        socket_path = self.workspace.paths.x11_socket
        lock_path = self.workspace.paths.x11_lock
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("stale", encoding="utf-8")
        lock_path.write_text("999999\n", encoding="utf-8")
        output = io.StringIO()
        runtime = FakeRuntime(process_alive=False, socket_in_use=False)
        clear_stale_display_files(self.workspace.paths, runtime.runtime, out=output)
        self.assertFalse(socket_path.exists())
        self.assertFalse(lock_path.exists())
        self.assertIn("Removed the stale X11 socket", output.getvalue())

    def test_clear_stale_display_files_keeps_a_live_socket(self):
        self.workspace.create_socket(self.workspace.paths.x11_socket, listen=True)
        runtime = FakeRuntime(process_alive=False, socket_in_use=True)
        with self.assertRaises(StartError):
            clear_stale_display_files(
                self.workspace.paths, runtime.runtime, out=io.StringIO()
            )
        self.assertTrue(self.workspace.paths.x11_socket.exists())

    def test_clear_stale_display_files_keeps_a_live_lock(self):
        lock_path = self.workspace.paths.x11_lock
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("123\n", encoding="utf-8")
        runtime = FakeRuntime(process_alive=True, socket_in_use=False)
        with self.assertRaises(StartError):
            clear_stale_display_files(
                self.workspace.paths, runtime.runtime, out=io.StringIO()
            )
        self.assertTrue(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
