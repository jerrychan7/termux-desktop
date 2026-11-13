"""Tests for the browser VNC proxy helpers."""

from __future__ import annotations

import gzip
import hashlib
import io
import socket
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from support import RecordingRunner, make_result

from termux_desk import webproxy
from termux_desk.config import load_paths


def novnc_tarball(top: str = "noVNC-1.7.0") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in (
            (f"{top}/vnc.html", b"<html></html>"),
            (f"{top}/app/ui.js", b"// ui"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


def wheel_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("websockify/__init__.py", "")
        archive.writestr("websockify/websocketproxy.py", "")
    return buffer.getvalue()


NOVNC_BYTES = novnc_tarball()
NOVNC_DIGEST = hashlib.sha256(NOVNC_BYTES).hexdigest()
WHEEL_BYTES = wheel_bytes()
WHEEL_NAME = "websockify-0.13.0-py3-none-any.whl"


def fake_fetcher(url, destination):
    if "websockify_rs" in url:
        destination.write_bytes(b"rs-binary")
    else:
        destination.write_bytes(NOVNC_BYTES)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = load_paths(
            {"HOME": str(self.root / "home"), "PREFIX": str(self.root / "usr")}
        )
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        self.out = io.StringIO()
        patcher = mock.patch.object(webproxy, "NOVNC_SHA256", NOVNC_DIGEST)
        patcher.start()
        self.addCleanup(patcher.stop)


class ArchiveTest(Fixture):
    def test_machine_arch_maps_termux_names(self):
        self.assertEqual(webproxy.machine_arch("aarch64"), "aarch64")
        self.assertEqual(webproxy.machine_arch("armv7l"), "armv7")
        self.assertEqual(webproxy.machine_arch("armv8l"), "armv7")
        self.assertEqual(webproxy.machine_arch("x86_64"), "x86_64")
        with self.assertRaises(webproxy.WebProxyError):
            webproxy.machine_arch("i686")

    def test_state_round_trip(self):
        state = webproxy.WebProxyState(
            display=":1",
            backend="rs",
            host="127.0.0.1",
            port=6080,
            web_pid=4242,
            static_pid=4243,
            started_at=1.5,
        )
        loaded = webproxy.WebProxyState.from_json(state.to_json())
        self.assertEqual(loaded, state)
        self.assertTrue(
            webproxy.web_url(state).endswith("/vnc.html?host=127.0.0.1&port=6081")
        )

    def test_python_url_uses_one_port(self):
        state = webproxy.WebProxyState(
            display=":1", backend="py", host="127.0.0.1", port=6080, web_pid=1
        )
        self.assertEqual(webproxy.web_url(state), "http://127.0.0.1:6080/vnc.html")

    def test_state_with_wrong_version_is_rejected(self):
        with self.assertRaises(webproxy.WebProxyError):
            webproxy.WebProxyState.from_json('{"version": 99}')


class InstallTest(Fixture):
    def test_novnc_install_verifies_the_tarball_and_checks_vnc_html(self):
        target = webproxy.install_novnc(self.paths, fetcher=fake_fetcher, out=self.out)
        self.assertTrue((target / "vnc.html").is_file())
        self.assertTrue((target / "app" / "ui.js").is_file())

    def test_novnc_checksum_mismatch_is_reported(self):
        with mock.patch.object(webproxy, "NOVNC_SHA256", "0" * 64):
            with self.assertRaises(webproxy.WebProxyError) as caught:
                webproxy.install_novnc(self.paths, fetcher=fake_fetcher, out=self.out)
        self.assertIn("checksum", str(caught.exception))

    def test_novnc_install_is_idempotent_without_force(self):
        webproxy.install_novnc(self.paths, fetcher=fake_fetcher, out=self.out)
        webproxy.install_novnc(self.paths, fetcher=fake_fetcher, out=self.out)
        self.assertIn("already in place", self.out.getvalue())

    def test_rs_install_writes_an_executable_with_the_recorded_hash(self):
        digest = hashlib.sha256(b"rs-binary").hexdigest()
        with mock.patch.dict(
            webproxy.RS_ASSETS, {"aarch64": ("websockify_rs-aarch64", digest)}
        ):
            target = webproxy.install_rs_proxy(
                self.paths, fetcher=fake_fetcher, machine="aarch64", out=self.out
            )
        self.assertTrue(target.is_file())
        self.assertTrue(target.stat().st_mode & 0o111)

    def test_rs_install_rejects_a_different_hash(self):
        with mock.patch.object(
            webproxy, "RS_ASSETS", {"aarch64": ("websockify_rs-aarch64", "0" * 64)}
        ):
            with self.assertRaises(webproxy.WebProxyError):
                webproxy.install_rs_proxy(
                    self.paths, fetcher=fake_fetcher, machine="aarch64", out=self.out
                )

    def test_python_proxy_downloads_a_wheel_without_installing_it(self):
        digest = hashlib.sha256(WHEEL_BYTES).hexdigest()

        def handler(command, **_kwargs):
            destination = Path(command[command.index("--dest") + 1])
            (destination / WHEEL_NAME).write_bytes(WHEEL_BYTES)
            return make_result(command)

        runner = RecordingRunner(handler)
        with mock.patch.object(webproxy, "WEBSOCKIFY_SHA256", digest):
            target = webproxy.install_python_proxy(
                self.paths, runner=runner, out=self.out
            )
        self.assertTrue((target / "websockify" / "__init__.py").is_file())
        self.assertEqual(runner.calls[0][1:4], ("-m", "pip", "download"))


class SetupDispatchTest(Fixture):
    def test_both_proxies_and_novnc_are_installed(self):
        rs_digest = hashlib.sha256(b"rs-binary").hexdigest()
        wheel_digest = hashlib.sha256(WHEEL_BYTES).hexdigest()

        def handler(command, **_kwargs):
            destination = Path(command[command.index("--dest") + 1])
            (destination / WHEEL_NAME).write_bytes(WHEEL_BYTES)
            return make_result(command)

        runner = RecordingRunner(handler)
        with mock.patch.dict(
            webproxy.RS_ASSETS, {"aarch64": ("websockify_rs-aarch64", rs_digest)}
        ), mock.patch.object(webproxy, "WEBSOCKIFY_SHA256", wheel_digest):
            webproxy.run_web_setup(
                self.paths,
                proxy="both",
                fetcher=fake_fetcher,
                runner=runner,
                machine="aarch64",
                out=self.out,
            )
        self.assertTrue((webproxy.novnc_dir(self.paths) / "vnc.html").is_file())
        self.assertTrue(webproxy.rs_proxy_path(self.paths).is_file())
        self.assertTrue(
            (webproxy.python_proxy_dir(self.paths) / "websockify" / "__init__.py").is_file()
        )

    def test_unknown_proxy_is_rejected(self):
        with self.assertRaises(webproxy.WebProxyError):
            webproxy.run_web_setup(self.paths, proxy="go", out=self.out)


class StartStopTest(Fixture):
    def prepare_novnc(self):
        target = webproxy.novnc_dir(self.paths)
        target.mkdir(parents=True, exist_ok=True)
        (target / "vnc.html").write_text("<html></html>")

    def prepare_rs(self):
        binary = webproxy.rs_proxy_path(self.paths)
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("")

    def test_py_command_serves_the_page_and_the_proxy_in_one_process(self):
        self.prepare_novnc()
        directory = webproxy.python_proxy_dir(self.paths)
        (directory / "websockify").mkdir(parents=True, exist_ok=True)
        (directory / "websockify" / "__init__.py").write_text("")
        command, environment = webproxy._proxy_command(
            self.paths, backend="py", display=":1", host="127.0.0.1", port=6080
        )
        self.assertIn("-m", command)
        self.assertIn("websockify", command)
        self.assertIn("--web", command)
        self.assertIn("127.0.0.1:6080", command)
        self.assertIn("127.0.0.1:5901", command)
        self.assertEqual(environment["PYTHONPATH"], str(directory))

    def test_rs_command_uses_the_next_port(self):
        self.prepare_rs()
        binary = webproxy.rs_proxy_path(self.paths)
        command, _environment = webproxy._proxy_command(
            self.paths, backend="rs", display=":1", host="127.0.0.1", port=6080
        )
        self.assertEqual(command, [str(binary), "127.0.0.1:6081", "127.0.0.1:5901"])

    def test_missing_python_proxy_is_reported(self):
        self.prepare_novnc()
        with self.assertRaises(webproxy.WebProxyError):
            webproxy._proxy_command(
                self.paths, backend="py", display=":1", host="127.0.0.1", port=6080
            )

    def test_start_requires_the_page(self):
        with self.assertRaises(webproxy.WebProxyError):
            webproxy.run_web_start(self.paths, out=self.out)

    def test_start_records_the_state_and_prints_the_url(self):
        self.prepare_novnc()
        self.prepare_rs()

        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.pid = 4242

            def terminate(self):
                pass

        with mock.patch.object(webproxy.vnc, "run_vnc_start") as start, \
                mock.patch.object(
                    webproxy, "session_status", return_value=mock.Mock(running=True)
                ), \
                mock.patch.object(webproxy.subprocess, "Popen", FakeProcess), \
                mock.patch.object(webproxy, "_wait_for_port", return_value=True):
            state = webproxy.run_web_start(self.paths, proxy="rs", out=self.out)
        start.assert_not_called()
        self.assertEqual(state.web_pid, 4242)
        self.assertIn("http://127.0.0.1:6080/vnc.html", self.out.getvalue())
        loaded = webproxy.read_web_state(self.paths, ":1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.web_pid, 4242)

    def test_start_starts_the_vnc_session_when_it_is_not_running(self):
        self.prepare_novnc()
        self.prepare_rs()

        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.pid = 4343

            def terminate(self):
                pass

        with mock.patch.object(
            webproxy, "session_status", return_value=mock.Mock(running=False)
        ), mock.patch.object(webproxy.vnc, "run_vnc_start") as start, \
                mock.patch.object(webproxy.subprocess, "Popen", FakeProcess), \
                mock.patch.object(webproxy, "_wait_for_port", return_value=True):
            webproxy.run_web_start(self.paths, proxy="rs", out=self.out)
        start.assert_called_once()

    def test_stop_signals_both_processes_and_removes_the_state(self):
        self.prepare_novnc()
        state = webproxy.WebProxyState(
            display=":1",
            backend="rs",
            host="127.0.0.1",
            port=6080,
            web_pid=4242,
            static_pid=4243,
        )
        webproxy.write_web_state(self.paths, state)
        alive = {4242: True, 4243: True}

        def fake_alive(pid):
            return alive.get(pid, False)

        def fake_kill(pid, _signal):
            alive[pid] = False

        with mock.patch.object(webproxy, "pid_alive", side_effect=fake_alive), \
                mock.patch.object(webproxy.os, "kill", side_effect=fake_kill):
            stopped = webproxy.run_web_stop(self.paths, out=self.out)
        self.assertTrue(stopped)
        self.assertIsNone(webproxy.read_web_state(self.paths, ":1"))
        self.assertIn("the VNC session was not changed", self.out.getvalue())

    def test_status_reports_running_and_absent(self):
        self.assertFalse(webproxy.run_web_status(self.paths, out=self.out))
        self.assertIn("not running", self.out.getvalue())
        state = webproxy.WebProxyState(
            display=":1", backend="py", host="127.0.0.1", port=6080, web_pid=1
        )
        webproxy.write_web_state(self.paths, state)
        with mock.patch.object(webproxy, "pid_alive", return_value=True):
            self.assertTrue(webproxy.run_web_status(self.paths, out=self.out))
        self.assertIn("http://127.0.0.1:6080/vnc.html", self.out.getvalue())

    def test_port_probe_detects_a_listener(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertFalse(webproxy.port_is_free("127.0.0.1", port))
        self.assertTrue(webproxy.port_is_free("127.0.0.1", port))
