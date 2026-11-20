"""Cursor theme writes: XFCE reads Gtk/CursorThemeName, not Net/CursorThemeName."""

from __future__ import annotations

import io

from test_theme import FakeMatcher, FakeRunner, ThemeCase, needs_yaml

from termux_desk import theme


@needs_yaml
class CursorPropertyTest(ThemeCase):
    def test_a_cursor_theme_is_written_to_the_gtk_property(self):
        self.add_icon_theme()
        self.write_theme("theme: test\ncomponents:\n  gtk: Adwaita\n  cursors: Adwaita\n")
        runner = FakeRunner()
        theme.run_theme_apply(
            self.paths,
            "test",
            out=io.StringIO(),
            session={"DISPLAY": ":0", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/bus"},
            runner=runner,
            matcher=FakeMatcher(),
        )
        self.assertIn(
            ("xfconf-query", "-c", "xsettings", "-p", "/Gtk/CursorThemeName", "-s", "Adwaita"),
            runner.calls,
        )

