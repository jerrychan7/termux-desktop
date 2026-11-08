"""Tests for the executable entry point and the module entry point."""

from __future__ import annotations

import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DESK_ENTRY = REPOSITORY_ROOT / "desk"


class DeskEntryTest(unittest.TestCase):
    def test_desk_file_is_executable_with_a_termux_shebang(self):
        self.assertTrue(DESK_ENTRY.is_file())
        self.assertTrue(DESK_ENTRY.stat().st_mode & stat.S_IXUSR)
        first_line = DESK_ENTRY.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "#!/data/data/com.termux/files/usr/bin/python3")

    def test_desk_help_runs_without_a_termux_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, str(DESK_ENTRY), "--help"],
                cwd=str(REPOSITORY_ROOT),
                env={"HOME": temporary, "PREFIX": str(Path(temporary) / "usr")},
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("setup", completed.stdout)
        self.assertIn("doctor", completed.stdout)

    def test_desk_status_reports_a_missing_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [sys.executable, str(DESK_ENTRY), "status"],
                cwd=str(REPOSITORY_ROOT),
                env={"HOME": temporary, "PREFIX": str(Path(temporary) / "usr")},
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("Session is not running.", completed.stdout)

    def test_module_invocation_prints_logs_without_a_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {
                "HOME": temporary,
                "PREFIX": str(Path(temporary) / "usr"),
                "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
            }
            completed = subprocess.run(
                [sys.executable, "-m", "termux_desk", "logs"],
                cwd=str(REPOSITORY_ROOT),
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("No log file at", completed.stdout)


if __name__ == "__main__":
    unittest.main()
