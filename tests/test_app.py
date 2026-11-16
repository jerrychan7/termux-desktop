"""Tests for launching a container application on the desk display."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace, make_state

from termux_desk.app import (
    CONTAINER_FONT_PREFERENCE,
    AppError,
    build_launch_command,
    launch_bindings,
    parse_target,
    probe_app_start,
    run_app_launch,
)
from termux_desk.container import PROOT_DISTRO_PROGRAM
from termux_desk.settings import Settings, SettingsError


class ParseTargetTest(unittest.TestCase):
    def test_container_and_app_are_split_at_the_first_slash(self):
        target = parse_target("ubuntu-desktop-test/xterm")
        self.assertEqual(target.container, "ubuntu-desktop-test")
        self.assertEqual(target.app, "xterm")
        self.assertEqual(target.spec, "ubuntu-desktop-test/xterm")

    def test_app_may_be_a_path_inside_the_container(self):
        self.assertEqual(parse_target("ubuntu/usr/bin/xterm").app, "usr/bin/xterm")

    def test_missing_parts_are_rejected(self):
        for text in ("ubuntu", "ubuntu/", "/xterm", ""):
            with self.subTest(text=text):
                with self.assertRaises(AppError):
                    parse_target(text)

    def test_invalid_container_name_is_rejected(self):
        with self.assertRaises(AppError):
            parse_target("--image/xterm")

    def test_option_like_program_is_rejected(self):
        with self.assertRaises(AppError):
            parse_target("ubuntu/-xterm")


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)
        self.workspace.write_state(make_state())
        self.workspace.create_socket(self.workspace.paths.x11_socket)

    def run_launch(self, text="ubuntu-desktop-test/xterm", **kwargs):
        output = io.StringIO()
        calls = []

        def record(path, argv):
            calls.append((path, argv))

        kwargs.setdefault("exec_func", record)
        run_app_launch(
            self.workspace.paths,
            text,
            environ=self.workspace.environ,
            out=output,
            **kwargs,
        )
        return output.getvalue(), calls

    def test_bindings_use_the_project_mount_points(self):
        paths = self.workspace.paths
        self.assertEqual(
            launch_bindings(
                paths,
                self.workspace.environ,
                font_host_dir="/nonexistent/system-fonts",
            ),
            (
                (str(paths.termux_data_dir), "/termux"),
                (str(paths.home), "/home/termux"),
                ("/storage/emulated/0", "/sdcard"),
            ),
        )

    def test_system_fonts_are_added_when_the_directory_exists(self):
        font_dir = Path(self.temporary.name) / "system-fonts"
        font_dir.mkdir()
        binds = launch_bindings(
            self.workspace.paths,
            self.workspace.environ,
            font_host_dir=str(font_dir),
        )
        self.assertEqual(
            binds[-1], (str(font_dir), "/usr/share/fonts/truetype/android")
        )
        self.assertEqual(len(binds), 4)

    def test_the_font_preference_is_bound_when_desk_setup_wrote_it(self):
        preference_directory = self.workspace.prefix / "etc" / "fonts" / "conf.d"
        preference_directory.mkdir(parents=True)
        preference = preference_directory / "63-cjk-prefer.conf"
        preference.write_text("<!-- Written by desk setup. -->\n", encoding="utf-8")
        binds = launch_bindings(
            self.workspace.paths,
            self.workspace.environ,
            font_host_dir="/nonexistent/system-fonts",
        )
        self.assertEqual(binds[-1], (str(preference), CONTAINER_FONT_PREFERENCE))
        self.assertEqual(len(binds), 4)

    def test_the_font_preference_is_not_bound_when_it_is_missing(self):
        binds = launch_bindings(
            self.workspace.paths,
            self.workspace.environ,
            font_host_dir="/nonexistent/system-fonts",
        )
        targets = [target for _, target in binds]
        self.assertNotIn(CONTAINER_FONT_PREFERENCE, targets)

    def test_external_storage_can_be_overridden(self):
        environ = dict(self.workspace.environ, EXTERNAL_STORAGE="/storage/abcd-1234")
        self.assertEqual(
            launch_bindings(
                self.workspace.paths, environ, font_host_dir="/nonexistent/fonts"
            )[2],
            ("/storage/abcd-1234", "/sdcard"),
        )

    def test_launch_command_shape(self):
        target = parse_target("ubuntu-desktop-test/xterm")
        binds = launch_bindings(
            self.workspace.paths,
            self.workspace.environ,
            font_host_dir="/nonexistent/system-fonts",
        )
        command = build_launch_command("/usr/bin/proot-distro", target, ":0", binds)
        self.assertEqual(command[0], "/usr/bin/proot-distro")
        self.assertEqual(command[1:4], ("sh", "ubuntu-desktop-test", "--isolated"))
        self.assertIn("--shared-tmp", command)
        self.assertEqual(
            command[-8:],
            (
                "-e",
                "DISPLAY=:0",
                "-e",
                "LANG=C.UTF-8",
                "-e",
                "LC_ALL=C.UTF-8",
                "--",
                "xterm",
            ),
        )

    def test_extra_environment_lands_before_the_command(self):
        target = parse_target("ubuntu-desktop-test/xterm")
        binds = launch_bindings(self.workspace.paths, self.workspace.environ)
        command = build_launch_command(
            "/usr/bin/proot-distro",
            target,
            ":0",
            binds,
            extra_environment=(("GTK_IM_MODULE", "xim"), ("XMODIFIERS", "@im=ibus")),
        )
        self.assertIn("GTK_IM_MODULE=xim", command)
        self.assertIn("XMODIFIERS=@im=ibus", command)
        self.assertLess(command.index("GTK_IM_MODULE=xim"), command.index("--"))

    def test_arguments_replace_the_program_name(self):
        target = parse_target("ubuntu/Alacritty")
        binds = launch_bindings(self.workspace.paths, self.workspace.environ)
        command = build_launch_command(
            "/usr/bin/proot-distro", target, ":0", binds, ("alacritty", "-e", "sh")
        )
        self.assertEqual(command[-4:], ("--", "alacritty", "-e", "sh"))

    def test_launch_replaces_the_desk_process(self):
        output, calls = self.run_launch()
        self.assertEqual(len(calls), 1)
        program, argv = calls[0]
        self.assertTrue(program.endswith("proot-distro"))
        self.assertEqual(argv[-1], "xterm")
        self.assertIn("--bind", argv)
        self.assertIn("Launching ubuntu-desktop-test/xterm on :0.", output)

    def test_launch_without_a_session_is_refused(self):
        self.workspace.paths.session_file.unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("not running", str(raised.exception))

    def test_launch_without_the_x11_socket_is_refused(self):
        self.workspace.paths.x11_socket.unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("socket", str(raised.exception))

    def test_missing_proot_distro_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        with self.assertRaises(AppError) as raised:
            self.run_launch()
        self.assertIn("proot-distro", str(raised.exception))

    def test_exec_failure_is_reported(self):
        def fail(path, argv):
            raise OSError("no such file")

        with self.assertRaises(AppError) as raised:
            self.run_launch(exec_func=fail)
        self.assertIn("could not launch", str(raised.exception))


if __name__ == "__main__":
    unittest.main()


try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - exercised on a host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")


class ConfigOverrideTest(CommandTest):
    """desk app launch reads the optional config.yaml app settings."""

    def write_config(self, text):
        path = (
            self.workspace.home
            / ".config"
            / "termux-desktop"
            / "config.yaml"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @needs_yaml
    def test_program_and_args_prefix_replace_the_command(self):
        self.write_config(
            'version: 1\n'
            'apps:\n'
            '  "ubuntu-desktop-test/xterm":\n'
            '    program: /usr/local/bin/myxterm\n'
            '    args_prefix: ["-fa", "Noto Sans CJK SC"]\n'
        )
        _, calls = self.run_launch()
        argv = calls[0][1]
        start = argv.index("/usr/local/bin/myxterm")
        self.assertEqual(argv[start + 1 : start + 3], ["-fa", "Noto Sans CJK SC"])

    @needs_yaml
    def test_arguments_after_the_program_are_kept(self):
        self.write_config(
            'version: 1\n'
            'apps:\n'
            '  "ubuntu-desktop-test/xterm":\n'
            '    program: /usr/local/bin/myxterm\n'
        )
        _, calls = self.run_launch(arguments=("/usr/share/xterm", "-rv"))
        argv = calls[0][1]
        start = argv.index("/usr/local/bin/myxterm")
        self.assertEqual(argv[start + 1 : start + 3], ["-rv"])

    @needs_yaml
    def test_environment_values_are_passed_to_proot(self):
        self.write_config(
            'version: 1\n'
            'apps:\n'
            '  "ubuntu-desktop-test/xterm":\n'
            '    environment:\n'
            '      GTK_IM_MODULE: xim\n'
            '      XMODIFIERS: "@im=ibus"\n'
        )
        _, calls = self.run_launch()
        argv = calls[0][1]
        joined = " ".join(argv)
        self.assertIn("GTK_IM_MODULE=xim", joined)
        self.assertIn("XMODIFIERS=@im=ibus", joined)

    @needs_yaml
    def test_the_configuration_can_replace_a_default_environment_value(self):
        self.write_config(
            'version: 1\n'
            'apps:\n'
            '  "ubuntu-desktop-test/xterm":\n'
            '    environment:\n'
            '      LANG: en_US.UTF-8\n'
        )
        _, calls = self.run_launch()
        joined = " ".join(calls[0][1])
        self.assertIn("LANG=en_US.UTF-8", joined)
        self.assertNotIn("LANG=C.UTF-8 LANG", joined)

    @needs_yaml
    def test_an_invalid_configuration_stops_the_launch(self):
        self.write_config("version: 2\n")
        with self.assertRaises(SettingsError) as raised:
            self.run_launch()
        self.assertIn("version", str(raised.exception))

    def test_a_missing_file_keeps_the_old_command(self):
        _, calls = self.run_launch()
        argv = calls[0][1]
        self.assertEqual(argv[-1], "xterm")


class TolerantSettingsTest(unittest.TestCase):
    def test_a_launch_reads_settings_tolerantly(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(Path(temporary.name))
        workspace.add_program(PROOT_DISTRO_PROGRAM)
        workspace.write_state(make_state())
        workspace.create_socket(workspace.paths.x11_socket)
        with mock.patch(
            "termux_desk.app.settings.load_settings", return_value=Settings()
        ) as loader:
            run_app_launch(
                workspace.paths,
                "ubuntu-desktop-test/xterm",
                environ=workspace.environ,
                out=io.StringIO(),
                exec_func=lambda path, argv: None,
            )
        self.assertTrue(loader.call_args.kwargs.get("allow_missing_pyyaml"))

    def test_a_launch_follows_the_recorded_session_profile(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Workspace(Path(temporary.name))
        workspace.add_program(PROOT_DISTRO_PROGRAM)
        workspace.write_state(make_state(profile="work"))
        workspace.create_socket(workspace.paths.x11_socket)
        environ = dict(workspace.environ)
        environ.pop("DESK_PROFILE", None)
        with mock.patch(
            "termux_desk.app.settings.load_settings", return_value=Settings()
        ) as loader:
            run_app_launch(
                workspace.paths,
                "ubuntu-desktop-test/xterm",
                environ=environ,
                out=io.StringIO(),
                exec_func=lambda path, argv: None,
            )
        self.assertEqual(
            loader.call_args.kwargs["environ"].get("DESK_PROFILE"), "work"
        )


class _FakeProcess:
    def __init__(self, waits, pid=4242):
        self.pid = pid
        self.waits = list(waits)

    def wait(self, timeout=None):
        item = self.waits.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)
        self.workspace.write_state(make_state())
        self.workspace.create_socket(self.workspace.paths.x11_socket)

    def spawn(self, waits, stderr=b""):
        recorded = []

        def start(command, **kwargs):
            recorded.append((command, kwargs))
            kwargs["stderr"].write(stderr)
            return _FakeProcess(waits)

        return start, recorded

    def test_a_process_still_running_after_the_timeout_started(self):
        start, recorded = self.spawn(
            [subprocess.TimeoutExpired("cmd", 5.0), 0]
        )
        with mock.patch("termux_desk.app.os.killpg") as killpg:
            probe = probe_app_start(
                self.workspace.paths,
                "ubuntu-desktop-test/xterm",
                arguments=("xterm",),
                timeout=5.0,
                environ=self.workspace.environ,
                spawn=start,
            )
        self.assertEqual(probe.status, "started")
        self.assertTrue(recorded[0][1].get("start_new_session"))
        self.assertIs(recorded[0][1].get("stdin"), subprocess.DEVNULL)
        killpg.assert_called_once()

    def test_a_zero_exit_counts_as_started_and_exited(self):
        start, _recorded = self.spawn([0])
        probe = probe_app_start(
            self.workspace.paths,
            "ubuntu-desktop-test/xterm",
            arguments=("xterm",),
            environ=self.workspace.environ,
            spawn=start,
        )
        self.assertEqual(probe.status, "exited")
        self.assertEqual(probe.exit_code, 0)

    def test_a_nonzero_exit_reports_the_stderr_tail(self):
        start, _recorded = self.spawn([2], stderr=b"boom\nno display\n")
        probe = probe_app_start(
            self.workspace.paths,
            "ubuntu-desktop-test/xterm",
            arguments=("xterm",),
            environ=self.workspace.environ,
            spawn=start,
        )
        self.assertEqual(probe.status, "failed")
        self.assertEqual(probe.exit_code, 2)
        self.assertIn("no display", probe.detail)

    def test_a_large_stderr_keeps_the_last_lines(self):
        stderr = b"noise\n" * 12000 + b"FATAL: boom\n"
        start, _recorded = self.spawn([2], stderr=stderr)
        probe = probe_app_start(
            self.workspace.paths,
            "ubuntu-desktop-test/xterm",
            arguments=("xterm",),
            environ=self.workspace.environ,
            spawn=start,
        )
        self.assertIn("FATAL: boom", probe.detail)

    def test_a_spawn_error_is_reported_as_failed(self):
        def start(command, **kwargs):
            raise OSError("cannot execute")

        probe = probe_app_start(
            self.workspace.paths,
            "ubuntu-desktop-test/xterm",
            arguments=("xterm",),
            environ=self.workspace.environ,
            spawn=start,
        )
        self.assertEqual(probe.status, "failed")
        self.assertIn("cannot execute", probe.detail)
