"""Tests for the optional component recipes of a skin."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace

from termux_desk import optional
from termux_desk.optional import OptionalError
from termux_desk.settings.model import config_directory

try:
    import yaml  # noqa: F401

    HAS_YAML = True
except ImportError:  # pragma: no cover - host without PyYAML
    HAS_YAML = False

needs_yaml = unittest.skipUnless(HAS_YAML, "PyYAML is not installed")


class Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def recipe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@needs_yaml
class OptionalTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.out = io.StringIO()

    def user_recipe(self, body="packages: [eww]\n"):
        target = config_directory(self.paths, self.workspace.environ) / "optional" / "eww.yaml"
        recipe(target, body)
        return target

    def install(self, names, **kwargs):
        kwargs.setdefault("installed", lambda package: False)
        kwargs.setdefault("runner", mock.Mock(return_value=Completed()))
        kwargs.setdefault("environ", self.workspace.environ)
        return optional.install(self.paths, names, out=self.out, **kwargs)

    def test_a_missing_recipe_is_skipped(self):
        count = self.install(("nosuch",))
        self.assertEqual(count, 0)
        self.assertIn("nosuch: no recipe, skipped", self.out.getvalue())

    def test_an_installed_component_is_left_alone(self):
        self.user_recipe()
        runner = mock.Mock(return_value=Completed())
        count = self.install(("eww",), installed=lambda package: True, runner=runner)
        self.assertEqual(count, 1)
        runner.assert_not_called()
        self.assertIn("already installed", self.out.getvalue())

    def test_a_missing_package_is_installed_then_the_commands_run(self):
        self.user_recipe("packages: [eww]\ncommands:\n  - mkdir -p ~/.config/eww\n")
        runner = mock.Mock(return_value=Completed())
        count = self.install(("eww",), runner=runner)
        self.assertEqual(count, 1)
        calls = [call.args[0] for call in runner.call_args_list]
        self.assertEqual(calls[0], ["pkg", "install", "-y", "eww"])
        self.assertEqual(calls[1], ["sh", "-c", "mkdir -p ~/.config/eww"])
        self.assertIn("optional eww: installed", self.out.getvalue())

    def test_a_failed_install_is_reported_and_skipped(self):
        self.user_recipe()
        runner = mock.Mock(return_value=Completed(returncode=1, stderr="E: no such package"))
        count = self.install(("eww",), runner=runner)
        self.assertEqual(count, 0)
        self.assertIn("could not be installed, skipped", self.out.getvalue())

    def test_a_failed_command_is_reported_and_skipped(self):
        self.user_recipe("packages: [eww]\ncommands:\n  - exit 2\n")
        runner = mock.Mock(
            side_effect=[Completed(), Completed(returncode=2, stderr="nope")]
        )
        count = self.install(("eww",), runner=runner)
        self.assertEqual(count, 0)
        self.assertIn("command failed, skipped", self.out.getvalue())

    def test_a_dry_run_only_reports(self):
        self.user_recipe()
        runner = mock.Mock(return_value=Completed())
        count = self.install(("eww",), runner=runner, dry_run=True)
        self.assertEqual(count, 1)
        runner.assert_not_called()
        self.assertIn("would install eww", self.out.getvalue())

    def test_a_malformed_recipe_is_rejected(self):
        self.user_recipe("packages: eww\n")
        with self.assertRaises(OptionalError):
            optional.load_recipe(self.paths, "eww", self.workspace.environ)

    def test_the_builtin_recipes_are_readable(self):
        for name in ("eww", "kvantum"):
            loaded = optional.load_recipe(self.paths, name, self.workspace.environ)
            self.assertIsNotNone(loaded, name)
            self.assertEqual(loaded.packages, (name,))


if __name__ == "__main__":
    unittest.main()
