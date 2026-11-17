"""Tests for the theme manifests, assets and the xfconf writer."""

from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from support import Workspace, make_result

from termux_desk import theme

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
        self.assertIn("applied theme test", output.getvalue())
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
        self.assertEqual(theme.read_state(self.paths).hashes["https://example.invalid/wall.png"], theme._sha256(b"asset"))

    def test_unknown_component_fails_before_writing(self):
        self.write_theme("theme: test\ncomponents:\n  gtk: NoSuchTheme\n")
        before = list(self.runner.calls)
        with self.assertRaisesRegex(theme.ThemeError, "missing"):
            theme.run_theme_apply(
                self.paths, "test", out=io.StringIO(), session=self.session, runner=self.runner, matcher=FakeMatcher(), fetch=self.fetch
            )
        self.assertEqual(self.runner.calls, before)


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
            "    sha256: " + theme._sha256(b"downloaded") + "\n"
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
            "    sha256: " + theme._sha256(b"expected") + "\n"
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

    def test_a_verified_download_is_reused_from_the_cache(self):
        self.add_icon_theme()
        self.write_theme(
            "theme: test\ncomponents:\n  gtk: Adwaita\n  xfwm: Default\n"
            "assets:\n  - kind: wallpaper\n    target: wall.png\n"
            "    url: https://example.invalid/wall.png\n"
            "    sha256: " + theme._sha256(b"downloaded") + "\n"
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


class ArchiveSafetyTest(ThemeCase):
    def test_unsafe_archive_member_is_rejected(self):
        archive = Path(self.temporary.name) / "bad.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            member = tarfile.TarInfo("../escape")
            member.size = 1
            handle.addfile(member, io.BytesIO(b"x"))
        with self.assertRaisesRegex(theme.ThemeError, "unsafe path"):
            theme._extract_archive(archive, Path(self.temporary.name) / "out", force=True)
