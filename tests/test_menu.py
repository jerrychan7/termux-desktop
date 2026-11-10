"""Tests for scanning container applications and generating menu entries."""

from __future__ import annotations

import base64
import io
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from support import RecordingRunner, Workspace, make_result
from termux_desk import menu
from termux_desk.config import load_paths
from termux_desk.container import PROOT_DISTRO_PROGRAM
from termux_desk.menu import (
    MenuError,
    collect_apps,
    command_from_exec,
    icon_candidates,
    icon_scan_command,
    is_menu_app,
    parse_icon_output,
    parse_desktop_entry,
    quote_exec_argument,
    refresh_for_session,
    render_entry,
    run_refresh,
    split_desktop_output,
)

XTERM = """[Desktop Entry]
Type=Application
Name=XTerm
Exec=xterm %u
Icon=mini.xterm
Terminal=false
"""

TERMINAL_APP = """[Desktop Entry]
Type=Application
Name=nano
Exec=nano %F
Terminal=true
"""

HIDDEN_APP = """[Desktop Entry]
Type=Application
Name=Hidden Tool
Exec=tool
NoDisplay=true
"""

LINK_ENTRY = """[Desktop Entry]
Type=Link
Name=Web Site
URL=https://example.com
"""

FOREIGN_GROUP = """[Desktop Entry]
Type=Application
Name=Real Name
Exec=xterm
[Desktop Action new-window]
Name=New Window
Exec=xterm -e
"""


class FakeMenu:
    """Answer proot-distro list, the per-container scan, and the icon scan."""

    def __init__(self, containers, files, icons=None):
        self.containers = list(containers)
        self.files = files
        self.icons = icons or {}
        self.runner = RecordingRunner(self.handle)

    def handle(self, command, **kwargs):
        parts = list(command[1:])
        if parts[0] == "list":
            names = "".join(f"  * {name}\n" for name in self.containers)
            return make_result(
                command,
                stderr=f"\nInstalled containers:\n\n{names}\nLog in with: x\n",
            )
        inside = parts[parts.index("--") + 1 :]
        if inside[:2] != ["sh", "-c"]:
            raise AssertionError(f"unexpected command: {command}")
        container = parts[1]
        script = inside[2]
        icon = re.search(r"# desk icon (\S+)", script)
        if icon is not None:
            found = self.icons.get(container, {}).get(icon.group(1))
            if found is None:
                return make_result(command, stdout="")
            container_path, data = found
            encoded = base64.b64encode(data).decode("ascii")
            return make_result(
                command, stdout=f"\x1e{container_path}\x1e{encoded}\x1e\n"
            )
        text = "".join(
            f"\x1e{name}\x1e\n{content}\n"
            for name, content in self.files.get(container, {}).items()
        )
        return make_result(command, stdout=text)


class ParseTest(unittest.TestCase):
    def test_split_reads_each_file(self):
        text = "\x1ea.desktop\x1e\nName=A\n\x1eb.desktop\x1e\nName=B\n"
        self.assertEqual(
            split_desktop_output(text),
            (("a.desktop", "Name=A"), ("b.desktop", "Name=B")),
        )

    def test_only_the_desktop_entry_group_is_read(self):
        values = parse_desktop_entry(FOREIGN_GROUP)
        self.assertEqual(values["Name"], "Real Name")
        self.assertEqual(values["Exec"], "xterm")

    def test_field_codes_are_removed(self):
        self.assertEqual(command_from_exec("xterm %u"), ("xterm",))
        self.assertEqual(
            command_from_exec("app --url=%u --file=%f"), ("app", "--url=", "--file=")
        )

    def test_a_literal_percent_survives(self):
        self.assertEqual(command_from_exec("app 100%% %u"), ("app", "100%"))

    def test_quoted_arguments_are_kept(self):
        self.assertEqual(
            command_from_exec('app "two words" --flag'), ("app", "two words", "--flag")
        )

    def test_terminal_and_hidden_entries_are_skipped(self):
        self.assertTrue(is_menu_app(parse_desktop_entry(XTERM)))
        self.assertFalse(is_menu_app(parse_desktop_entry(TERMINAL_APP)))
        self.assertFalse(is_menu_app(parse_desktop_entry(HIDDEN_APP)))
        self.assertFalse(is_menu_app(parse_desktop_entry(LINK_ENTRY)))

    def test_collect_apps_keeps_the_valid_entry(self):
        text = (
            "\x1exterm.desktop\x1e\n" + XTERM + "\n"
            "\x1enano.desktop\x1e\n" + TERMINAL_APP + "\n"
            "\x1elink.desktop\x1e\n" + LINK_ENTRY + "\n"
        )
        apps = collect_apps("ubuntu-desktop-test", text)
        self.assertEqual([app.app_id for app in apps], ["xterm"])
        self.assertEqual(apps[0].name, "XTerm")
        self.assertEqual(apps[0].command, ("xterm",))
        self.assertEqual(apps[0].icon, "mini.xterm")
        self.assertEqual(apps[0].entry_name, "desk-ubuntu-desktop-test-xterm.desktop")

    def test_path_icons_are_kept_for_copying(self):
        content = "[Desktop Entry]\nType=Application\nName=App\nExec=app\nIcon=/usr/share/icons/app.png\n"
        apps = collect_apps("debian", "\x1eapp.desktop\x1e\n" + content)
        self.assertEqual(apps[0].icon, "/usr/share/icons/app.png")

    def test_exec_is_quoted_for_the_desktop_entry(self):
        self.assertEqual(quote_exec_argument("plain"), "plain")
        self.assertEqual(quote_exec_argument("two words"), '"two words"')
        self.assertEqual(quote_exec_argument("100%"), "100%%")
        self.assertEqual(quote_exec_argument('say "hi"'), '"say \\"hi\\""')

    def test_rendered_entry_names_the_container(self):
        apps = collect_apps("ubuntu-desktop-test", "\x1exterm.desktop\x1e\n" + XTERM)
        text = render_entry(apps[0], "/data/desk")
        self.assertIn("Name=XTerm (ubuntu-desktop-test)", text)
        self.assertIn(
            "Exec=/data/desk app launch ubuntu-desktop-test/xterm -- xterm", text
        )
        self.assertIn("X-Desk-Container=ubuntu-desktop-test", text)
        self.assertIn("Icon=mini.xterm", text)
        self.assertIn("Terminal=false", text)



