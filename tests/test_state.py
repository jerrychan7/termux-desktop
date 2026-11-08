"""Tests for the JSON state file, the state lock, and the log helpers."""

from __future__ import annotations

import fcntl
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import BOOT_ID, FIXED_TIME, LEADER_STARTTIME, SESSION_ID, make_state
from termux_desk.config import DeskError, load_paths
from termux_desk.state import (
    LOCK_FILE_NAME,
    LockError,
    StateError,
    append_log,
    read_log_tail,
    read_session_state,
    remove_session_state,
    state_lock,
    write_session_state,
)


class StateFileTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "state" / "session.json"

    def test_write_then_read_returns_the_same_facts(self):
        state = make_state()
        write_session_state(self.state_path, state)
        self.assertEqual(read_session_state(self.state_path), state)

    def test_write_creates_parent_directories(self):
        write_session_state(self.state_path, make_state())
        self.assertTrue(self.state_path.parent.is_dir())

    def test_written_file_is_json_with_version_two_and_session_facts(self):
        write_session_state(self.state_path, make_state())
        record = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(record["version"], 2)
        self.assertEqual(record["pgid"], 4242)
        self.assertEqual(record["display"], ":0")
        self.assertEqual(record["session_id"], SESSION_ID)
        self.assertEqual(record["boot_id"], BOOT_ID)
        self.assertEqual(record["leader_starttime"], LEADER_STARTTIME)

    def test_write_leaves_no_temporary_files(self):
        write_session_state(self.state_path, make_state())
        self.assertEqual(list(self.state_path.parent.glob("session.json.*.tmp")), [])

    def test_failed_replace_removes_the_temporary_file(self):
        write_session_state(self.state_path, make_state())
        previous = self.state_path.read_text(encoding="utf-8")
        with mock.patch("termux_desk.state.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                write_session_state(self.state_path, make_state(pgid=9999, pid=9999))
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), previous)
        self.assertEqual(list(self.state_path.parent.glob("session.json.*.tmp")), [])

    def test_missing_file_returns_none(self):
        self.assertIsNone(read_session_state(self.state_path))

    def test_invalid_json_raises_state_error(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("this is not json", encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("not valid JSON", str(raised.exception))
        self.assertIsInstance(raised.exception, DeskError)

    def test_json_array_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("JSON object", str(raised.exception))

    def test_missing_version_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        del record["version"]
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("version", str(raised.exception))

    def test_old_version_is_rejected_with_guidance(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        record["version"] = 1
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("version 1", str(raised.exception))
        self.assertIn("Remove the state file", str(raised.exception))

    def test_missing_session_id_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        del record["session_id"]
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("session_id", str(raised.exception))

    def test_missing_boot_id_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        del record["boot_id"]
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("boot_id", str(raised.exception))

    def test_missing_leader_starttime_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        del record["leader_starttime"]
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("leader_starttime", str(raised.exception))

    def test_missing_pgid_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        del record["pgid"]
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("pgid", str(raised.exception))

    def test_string_pid_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        record["pid"] = "4242"
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("pid", str(raised.exception))

    def test_process_group_one_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        record["pgid"] = 1
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError) as raised:
            read_session_state(self.state_path)
        self.assertIn("pgid", str(raised.exception))

    def test_process_id_one_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        record["pid"] = 1
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError):
            read_session_state(self.state_path)

    def test_zero_process_group_is_rejected(self):
        self.state_path.parent.mkdir(parents=True)
        record = make_state().to_record()
        record["pgid"] = 0
        self.state_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaises(StateError):
            read_session_state(self.state_path)

    def test_remove_state_is_idempotent(self):
        write_session_state(self.state_path, make_state())
        self.assertTrue(remove_session_state(self.state_path))
        self.assertFalse(remove_session_state(self.state_path))
        self.assertIsNone(read_session_state(self.state_path))

    def test_remove_state_requires_the_same_session_id(self):
        write_session_state(self.state_path, make_state())
        self.assertFalse(remove_session_state(self.state_path, session_id="another-session"))
        self.assertIsNotNone(read_session_state(self.state_path))
        self.assertTrue(remove_session_state(self.state_path, session_id=SESSION_ID))
        self.assertIsNone(read_session_state(self.state_path))

    def test_remove_state_with_broken_file_is_not_removed(self):
        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_text("{broken", encoding="utf-8")
        self.assertFalse(remove_session_state(self.state_path, session_id=SESSION_ID))
        self.assertTrue(self.state_path.exists())

    def test_overwrite_replaces_previous_state(self):
        write_session_state(self.state_path, make_state(pgid=1000, pid=1000))
        write_session_state(self.state_path, make_state(pgid=2000, pid=2000))
        self.assertEqual(read_session_state(self.state_path).pgid, 2000)


class StateLockTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = load_paths({"HOME": self.temporary.name, "PREFIX": "/tmp/desk-usr"})
        self.lock_path = self.paths.state_dir / LOCK_FILE_NAME

    def test_lock_creates_the_state_directory_and_the_lock_file(self):
        with state_lock(self.paths):
            self.assertTrue(self.lock_path.is_file())

    def test_two_locks_are_exclusive(self):
        with state_lock(self.paths):
            with self.assertRaises(LockError):
                with state_lock(self.paths, timeout=0):
                    pass

    def test_contention_is_reported_with_a_clear_message(self):
        with state_lock(self.paths):
            with self.assertRaises(LockError) as raised:
                with state_lock(self.paths, timeout=0.05, poll_interval=0.01):
                    pass
        self.assertIn("another desk command", str(raised.exception))

    def test_lock_is_released_after_an_exception(self):
        with self.assertRaises(RuntimeError):
            with state_lock(self.paths):
                raise RuntimeError("boom")
        with state_lock(self.paths, timeout=0):
            pass

    def test_external_lock_holder_blocks_desk(self):
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with self.assertRaises(LockError):
                    with state_lock(self.paths, timeout=0):
                        pass
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class LogTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.log_path = Path(self.temporary.name) / "nested" / "desk.log"

    def test_append_log_creates_the_file_and_writes_the_message(self):
        append_log(self.log_path, "start: started", timestamp=FIXED_TIME)
        text = self.log_path.read_text(encoding="utf-8")
        self.assertIn(FIXED_TIME.isoformat(timespec="seconds"), text)
        self.assertIn("start: started", text)

    def test_append_log_appends_lines_in_order(self):
        append_log(self.log_path, "first")
        append_log(self.log_path, "second")
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith("first"))
        self.assertTrue(lines[1].endswith("second"))

    def test_append_log_ignores_unwritable_paths(self):
        unwritable = Path(self.temporary.name) / "file"
        unwritable.write_text("x", encoding="utf-8")
        append_log(unwritable / "desk.log", "message")

    def test_read_log_tail_default_is_the_last_lines(self):
        self.log_path.parent.mkdir(parents=True)
        self.log_path.write_text("".join(f"line {index}\n" for index in range(1, 6)), encoding="utf-8")
        self.assertEqual(read_log_tail(self.log_path, 2), ["line 4\n", "line 5\n"])
        self.assertEqual(read_log_tail(self.log_path, 100), read_log_tail(self.log_path, 5))

    def test_read_log_tail_missing_file_returns_none(self):
        self.assertIsNone(read_log_tail(self.log_path, 10))

    def test_read_log_tail_empty_file_returns_empty_list(self):
        self.log_path.parent.mkdir(parents=True)
        self.log_path.write_text("", encoding="utf-8")
        self.assertEqual(read_log_tail(self.log_path, 10), [])

    def test_read_log_tail_zero_lines(self):
        self.log_path.parent.mkdir(parents=True)
        self.log_path.write_text("line\n", encoding="utf-8")
        self.assertEqual(read_log_tail(self.log_path, 0), [])

    def test_read_log_tail_negative_count_is_rejected(self):
        with self.assertRaises(ValueError):
            read_log_tail(self.log_path, -1)


class TimestampTest(unittest.TestCase):
    def test_fixed_time_is_timezone_aware(self):
        self.assertIsNotNone(FIXED_TIME.tzinfo)
        self.assertEqual(FIXED_TIME.utcoffset(), timedelta(hours=8))
        self.assertIsInstance(FIXED_TIME, datetime)

    def test_appended_timestamp_uses_local_timezone(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "desk.log"
            append_log(log_path, "message")
            first = log_path.read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("message", first)
            self.assertNotEqual(first, "")


if __name__ == "__main__":
    unittest.main()
