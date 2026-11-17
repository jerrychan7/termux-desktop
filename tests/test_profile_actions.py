"""Tests for the profile apply/reset composition."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace

from termux_desk import profile_actions
from termux_desk.config import DeskError
from termux_desk.layout import LayoutError


class _Settings:
    def __init__(self, theme=None, layout=None):
        self.theme = theme
        self.layout = layout


class ProfileApplyTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = Workspace(Path(self.temporary.name)).paths
        self.output = io.StringIO()

    def test_applies_theme_before_layout(self):
        calls = []
        with mock.patch.object(
            profile_actions.settings, "load_settings", return_value=_Settings("stock", "compact")
        ), mock.patch.object(
            profile_actions.theme_tools, "run_theme_apply", side_effect=lambda *a, **k: calls.append("theme") or 0
        ), mock.patch.object(
            profile_actions.layout_tools, "run_layout_apply", side_effect=lambda *a, **k: calls.append("layout") or 0
        ):
            code = profile_actions.run_profile_apply(self.paths, out=self.output)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["theme", "layout"])
        self.assertIn("Applied the profile's theme 'stock' and layout 'compact'", self.output.getvalue())

    def test_nothing_selected_does_nothing(self):
        with mock.patch.object(
            profile_actions.settings, "load_settings", return_value=_Settings(None, None)
        ), mock.patch.object(profile_actions.theme_tools, "run_theme_apply") as theme_apply, mock.patch.object(
            profile_actions.layout_tools, "run_layout_apply"
        ) as layout_apply:
            code = profile_actions.run_profile_apply(self.paths, out=self.output)
        self.assertEqual(code, 0)
        theme_apply.assert_not_called()
        layout_apply.assert_not_called()
        self.assertIn("nothing to apply", self.output.getvalue())

    def test_dry_run_validates_without_applying(self):
        with mock.patch.object(
            profile_actions.settings, "load_settings", return_value=_Settings("stock", "compact")
        ), mock.patch.object(profile_actions.theme_tools, "load_theme") as load_theme, mock.patch.object(
            profile_actions.layout_tools, "load_layout"
        ) as load_layout, mock.patch.object(profile_actions.theme_tools, "run_theme_apply") as theme_apply, mock.patch.object(
            profile_actions.layout_tools, "run_layout_apply"
        ) as layout_apply:
            code = profile_actions.run_profile_apply(self.paths, dry_run=True, out=self.output)
        self.assertEqual(code, 0)
        load_theme.assert_called_once()
        load_layout.assert_called_once()
        theme_apply.assert_not_called()
        layout_apply.assert_not_called()

    def test_layout_failure_rolls_back_theme(self):
        error = LayoutError("bad layout")
        with mock.patch.object(
            profile_actions.settings, "load_settings", return_value=_Settings("stock", "compact")
        ), mock.patch.object(profile_actions.theme_tools, "run_theme_apply"), mock.patch.object(
            profile_actions.theme_tools, "run_theme_reset"
        ) as reset, mock.patch.object(
            profile_actions.layout_tools, "run_layout_apply", side_effect=error
        ):
            with self.assertRaises(LayoutError):
                profile_actions.run_profile_apply(self.paths, out=self.output)
        reset.assert_called_once()

    def test_failed_rollback_is_reported(self):
        with mock.patch.object(
            profile_actions.settings, "load_settings", return_value=_Settings("stock", "compact")
        ), mock.patch.object(profile_actions.theme_tools, "run_theme_apply"), mock.patch.object(
            profile_actions.theme_tools, "run_theme_reset", side_effect=DeskError("cannot reset")
        ), mock.patch.object(
            profile_actions.layout_tools, "run_layout_apply", side_effect=LayoutError("bad layout")
        ):
            with self.assertRaises(DeskError) as caught:
                profile_actions.run_profile_apply(self.paths, out=self.output)
        self.assertIn("could not be rolled back", str(caught.exception))


class ProfileResetTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = Workspace(Path(self.temporary.name)).paths
        self.output = io.StringIO()

    def test_resets_layout_then_theme(self):
        calls = []
        with mock.patch.object(
            profile_actions.layout_tools, "run_layout_reset", side_effect=lambda *a, **k: calls.append("layout") or 0
        ), mock.patch.object(
            profile_actions.theme_tools, "run_theme_reset", side_effect=lambda *a, **k: calls.append("theme") or 0
        ):
            code = profile_actions.run_profile_reset(self.paths, out=self.output)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["layout", "theme"])
        self.assertIn("theme and layout were reset", self.output.getvalue())

    def test_one_failure_does_not_skip_the_other_layer(self):
        with mock.patch.object(
            profile_actions.layout_tools, "run_layout_reset", side_effect=LayoutError("layout bad")
        ), mock.patch.object(profile_actions.theme_tools, "run_theme_reset") as theme_reset:
            with self.assertRaises(DeskError):
                profile_actions.run_profile_reset(self.paths, out=self.output)
        theme_reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
