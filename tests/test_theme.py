"""Tests for the theme manifests, assets and the xfconf writer."""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from support import Workspace, make_result

from termux_desk import theme, theme_assets, theme_session

try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover - devices without PyYAML skip parsing
    yaml = None


def needs_yaml(function):
    return unittest.skipIf(yaml is None, "PyYAML is not installed")(function)


class ThemeCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths

    def write_theme(self, text: str, name: str = "test") -> Path:
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def add_icon_theme(self, name: str = "Adwaita") -> None:
        directory = self.workspace.prefix / "share" / "icons" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")


@needs_yaml
class ManifestTest(ThemeCase):
    def test_stock_preset_is_available(self):
        theme_manifest = theme.load_theme(self.paths, "stock")
        self.assertEqual(theme_manifest.components["gtk"], "Adwaita")
        self.assertEqual(theme_manifest.components["xfwm"], "Default")

    def test_unknown_theme_is_an_error(self):
        with self.assertRaises(theme.ThemeError):
            theme.load_theme(self.paths, "missing")

    def test_duplicate_keys_are_rejected(self):
        self.write_theme("theme: test\ncomponents:\n  gtk: Adwaita\n  gtk: Adwaita-dark\n")
        with self.assertRaisesRegex(theme.ThemeError, "repeats the key"):
            theme.load_theme(self.paths, "test")

    def test_unknown_component_is_rejected(self):
        self.write_theme("theme: test\ncomponents:\n  panel: xfce4-panel\n")
        with self.assertRaisesRegex(theme.ThemeError, "unknown components"):
            theme.load_theme(self.paths, "test")

    def test_assets_need_exactly_one_source(self):
        self.write_theme(
            "theme: test\nassets:\n  - kind: font\n    target: f.ttf\n    url: https://x/f.ttf\n    path: f.ttf\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "both"):
            theme.load_theme(self.paths, "test")

    def test_asset_sha256_must_be_hex(self):
        self.write_theme("theme: test\nassets:\n  - kind: font\n    target: f.ttf\n    url: https://x/f.ttf\n    sha256: nope\n")
        with self.assertRaisesRegex(theme.ThemeError, "sha256"):
            theme.load_theme(self.paths, "test")

    def test_list_marks_the_active_theme(self):
        state = theme.ThemeState(active="stock")
        theme._write_state(self.paths, state)
        output = io.StringIO()
        self.assertEqual(theme.run_theme_list(self.paths, out=output), 0)
        self.assertIn("* stock (preset)", output.getvalue())

    def test_init_writes_and_refuses_to_overwrite(self):
        output = io.StringIO()
        self.assertEqual(theme.run_theme_init(self.paths, "mine", force=False, out=output), 0)
        path = theme.themes_directory(self.paths) / "mine.yaml"
        self.assertIn("theme: mine", path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(theme.ThemeError, "already exists"):
            theme.run_theme_init(self.paths, "mine", force=False, out=io.StringIO())


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.values = {"/Net/ThemeName": "OldTheme", "/backdrop/screen0/monitor0/workspace0/last-image": "/old.png"}

    def __call__(self, argv, env):
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if command[0] in ("xfsettingsd", "xfwm4"):
            return make_result(command)
        if command[:3] == ("xfconf-query", "-c", "xfce4-desktop") and "-l" in command:
            return make_result(command, stdout="/backdrop/screen0/monitor0/workspace0/last-image\n")
        if "-s" in command:
            name = command[command.index("-p") + 1]
            self.values[name] = command[command.index("-s") + 1]
            return make_result(command)
        if "-r" in command:
            name = command[command.index("-p") + 1]
            self.values.pop(name, None)
            return make_result(command)
        if "-p" in command:
            name = command[command.index("-p") + 1]
            if name in self.values:
                return make_result(command, stdout=self.values[name] + "\n")
            return make_result(command, returncode=1)
        return make_result(command)


class FakeMatcher:
    def family_exists(self, name):
        return True


@needs_yaml
class ApplyTest(ThemeCase):
    def setUp(self):
        super().setUp()
        self.add_icon_theme()
        self.runner = FakeRunner()
        self.session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"}
        self.fetch = lambda url: b"asset"

    def test_apply_writes_the_components_and_snapshots_the_old_values(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  xfwm: Default\n  icons: Adwaita\n"
            "  wallpaper: image.png\nassets:\n  - kind: wallpaper\n    target: image.png\n    path: image.png\n"
        )
        (theme.themes_directory(self.paths) / "image.png").write_bytes(b"png")
        output = io.StringIO()
        self.assertEqual(
            theme.run_theme_apply(
                self.paths,
                "test",
                out=output,
                session=self.session,
                runner=self.runner,
                matcher=FakeMatcher(),
                fetch=self.fetch,
            ),
            0,
        )
        self.assertIn("applied appearance test", output.getvalue())
        state = theme.read_state(self.paths)
        self.assertEqual(state.active, "test")
        self.assertTrue(any(item["property"] == "/Net/ThemeName" and item["value"] == "OldTheme" for item in state.snapshot))
        self.assertIn(("xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName", "-s", "Adwaita"), self.runner.calls)
        self.assertIn(("xfconf-query", "-c", "xfwm4", "-p", "/general/theme", "-s", "Default"), self.runner.calls)

    def test_reset_restores_the_snapshot(self):
        theme._write_state(
            self.paths,
            theme.ThemeState(
                active="test",
                snapshot=(
                    {"channel": "xsettings", "property": "/Net/ThemeName", "value": "OldTheme"},
                    {"channel": "xsettings", "property": "/Net/IconThemeName", "value": None},
                    {"channel": "xfce4-desktop", "property": "/backdrop/screen0/monitor0/workspace0/last-image", "value": "/old.png"},
                ),
            ),
        )
        output = io.StringIO()
        self.assertEqual(theme.run_theme_reset(self.paths, out=output, session=self.session, runner=self.runner), 0)
        self.assertIn(("xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName", "-s", "OldTheme"), self.runner.calls)
        self.assertIn(("xfconf-query", "-c", "xsettings", "-p", "/Net/IconThemeName", "-r"), self.runner.calls)
        self.assertEqual(theme.read_state(self.paths).active, None)

    def test_font_asset_is_installed_in_the_home_fonts_directory(self):
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / "MyFont.ttf"
        source.write_bytes(b"ttf")
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "assets:\n  - kind: font\n    target: MyFont.ttf\n    path: MyFont.ttf\n"
        )
        output = io.StringIO()
        theme.run_theme_apply(
            self.paths, "test", out=output, session=self.session, runner=self.runner, matcher=FakeMatcher(), fetch=self.fetch
        )
        self.assertTrue((self.paths.home / ".fonts" / "MyFont.ttf").is_file())

    def test_unverified_download_needs_the_flag_at_first(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n    url: https://example.invalid/wall.png\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "allow-unverified"):
            theme.run_theme_apply(
                self.paths, "test", out=io.StringIO(), session=self.session, runner=self.runner, matcher=FakeMatcher(), fetch=self.fetch
            )
        theme.run_theme_apply(
            self.paths, "test", out=io.StringIO(), session=self.session, runner=self.runner, matcher=FakeMatcher(), fetch=self.fetch, allow_unverified=True
        )
        self.assertEqual(
            theme.read_state(self.paths).hashes["https://example.invalid/wall.png"],
            "d59386e0ae435e292fbe0ebcdb954b75ed5fb3922091277cb19f798fc5d50718",
        )

    def test_an_unknown_component_leaves_no_assets_behind(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: NoSuchTheme\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n"
            "    url: https://example.invalid/wall.png\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "missing"):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session=self.session,
                runner=self.runner,
                matcher=FakeMatcher(),
                fetch=self.fetch,
                allow_unverified=True,
            )
        self.assertEqual(sorted(theme.assets_directory(self.paths, "test").rglob("*")), [])
        self.assertEqual(theme.read_state(self.paths).active, None)

    def test_unknown_component_fails_before_writing(self):
        self.write_theme("theme: test\ncomponents:\n  gtk: NoSuchTheme\n")
        before = list(self.runner.calls)
        with self.assertRaisesRegex(theme.ThemeError, "missing"):
            theme.run_theme_apply(
                self.paths, "test", out=io.StringIO(), session=self.session, runner=self.runner, matcher=FakeMatcher(), fetch=self.fetch
            )
        self.assertEqual(self.runner.calls, before)


class ManifestFailureTest(ThemeCase):
    def test_list_reports_an_invalid_manifest_with_a_failing_exit_code(self):
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "broken.yaml").write_text("theme: broken\ncomponents: [\n", encoding="utf-8")
        output = io.StringIO()
        code = theme.run_theme_list(self.paths, out=output)
        self.assertEqual(code, 1)
        self.assertIn("broken: invalid:", output.getvalue())

    def test_a_manifest_that_is_not_utf8_is_reported(self):
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bad.yaml").write_bytes(b"theme: bad\n\xff\xfe")
        with self.assertRaisesRegex(theme.ThemeError, "is not UTF-8"):
            theme.load_theme(self.paths, "bad")

    def test_a_manifest_with_a_different_name_is_rejected(self):
        self.write_theme("theme: other\ncomponents:\n  gtk: Adwaita\n")
        with self.assertRaisesRegex(theme.ThemeError, "must match the file name"):
            theme.load_theme(self.paths, "test")

    def test_a_missing_xfconf_query_is_a_readable_error(self):
        with self.assertRaisesRegex(theme.ThemeError, "was not found"):
            theme._default_runner(("desk-no-such-xfconf-tool", "--version"), {})


class SnapshotTest(ThemeCase):
    def _theme_case(self):
        self.add_icon_theme()
        self.write_theme("theme: test\ncomponents:\n  gtk: Adwaita\n")

    def test_an_empty_property_is_recorded_and_restored_as_empty(self):
        self._theme_case()
        runner = FakeRunner()
        runner.values["/Net/ThemeName"] = ""
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        theme.run_theme_apply(
            self.paths, "test", out=io.StringIO(), runner=runner, matcher=FakeMatcher(), session=session
        )
        self.assertEqual(runner.values["/Net/ThemeName"], "Adwaita")
        theme.run_theme_reset(self.paths, out=io.StringIO(), runner=runner, session=session)
        self.assertEqual(runner.values["/Net/ThemeName"], "")

    def test_a_string_array_is_written_with_force_array(self):
        calls = []

        def runner(argv, env):
            calls.append(tuple(argv))
            return make_result(tuple(argv))

        theme_session._set_property(
            "xfwm4", "/general/workspace_names", ("1", "2"), {}, runner
        )
        self.assertEqual(
            calls[0],
            (
                "xfconf-query", "-c", "xfwm4", "-p", "/general/workspace_names",
                "-n", "-a", "-t", "string", "-s", "1", "-t", "string", "-s", "2",
            ),
        )

    def test_recorded_array_output_is_restored_as_an_array(self):
        calls = []

        def runner(argv, env):
            calls.append(tuple(argv))
            return make_result(tuple(argv))

        theme_session._set_property(
            "xfwm4",
            "/general/workspace_names",
            "Value is an array with 2 items:\n\nWorkspace 1\nWorkspace 2",
            {},
            runner,
        )
        self.assertEqual(
            calls[0],
            (
                "xfconf-query", "-c", "xfwm4", "-p", "/general/workspace_names",
                "-n", "-a", "-t", "string", "-s", "Workspace 1", "-t", "string", "-s", "Workspace 2",
            ),
        )

    def test_a_new_property_is_created_with_its_real_type(self):
        command = ("xfconf-query", "-c", "xfwm4", "-p", "/general/use_compositing", "-s", "true")
        runner = FakeRunner()
        failing = []

        def only_missing(argv, env):
            if "-s" in argv and "-n" not in argv:
                failing.append(tuple(argv))
                return make_result(tuple(argv), returncode=1)
            return runner(argv, env)

        theme_session._set_property("xfwm4", "/general/use_compositing", "true", {}, only_missing)
        created = failing and runner.calls[-1]
        self.assertIn("-n", runner.calls[-1])
        self.assertEqual(runner.calls[-1][runner.calls[-1].index("-t") + 1], "bool")
        del command, created

    def test_the_snapshot_keeps_only_the_properties_the_theme_changes(self):
        self._theme_case()
        runner = FakeRunner()
        session = {"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"}
        theme.run_theme_apply(
            self.paths, "test", out=io.StringIO(), runner=runner, matcher=FakeMatcher(), session=session
        )
        state = theme.read_state(self.paths)
        names = [item["property"] for item in state.snapshot]
        self.assertEqual(names, ["/Net/ThemeName"])
        self.assertEqual(state.display, ":0")

    def test_reset_refuses_a_different_display(self):
        self._theme_case()
        runner = FakeRunner()
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            runner=runner,
            matcher=FakeMatcher(),
            display=":1",
            session={"DISPLAY": ":1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"},
        )
        with self.assertRaisesRegex(theme.ThemeError, "was applied on :1"):
            theme.run_theme_reset(self.paths, out=io.StringIO(), runner=runner)

    def test_a_dry_run_reports_the_values_without_writing(self):
        self._theme_case()
        runner = FakeRunner()
        output = io.StringIO()
        code = theme.run_theme_apply(
            self.paths,
            "test",
            out=output,
            runner=runner,
            matcher=FakeMatcher(),
            dry_run=True,
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/x"},
        )
        self.assertEqual(code, 0)
        self.assertIn("would set xsettings/Net/ThemeName: Adwaita", output.getvalue())
        self.assertNotIn("-s", " ".join(" ".join(call) for call in runner.calls))
        self.assertIsNone(theme.read_state(self.paths).active)


