"""Tests for scanning container applications and generating menu entries."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from support import RecordingRunner, Workspace, make_result
from termux_desk.config import load_paths
from termux_desk.container import PROOT_DISTRO_PROGRAM
from termux_desk.menu import (
    MenuError,
    collect_apps,
    command_from_exec,
    is_menu_app,
    parse_desktop_entry,
    quote_exec_argument,
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
    """Answer proot-distro list and the per-container scan."""

    def __init__(self, containers, files):
        self.containers = list(containers)
        self.files = files
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

    def test_path_icons_are_dropped(self):
        content = "[Desktop Entry]\nType=Application\nName=App\nExec=app\nIcon=/usr/share/icons/app.png\n"
        apps = collect_apps("debian", "\x1eapp.desktop\x1e\n" + content)
        self.assertIsNone(apps[0].icon)

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


if __name__ == "__main__":
    unittest.main()