class IconTest(unittest.TestCase):
    def test_name_lookup_starts_with_the_largest_hicolor_icon(self):
        candidates = icon_candidates("xterm")
        self.assertEqual(
            candidates[0], "/usr/share/icons/hicolor/128x128/apps/xterm.png"
        )
        self.assertIn("/usr/share/pixmaps/xterm.png", candidates)
        self.assertEqual(len(candidates), 18)

    def test_absolute_icon_is_used_as_it_is(self):
        self.assertEqual(icon_candidates("/opt/app.png"), ("/opt/app.png",))

    def test_absolute_icon_without_a_suffix_tries_every_suffix(self):
        self.assertEqual(
            icon_candidates("/opt/app"),
            ("/opt/app.png", "/opt/app.svg", "/opt/app.xpm"),
        )

    def test_odd_icon_name_is_ignored(self):
        self.assertEqual(icon_candidates("two words"), ())

    def test_scan_command_carries_the_candidates_and_the_marker(self):
        command = icon_scan_command("/p/proot-distro", "ubuntu", "xterm")
        script = command[-1]
        self.assertTrue(script.startswith("# desk icon xterm"))
        self.assertIn("/usr/share/icons/hicolor/48x48/apps/xterm.png", script)
        self.assertIn("base64", script)

    def test_parse_reads_the_path_and_the_bytes(self):
        text = "\x1e/usr/share/pixmaps/x.png\x1eaGVsbG8=\x1e\n"
        self.assertEqual(
            parse_icon_output(text), ("/usr/share/pixmaps/x.png", b"hello")
        )

    def test_parse_returns_none_for_an_empty_result(self):
        self.assertIsNone(parse_icon_output(""))

    def test_rendered_entry_uses_the_copied_file(self):
        apps = collect_apps("ubuntu", "\x1exterm.desktop\x1e\n" + XTERM)
        app = replace(
            apps[0],
            icon_file=Path(
                "/home/termux/.local/share/icons/termux-desktop/ubuntu/mini.xterm.png"
            ),
        )
        text = render_entry(app, "/data/desk")
        self.assertIn(
            "Icon=/home/termux/.local/share/icons/termux-desktop/ubuntu/mini.xterm.png",
            text,
        )


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)

    def refresh(self, fake):
        output = io.StringIO()
        run_refresh(
            self.workspace.paths,
            runner=fake.runner,
            environ=self.workspace.environ,
            out=output,
        )
        return output.getvalue()

    def test_entries_are_written_for_every_container(self):
        fake = FakeMenu(
            ["ubuntu", "ubuntu-desktop-test"],
            {
                "ubuntu": {"xterm.desktop": XTERM},
                "ubuntu-desktop-test": {"xterm.desktop": XTERM},
            },
        )
        output = self.refresh(fake)
        menu_dir = self.workspace.paths.menu_dir
        self.assertEqual(
            sorted(path.name for path in menu_dir.glob("desk-*.desktop")),
            ["desk-ubuntu-desktop-test-xterm.desktop", "desk-ubuntu-xterm.desktop"],
        )
        first = (menu_dir / "desk-ubuntu-xterm.desktop").read_text(encoding="utf-8")
        self.assertIn("Name=XTerm (ubuntu)", first)
        self.assertIn("2 entries", output)

    def test_entries_that_no_longer_match_are_removed(self):
        menu_dir = self.workspace.paths.menu_dir
        menu_dir.mkdir(parents=True)
        stale = menu_dir / "desk-gone-app.desktop"
        stale.write_text("[Desktop Entry]\n", encoding="utf-8")
        foreign = menu_dir / "hand-written.desktop"
        foreign.write_text("[Desktop Entry]\n", encoding="utf-8")
        fake = FakeMenu([], {})
        output = self.refresh(fake)
        self.assertFalse(stale.exists())
        self.assertTrue(foreign.exists())
        self.assertIn("Removed 1 entries", output)


    def test_refresh_copies_the_icon_and_points_the_entry_at_it(self):
        fake = FakeMenu(
            ["ubuntu"],
            {"ubuntu": {"xterm.desktop": XTERM}},
            {"ubuntu": {"mini.xterm": ("/usr/share/pixmaps/mini.xterm.png", b"\x89PNG")}},
        )
        output = self.refresh(fake)
        icon_file = self.workspace.paths.icon_root / "ubuntu" / "mini.xterm.png"
        self.assertEqual(icon_file.read_bytes(), b"\x89PNG")
        entry = (self.workspace.paths.menu_dir / "desk-ubuntu-xterm.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"Icon={icon_file}", entry)
        self.assertIn("Copied 1 of 1 icons.", output)

    def test_refresh_keeps_the_icon_name_when_the_file_is_missing(self):
        fake = FakeMenu(["ubuntu"], {"ubuntu": {"xterm.desktop": XTERM}})
        self.refresh(fake)
        entry = (self.workspace.paths.menu_dir / "desk-ubuntu-xterm.desktop").read_text(
            encoding="utf-8"
        )
        self.assertIn("Icon=mini.xterm", entry)

    def test_refresh_removes_icon_files_that_no_longer_match(self):
        icon_dir = self.workspace.paths.icon_root / "ubuntu"
        icon_dir.mkdir(parents=True)
        stale = icon_dir / "old.png"
        stale.write_bytes(b"old")
        fake = FakeMenu(["ubuntu"], {"ubuntu": {"xterm.desktop": XTERM}})
        self.refresh(fake)
        self.assertFalse(stale.exists())

    def test_refresh_removes_icon_directories_of_removed_containers(self):
        removed = self.workspace.paths.icon_root / "gone"
        removed.mkdir(parents=True)
        (removed / "app.png").write_bytes(b"old")
        fake = FakeMenu([], {})
        self.refresh(fake)
        self.assertFalse(removed.exists())

    def test_missing_proot_distro_is_reported(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        fake = FakeMenu([], {})
        with self.assertRaises(MenuError) as raised:
            self.refresh(fake)
        self.assertIn("proot-distro", str(raised.exception))

    def test_failed_scan_is_reported(self):
        def broken(command, **kwargs):
            if command[1] == "list":
                return make_result(command, stderr="\nInstalled containers:\n\n  * ubuntu\n")
            return make_result(command, returncode=1)

        with self.assertRaises(MenuError) as raised:
            run_refresh(
                self.workspace.paths,
                runner=RecordingRunner(broken),
                environ=self.workspace.environ,
                out=io.StringIO(),
            )
        self.assertIn("ubuntu", str(raised.exception))


class RefreshForSessionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.workspace.add_program(PROOT_DISTRO_PROGRAM)

    def refresh(self, runner=None):
        output = io.StringIO()
        refresh_for_session(
            self.workspace.paths,
            runner=runner,
            environ=self.workspace.environ,
            out=output,
        )
        return output.getvalue()

    def test_the_menu_is_written_without_a_report(self):
        fake = FakeMenu(["ubuntu"], {"ubuntu": {"xterm.desktop": XTERM}})
        output = self.refresh(fake.runner)
        self.assertEqual(output, "")
        entry = self.workspace.paths.menu_dir / "desk-ubuntu-xterm.desktop"
        self.assertTrue(entry.is_file())

    def test_a_missing_proot_distro_is_skipped_silently(self):
        self.workspace.paths.prefix.joinpath("bin", PROOT_DISTRO_PROGRAM).unlink()
        self.assertEqual(self.refresh(), "")

    def test_a_failed_refresh_is_a_warning(self):
        failure = MenuError("the menu could not be written")
        with mock.patch.object(menu, "run_refresh", side_effect=failure):
            output = self.refresh()
        self.assertEqual(
            output,
            "the menu was not refreshed: the menu could not be written\n",
        )


if __name__ == "__main__":
    unittest.main()