class AssetSourceTest(ThemeCase):
    def test_asset_targets_that_escape_the_directory_are_rejected(self):
        for target in ("..", ".", "a/b", "a\x1b]52;c;cGF5bG9hZA==\x07"):
            self.write_theme(
                "theme: test\ncomponents:\n  gtk: Adwaita\n"
                f"assets:\n  - kind: font\n    target: {target!r}\n    path: font.ttf\n"
            )
            with self.assertRaisesRegex(theme.ThemeError, "usable target"):
                theme.load_theme(self.paths, "test")

    def test_a_url_asset_is_never_shadowed_by_a_local_file(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  wallpaper: wall.png\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n"
            "    url: https://example.invalid/wall.png\n"
            "    sha256: b7a8a844a613be796bc1892dc480f9d92c50d32a5713a87758e5c5addc4ec814\n"
        )
        shadow = theme.themes_directory(self.paths) / "https:" / "example.invalid"
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "wall.png").write_bytes(b"local-bytes")
        fetched: list[str] = []
        output = io.StringIO()
        theme.run_theme_apply(
            self.paths,
            "test",
            out=output,
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
            fetch=lambda url: fetched.append(url) or b"downloaded",
        )
        self.assertEqual(fetched, ["https://example.invalid/wall.png"])
        installed = theme.assets_directory(self.paths, "test") / "wallpapers" / "wall.png"
        self.assertEqual(installed.read_bytes(), b"downloaded")

    def test_a_local_asset_must_match_its_sha256(self):
        self.add_icon_theme()
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "wall.png").write_bytes(b"other")
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n    path: wall.png\n"
            "    sha256: cea23dd4b87e8b00d19fb9ccaaef93e97353c7353e2070f3baf05aeb3995dff4\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "sha256 mismatch"):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=FakeRunner(),
                matcher=FakeMatcher(),
                fetch=lambda url: b"downloaded",
            )

    def test_a_wallpaper_archive_installs_only_the_named_image(self):
        import tarfile
        self.add_icon_theme()
        source_dir = theme.themes_directory(self.paths) / "wallpapers"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "wanted.png").write_bytes(b"wanted")
        (source_dir / "other.png").write_bytes(b"other")
        archive = theme.themes_directory(self.paths) / "wallpapers.tar.gz"
        with tarfile.open(archive, "w:gz") as packed:
            for entry in sorted(source_dir.iterdir()):
                packed.add(entry, arcname=entry.name)
        shutil.rmtree(source_dir)
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  wallpaper: wanted.png\n"
            "assets:\n  - kind: wallpaper\n    target: wanted.png\n"
            "    path: wallpapers.tar.gz\n"
        )
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        installed = theme.assets_directory(self.paths, "test") / "wallpapers"
        self.assertEqual((installed / "wanted.png").read_bytes(), b"wanted")
        self.assertFalse((installed / "other.png").exists())

    def test_a_wallpaper_pick_naming_a_directory_installs_every_image(self):
        import tarfile

        self.add_icon_theme()
        (theme.themes_directory(self.paths)).mkdir(parents=True, exist_ok=True)
        source_dir = theme.themes_directory(self.paths) / "Backgrounds" / "Wallpaper"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "wanted.png").write_bytes(b"wanted")
        (source_dir / "other.png").write_bytes(b"other")
        (source_dir / "notes.txt").write_text("not an image", encoding="utf-8")
        archive = theme.themes_directory(self.paths) / "wallpapers.tar.gz"
        with tarfile.open(archive, "w:gz") as packed:
            packed.add(source_dir, arcname="Backgrounds/Wallpaper")
        shutil.rmtree(source_dir.parent)
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  wallpaper: wanted.png\n"
            "assets:\n  - kind: wallpaper\n    target: wanted.png\n"
            "    pick: Backgrounds/Wallpaper\n"
            "    path: wallpapers.tar.gz\n"
        )
        runner = FakeRunner()
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=runner,
            matcher=FakeMatcher(),
        )
        installed = theme.assets_directory(self.paths, "test") / "wallpapers"
        self.assertEqual((installed / "wanted.png").read_bytes(), b"wanted")
        self.assertEqual((installed / "other.png").read_bytes(), b"other")
        self.assertFalse((installed / "notes.txt").exists())
        self.assertIn(
            (
                "xfconf-query",
                "-c",
                "xfce4-desktop",
                "-p",
                "/backdrop/screen0/monitor0/workspace0/last-image",
                "-s",
                str(installed / "wanted.png"),
            ),
            runner.calls,
        )

    def test_a_wallpaper_pick_that_names_no_directory_or_image_is_reported(self):
        import tarfile

        (theme.themes_directory(self.paths)).mkdir(parents=True, exist_ok=True)
        source_file = theme.themes_directory(self.paths) / "only.png"
        source_file.write_bytes(b"png")
        archive = theme.themes_directory(self.paths) / "wallpapers.tar.gz"
        with tarfile.open(archive, "w:gz") as packed:
            packed.add(source_file, arcname="only.png")
        source_file.unlink()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  wallpaper: wanted.png\n"
            "assets:\n  - kind: wallpaper\n    target: wanted.png\n"
            "    pick: Backgrounds/Missing\n"
            "    path: wallpapers.tar.gz\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "does not contain 'wanted.png'"):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=FakeRunner(),
                matcher=FakeMatcher(),
            )

    def test_a_verified_download_is_reused_from_the_cache(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  xfwm: Default\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n"
            "    url: https://example.invalid/wall.png\n"
            "    sha256: b7a8a844a613be796bc1892dc480f9d92c50d32a5713a87758e5c5addc4ec814\n"
        )
        fetched: list[str] = []

        def fetch(url: str) -> bytes:
            fetched.append(url)
            return b"downloaded"

        for _ in range(2):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=FakeRunner(),
                matcher=FakeMatcher(),
                fetch=fetch,
            )
        self.assertEqual(len(fetched), 1)


