"""Integration tests with real short-lived process groups.

These tests do not start XFCE or termux-x11. They install small stand-in
programs in a temporary Termux prefix and let the generated session.sh run
them, so the process group handling, the state verification, and the signal
cleanup are exercised on real processes.
"""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from support import Workspace, make_state
from termux_desk import session
from termux_desk.config import load_paths
from termux_desk.session import (
    StartError,
    StopError,
    process_group_members,
    process_group_of,
    process_start_time,
    read_boot_id,
    render_session_script,
    session_status,
    start_session,
    stop_session,
    x11_socket_ready,
)
from termux_desk.state import LockError, state_lock

FAKE_TERMUX_X11 = """\
#!/usr/bin/env python3
import os
import socket
import sys
import time

prefix = os.environ["PREFIX"]
socket_path = os.path.join(prefix, "tmp", ".X11-unix", "X0")
os.makedirs(os.path.dirname(socket_path), exist_ok=True)
try:
    os.unlink(socket_path)
except FileNotFoundError:
    pass
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(socket_path)
server.listen(64)
time.sleep(120)
"""

FAKE_AM = """\
#!/usr/bin/env python3
import sys

sys.exit(0)
"""

FAKE_DBUS_LAUNCH = """\
#!/usr/bin/env python3
import os
import sys

arguments = [value for value in sys.argv[1:] if not value.startswith("--")]
if not arguments:
    sys.exit(2)
os.execvp(arguments[0], arguments)
"""

FAKE_STARTXFCE4 = """\
#!/usr/bin/env python3
import os

target = os.path.join(os.environ["PREFIX"], "bin", "xfce4-session")
os.execv(target, [target])
"""

FAKE_XFCE4_SESSION = """\
#!/usr/bin/env python3
import time

time.sleep(120)
"""


def kill_group_quietly(pgid: int) -> None:
    """Kill a test process group and ignore a group that is already gone."""
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        return


class FakeTermux:
    """A temporary Termux prefix with stand-in programs for one test."""

    def __init__(self, root: Path, *, home_name: str = "home"):
        self.workspace = Workspace(root)
        self.home = root / home_name
        self.home.mkdir(parents=True, exist_ok=True)
        self.prefix = self.workspace.prefix
        self.environ = {
            "HOME": str(self.home),
            "PREFIX": str(self.prefix),
            "PATH": os.pathsep.join(
                [str(self.prefix / "bin"), os.environ.get("PATH", "/usr/bin:/bin")]
            ),
        }
        self.paths = load_paths(self.environ)
        self.install_fakes()

    def install_fakes(self) -> None:
        bash = shutil.which("bash") or "/bin/bash"
        os.symlink(bash, self.prefix / "bin" / "bash")
        self.write_program("termux-x11", FAKE_TERMUX_X11)
        self.write_program("am", FAKE_AM)
        self.write_program("dbus-launch", FAKE_DBUS_LAUNCH)
        self.write_program("startxfce4", FAKE_STARTXFCE4)
        self.write_program("xfce4-session", FAKE_XFCE4_SESSION)

    def write_program(self, name: str, text: str) -> Path:
        path = self.prefix / "bin" / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        path.chmod(0o755)
        return path


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def start(self, fake: FakeTermux, **kwargs):
        output = io.StringIO()
        state = start_session(
            fake.paths,
            environ=fake.environ,
            out=output,
            ready_timeout=30,
            terminate_timeout=10,
            kill_timeout=5,
            **kwargs,
        )
        self.addCleanup(kill_group_quietly, state.pgid)
        return state, output.getvalue()

    def test_real_start_and_stop_clean_the_process_group(self):
        fake = FakeTermux(self.root)
        state, output = self.start(fake)

        self.assertEqual(state.status, "running")
        self.assertTrue(state.session_id)
        self.assertIn("Session is running.", output)
        self.assertIn(state.session_id, fake.paths.session_log.read_text(encoding="utf-8"))
        self.assertTrue(x11_socket_ready(fake.paths.x11_socket))
        self.assertTrue(process_group_members(state.pgid))

        report = session_status(fake.paths)
        self.assertTrue(report.running)

        stop_session(fake.paths, out=io.StringIO(), terminate_timeout=10, kill_timeout=5)

        self.assertEqual(process_group_members(state.pgid), ())
        self.assertFalse(fake.paths.session_file.exists())

    def test_stale_socket_file_is_removed_before_the_real_start(self):
        fake = FakeTermux(self.root)
        stale = fake.paths.x11_socket
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("stale socket file", encoding="utf-8")

        state, output = self.start(fake)

        self.assertIn("Removed the stale X11 socket", output)
        self.assertTrue(x11_socket_ready(fake.paths.x11_socket))
        stop_session(fake.paths, out=io.StringIO(), terminate_timeout=10, kill_timeout=5)
        self.assertEqual(process_group_members(state.pgid), ())

    def test_keyboard_interrupt_after_spawn_kills_the_real_group(self):
        fake = FakeTermux(self.root)
        recorded = []
        real_spawn = session.spawn_session_script

        def recording_spawn(*args, **kwargs):
            started = real_spawn(*args, **kwargs)
            recorded.append(started)
            return started

        def interrupt():
            raise KeyboardInterrupt()

        runtime = session.SessionRuntime(spawn=recording_spawn, now=interrupt)
        with self.assertRaises(KeyboardInterrupt):
            start_session(
                fake.paths,
                runtime=runtime,
                environ=fake.environ,
                out=io.StringIO(),
                ready_timeout=0,
                terminate_timeout=5,
                kill_timeout=5,
            )

        self.assertEqual(len(recorded), 1)
        self.addCleanup(kill_group_quietly, recorded[0].pgid)
        self.assertEqual(process_group_members(recorded[0].pgid), ())
        self.assertFalse(fake.paths.session_file.exists())

    def test_second_real_start_is_refused_by_the_lock(self):
        fake = FakeTermux(self.root)
        with state_lock(fake.paths):
            with self.assertRaises(LockError):
                start_session(
                    fake.paths,
                    environ=fake.environ,
                    out=io.StringIO(),
                    lock_timeout=0,
                )
        self.assertFalse(fake.paths.session_file.exists())
        self.assertFalse(fake.paths.session_script.exists())

    def test_unverified_process_group_is_not_signalled(self):
        fake = FakeTermux(self.root)
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            start_new_session=True,
        )
        self.addCleanup(self._reap_child, child)

        start_time = process_start_time(child.pid)
        self.assertIsNotNone(start_time)
        fake.workspace.write_state(
            make_state(
                pid=child.pid,
                pgid=child.pid,
                boot_id=read_boot_id(),
                session_id="deadbeef" * 4,
                leader_starttime=start_time + 1,
                status="running",
            )
        )

        report = session_status(fake.paths)
        self.assertFalse(report.running)
        self.assertIn("cannot verify", " ".join(report.lines))

        with self.assertRaises(StopError):
            stop_session(fake.paths, out=io.StringIO(), terminate_timeout=1, kill_timeout=1)

        self.assertIsNone(child.poll())
        self.assertTrue(fake.paths.session_file.exists())

    def test_zombie_process_is_not_a_live_group_member(self):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        try:
            group = os.getpgrp()
            members = ()
            for _ in range(100):
                members = process_group_members(group)
                if pid not in members:
                    break
                time.sleep(0.02)
            self.assertNotIn(pid, members)
            self.assertEqual(process_group_of(pid), group)
        finally:
            os.waitpid(pid, 0)

    def test_home_path_with_spaces_and_placeholder_runs(self):
        fake = FakeTermux(self.root, home_name="home with spaces @DISPLAY@")
        script_text = render_session_script(fake.paths)
        self.assertIn(f"export HOME='{fake.paths.home}'", script_text)

        state, _ = self.start(fake)
        stop_session(fake.paths, out=io.StringIO(), terminate_timeout=10, kill_timeout=5)

        self.assertEqual(process_group_members(state.pgid), ())

    def test_prefix_with_spaces_and_placeholders_renders_valid_bash(self):
        home = self.root / "home"
        prefix = self.root / "usr with spaces @PREFIX@"
        paths = load_paths({"HOME": str(home), "PREFIX": str(prefix)})
        script_text = render_session_script(paths)
        self.assertIn(f"export PREFIX='{prefix}'", script_text)
        self.assertIn("@PREFIX@", script_text)
        self.assertNotIn("@BASH@", script_text)
        self.assertNotIn("@HOME@", script_text)
        self.assertNotIn("@TERMUX_X11_ACTIVITY@", script_text)
        script = paths.session_script
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(script_text, encoding="utf-8")
        completed = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_missing_am_is_refused_before_any_process_starts(self):
        fake = FakeTermux(self.root)
        fake.prefix.joinpath("bin", "am").unlink()
        with self.assertRaises(StartError) as raised:
            start_session(
                fake.paths,
                environ=fake.environ,
                out=io.StringIO(),
                ready_timeout=0,
            )
        self.assertIn("desk setup", str(raised.exception))
        self.assertFalse(fake.paths.session_script.exists())

    def _reap_child(self, child):
        kill_group_quietly(child.pid)
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