class ApplySafetyTest(ThemeCase):
    def failing_runner(self, property_name: str) -> FakeRunner:
        class Failing(FakeRunner):
            def __call__(self, argv, env):
                command = tuple(str(item) for item in argv)
                if "-s" in command and property_name in command:
                    self.calls.append(command)
                    return make_result(command, returncode=1, stderr="boom")
                return super().__call__(argv, env)

        return Failing()

    def test_a_failed_apply_puts_the_old_values_back(self):
        self.add_icon_theme()
        self.write_theme("theme: test\ncomponents:\n  gtk: Adwaita\n  icons: Adwaita\n  xfwm: Default\n")
        runner = self.failing_runner("/general/theme")
        with self.assertRaisesRegex(theme.ThemeError, "could not set xfwm4/general/theme"):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=runner,
                matcher=FakeMatcher(),
            )
        self.assertEqual(runner.values["/Net/ThemeName"], "OldTheme")
        self.assertIsNone(theme.read_state(self.paths).active)
        self.assertEqual(theme.read_state(self.paths).snapshot, ())

    def test_assets_are_not_installed_when_a_component_is_missing(self):
        directory = theme.themes_directory(self.paths)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "wall.png").write_bytes(b"wall")
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: NotInstalled\n  wallpaper: wall.png\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n    path: wall.png\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "components are missing"):
            theme.run_theme_apply(
                self.paths,
                "test",
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=FakeRunner(),
                matcher=FakeMatcher(),
            )
        self.assertFalse((theme.assets_directory(self.paths, "test") / "wallpapers").exists())

    def test_applying_twice_keeps_the_first_snapshot(self):
        self.add_icon_theme()
        self.write_theme("theme: one\ncomponents:\n  gtk: Adwaita\n", name="one")
        self.write_theme("theme: two\ncomponents:\n  gtk: Adwaita-dark\n", name="two")
        runner = FakeRunner()
        for name in ("one", "two"):
            theme.run_theme_apply(
                self.paths,
                name,
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=runner,
                matcher=FakeMatcher(),
            )
        theme.run_theme_reset(
            self.paths,
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=runner,
        )
        self.assertEqual(runner.values["/Net/ThemeName"], "OldTheme")

    def test_a_download_hash_survives_a_failed_apply(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  xfwm: Default\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n"
            "    url: https://example.invalid/wall.png\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "could not set"):
            theme.run_theme_apply(
                self.paths,
                "test",
                allow_unverified=True,
                out=io.StringIO(),
                session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                runner=self.failing_runner("/Net/ThemeName"),
                matcher=FakeMatcher(),
                fetch=lambda url: b"downloaded",
            )
        self.assertEqual(
            theme.read_state(self.paths).hashes, {"https://example.invalid/wall.png": theme._sha256(b"downloaded")}
        )


class DownloadSafetyTest(ThemeCase):
    def test_a_redirect_to_plain_http_is_refused(self):
        handler = theme_assets._HttpsRedirect()
        request = urllib.request.Request("https://example.invalid/asset")
        with self.assertRaisesRegex(theme.ThemeError, "which is not HTTPS"):
            handler.redirect_request(request, None, 302, "Found", {}, "http://example.invalid/asset")

    def test_an_archive_with_too_many_entries_is_refused(self):
        archive = Path(self.temporary.name) / "many.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            for index in range(11):
                member = tarfile.TarInfo(f"file{index}")
                member.size = 1
                handle.addfile(member, io.BytesIO(b"x"))
        original = theme_assets.MAX_ARCHIVE_ENTRIES
        theme_assets.MAX_ARCHIVE_ENTRIES = 10
        try:
            with self.assertRaisesRegex(theme.ThemeError, "entries"):
                theme._extract_archive(archive, Path(self.temporary.name) / "out", force=True)
        finally:
            theme_assets.MAX_ARCHIVE_ENTRIES = original

    def test_an_archive_that_unpacks_too_large_is_refused(self):
        archive = Path(self.temporary.name) / "big.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            member = tarfile.TarInfo("big.bin")
            member.size = 2048
            handle.addfile(member, io.BytesIO(b"x" * 2048))
        original = theme_assets.MAX_ARCHIVE_BYTES
        theme_assets.MAX_ARCHIVE_BYTES = 1024
        try:
            with self.assertRaisesRegex(theme.ThemeError, "unpacks to"):
                theme._extract_archive(archive, Path(self.temporary.name) / "out", force=True)
        finally:
            theme_assets.MAX_ARCHIVE_BYTES = original

    def test_a_local_asset_may_link_to_a_sibling_theme(self):
        builds = theme_assets._shared_roots(self.paths)[-1]
        base = builds / "base"
        base.mkdir(parents=True)
        (base / "scalable").mkdir()
        (base / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        thin = builds / "thin"
        thin.mkdir()
        (thin / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        (thin / "scalable").symlink_to("../base/scalable")
        out = io.StringIO()
        theme_assets._copy_tree(
            thin,
            self.paths.xdg_data_home / "icons" / "thin",
            force=True,
            allowed=theme_assets._shared_roots(self.paths),
            out=out,
        )
        self.assertTrue((self.paths.xdg_data_home / "icons" / "thin" / "scalable").is_symlink())
        self.assertIn("allowed shared link scalable ->", out.getvalue())

    def test_a_local_asset_linking_outside_the_shared_roots_is_refused(self):
        thin = Path(self.temporary.name) / "thin"
        thin.mkdir()
        (thin / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        (thin / "link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(theme.ThemeError, "contains a link"):
            theme_assets._copy_tree(
                thin,
                Path(self.temporary.name) / "copy",
                force=True,
                allowed=theme_assets._shared_roots(self.paths),
            )

    def test_a_cursor_theme_fills_in_an_installed_icon_theme(self):
        icons = self.paths.xdg_data_home / "icons" / "Shared"
        icons.mkdir(parents=True)
        (icons / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        source = Path(self.temporary.name) / "cursor"
        (source / "cursors").mkdir(parents=True)
        (source / "cursors" / "left_ptr").write_text("x", encoding="utf-8")
        asset = theme.ThemeAsset(kind="cursors", target="Shared", source="Shared")
        theme_assets._install_tree(self.paths, asset, source, force=False, out=io.StringIO())
        self.assertTrue((icons / "cursors" / "left_ptr").is_file())
        self.assertTrue((icons / "index.theme").is_file())

    def test_a_tree_with_a_link_is_refused(self):
        source = Path(self.temporary.name) / "tree"
        source.mkdir()
        (source / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        (source / "link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(theme.ThemeError, "contains a link"):
            theme_assets._copy_tree(source, Path(self.temporary.name) / "copy", force=True)

    def test_a_kept_tree_still_gets_its_legacy_link(self):
        target = Path(self.temporary.name) / "installed"
        target.mkdir()
        asset = theme.ThemeAsset(kind="theme", target="Mine", source="Mine")
        theme_assets._install_tree(self.paths, asset, target, force=False, out=io.StringIO())
        self.assertTrue((self.paths.home / ".themes" / "Mine").is_symlink())

    def test_force_replaces_a_real_directory_with_the_alias_link(self):
        target = Path(self.temporary.name) / "installed"
        target.mkdir()
        alias = Path(self.temporary.name) / "alias"
        alias.mkdir()
        (alias / "stale").write_text("x", encoding="utf-8")
        theme_assets._make_alias(alias, target, io.StringIO(), force=True)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(alias.resolve(), target.resolve())


class ArchiveSafetyTest(ThemeCase):
    def test_unsafe_archive_member_is_rejected(self):
        archive = Path(self.temporary.name) / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            handle.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(theme.ThemeError, "unsafe path"):
            theme._extract_archive(archive, Path(self.temporary.name) / "out", force=True)


class ConfigAssetTest(ThemeCase):
    def _theme(self, target: str, *, path: str = "uca.xml") -> None:
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            f"assets:\n  - kind: config\n    target: {target}\n    path: {path}\n"
        )

    def test_a_whitelisted_config_file_is_replaced_and_reset_restores_it(self):
        config = self.paths.home / ".config" / "Thunar" / "uca.xml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("<old/>", encoding="utf-8")
        theme.themes_directory(self.paths).mkdir(parents=True, exist_ok=True)
        (theme.themes_directory(self.paths) / "uca.xml").write_text("<new/>", encoding="utf-8")
        self._theme("Thunar/uca.xml")
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        self.assertEqual(config.read_text(encoding="utf-8"), "<new/>")
        theme.run_theme_reset(
            self.paths,
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
        )
        self.assertEqual(config.read_text(encoding="utf-8"), "<old/>")

    def test_a_config_file_the_skin_created_is_removed_on_reset(self):
        bookmark = self.paths.home / ".config" / "gtk-3.0" / "bookmarks"
        theme.themes_directory(self.paths).mkdir(parents=True, exist_ok=True)
        (theme.themes_directory(self.paths) / "bookmarks").write_text("file:///x\n", encoding="utf-8")
        self._theme("gtk-3.0/bookmarks", path="bookmarks")
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        self.assertTrue(bookmark.is_file())
        theme.run_theme_reset(
            self.paths,
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
        )
        self.assertFalse(bookmark.exists())

    def test_a_config_target_outside_the_whitelist_is_rejected(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "assets:\n  - kind: config\n    target: xfce4/xfconf/xfce-perchannel-xml/xfsettingsd.xml\n"
            "    path: uca.xml\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "cannot replace"):
            theme.load_theme(self.paths, "test")


class PickTest(ThemeCase):
    def _archive(self, root: str, entries: dict[str, str]) -> Path:
        archive = Path(self.temporary.name) / f"{root or 'bundle'}.tar.gz"
        prefix = f"{root}/" if root else ""
        with tarfile.open(archive, "w:gz") as handle:
            for name, text in entries.items():
                data = text.encode()
                member = tarfile.TarInfo(f"{prefix}{name}")
                member.size = len(data)
                handle.addfile(member, io.BytesIO(data))
        return archive

    def test_a_pick_installs_that_subdirectory_not_the_archive_root(self):
        archive = self._archive(
            "",
            {
                "Theme/Mine/gtk-3.0/gtk.css": "x",
                "Icons/Mine/index.theme": "[Icon Theme]\n",
            },
        )
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Mine\n"
            f"assets:\n  - kind: theme\n    target: Mine\n    pick: Theme/Mine\n"
            f"    path: {archive}\n"
        )
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        self.assertTrue((self.paths.home / ".themes" / "Mine" / "gtk-3.0" / "gtk.css").is_file())
        self.assertFalse((self.paths.home / ".themes" / "Mine" / "pkg").exists())

    def test_a_font_can_be_picked_out_of_an_archive(self):
        archive = self._archive("", {"Fonts/vga/Mine.ttf": "font-bytes"})
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            f"assets:\n  - kind: font\n    target: Mine.ttf\n    pick: Fonts/vga/Mine.ttf\n"
            f"    path: {archive}\n"
        )
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        self.assertEqual((self.paths.home / ".fonts" / "Mine.ttf").read_text(), "font-bytes")

    def test_a_cursors_asset_installs_into_the_icon_directory(self):
        archive = self._archive("", {"MineCursors/cursors/left_ptr": "cursor-bytes"})
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  cursors: MineCursors\n"
            f"assets:\n  - kind: cursors\n    target: MineCursors\n    pick: MineCursors\n"
            f"    path: {archive}\n"
        )
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        destination = self.paths.xdg_data_home / "icons" / "MineCursors" / "cursors" / "left_ptr"
        self.assertEqual(destination.read_text(), "cursor-bytes")
        self.assertTrue((self.paths.home / ".icons" / "MineCursors").is_symlink())

    def test_a_cursors_asset_takes_the_cursors_subdirectory_of_a_local_theme(self):
        source = Path(self.temporary.name) / "Qogir-Dark"
        (source / "cursors").mkdir(parents=True)
        (source / "cursors" / "left_ptr").write_text("cursor-bytes", encoding="utf-8")
        (source / "index.theme").write_text("[Icon Theme]\n", encoding="utf-8")
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  cursors: Qogir-dark\n"
            f"assets:\n  - kind: cursors\n    target: Qogir-dark\n    pick: cursors\n"
            f"    path: {source}\n"
        )
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        destination = self.paths.xdg_data_home / "icons" / "Qogir-dark" / "cursors" / "left_ptr"
        self.assertEqual(destination.read_text(), "cursor-bytes")
        self.assertFalse((self.paths.xdg_data_home / "icons" / "Qogir-dark" / "index.theme").exists())

    def test_an_installed_cursor_theme_needs_no_asset_on_later_applies(self):
        source = Path(self.temporary.name) / "MineCursors"
        (source / "cursors").mkdir(parents=True)
        (source / "cursors" / "left_ptr").write_text("cursor-bytes", encoding="utf-8")
        self.write_theme(
            "theme: first\ncomponents:\n  gtk: Adwaita\n  cursors: MineCursors\n"
            f"assets:\n  - kind: cursors\n    target: MineCursors\n    path: {source}\n",
            name="first",
        )
        theme.run_theme_apply(
            self.paths,
            "first",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )
        self.write_theme(
            "theme: second\ncomponents:\n  gtk: Adwaita\n  cursors: MineCursors\nassets: []\n",
            name="second",
        )
        theme.run_theme_apply(
            self.paths,
            "second",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=FakeRunner(),
            matcher=FakeMatcher(),
        )

    def test_a_pick_that_escapes_the_archive_is_rejected(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "assets:\n  - kind: theme\n    target: Mine\n    pick: ../../etc\n    path: x\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "escapes the archive"):
            theme.load_theme(self.paths, "test")


class PackagesTest(ThemeCase):
    def test_package_names_are_parsed_and_deduplicated(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n"
            "packages: [papirus-icon-theme, papirus-icon-theme, xfce4-whiskermenu-plugin]\n"
        )
        loaded = theme.load_theme(self.paths, "test")
        self.assertEqual(loaded.packages, ("papirus-icon-theme", "xfce4-whiskermenu-plugin"))

    def test_an_invalid_package_name_is_rejected(self):
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\npackages: ['../evil']\n"
        )
        with self.assertRaisesRegex(theme.ThemeError, "invalid package name"):
            theme.load_theme(self.paths, "test")

    def test_apply_refuses_when_a_package_is_missing(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\npackages: [papirus-icon-theme]\n"
        )
        with mock.patch.object(theme_session, "missing_packages", return_value=["papirus-icon-theme"]):
            with self.assertRaisesRegex(theme.ThemeError, "pkg install -y papirus-icon-theme"):
                theme.run_theme_apply(
                    self.paths,
                    "test",
                    out=io.StringIO(),
                    session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
                    runner=FakeRunner(),
                    matcher=FakeMatcher(),
                )


class PathDirectoryAssetTest(ThemeCase):
    def test_a_path_asset_may_point_at_a_directory(self):
        built = self.workspace.home / ".local/share/termux-desktop/builds/demo/Mine"
        (built / "gtk-3.0").mkdir(parents=True)
        (built / "gtk-3.0" / "gtk.css").write_text("/* css */", encoding="utf-8")
        manifest = self.write_theme(
            "theme: test\ncomponents:\n  gtk: Mine\n"
            "assets:\n  - kind: theme\n    target: Mine\n"
            f"    path: {built}\n"
        )
        asset = theme.load_theme(self.paths, "test").assets[0]
        source = theme_assets._local_source(asset, manifest)
        self.assertTrue(source.is_dir())

    def test_a_missing_build_artifact_hints_at_the_build_command(self):
        manifest = self.write_theme(
            "theme: test\ncomponents:\n  gtk: Mine\n"
            "assets:\n  - kind: theme\n    target: Mine\n"
            "    path: ~/.local/share/termux-desktop/builds/demo/Mine\n"
        )
        asset = theme.load_theme(self.paths, "test").assets[0]
        with self.assertRaisesRegex(theme.ThemeError, "desk skin build"):
            theme_assets._local_source(asset, manifest)
