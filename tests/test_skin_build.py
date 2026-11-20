"""Tests for the experimental build recipes."""

from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import Workspace

from termux_desk import skin_build

try:
    import yaml  # noqa: F401
except ImportError:  # pragma: no cover
    yaml = None


def needs_yaml(case):
    return unittest.skipIf(yaml is None, "PyYAML is not installed")(case)


class FakeRunner:
    def __init__(self, rootfs: Path, artifact: Path) -> None:
        self.rootfs = rootfs
        self.artifact = artifact
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command):
        self.commands.append(tuple(command))
        if command[:2] == ("proot-distro", "list"):
            return subprocess.CompletedProcess(command, 0, "Installed containers:\n* ubuntu\n", "")
        if command[1] == "login":
            target = self.rootfs / self.artifact
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("built", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "build output\n", "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected command")


@needs_yaml
class RecipeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "materia.yaml"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_a_valid_recipe_is_parsed(self):
        self.write(
            "build: materia\ncontainer: ubuntu\n"
            "source:\n  url: https://example.invalid/src.tar.gz\n  sha256: "
            + "a" * 64 + "\n"
            "packages: [meson, ninja-build]\ncommands: [meson setup build]\n"
            "artifacts:\n  - path: build/Materia\n    kind: theme\n    target: Materia\n"
        )
        recipe = skin_build.load_recipe(self.path)
        self.assertEqual(recipe.name, "materia")
        self.assertEqual(recipe.packages, ("meson", "ninja-build"))
        self.assertEqual(recipe.artifacts[0].target, "Materia")

    def test_a_plain_http_source_is_refused(self):
        self.write(
            "build: x\nsource:\n  url: http://example.invalid/x.tar.gz\n"
            'commands: ["true"]\nartifacts:\n  - path: x\n    kind: theme\n    target: X\n'
        )
        with self.assertRaisesRegex(skin_build.BuildError, "HTTPS"):
            skin_build.load_recipe(self.path)

    def test_an_unknown_artifact_kind_is_refused(self):
        self.write(
            "build: x\nsource:\n  url: https://example.invalid/x.tar.gz\n"
            'commands: ["true"]\nartifacts:\n  - path: x\n    kind: wallpaper\n    target: X\n'
        )
        with self.assertRaisesRegex(skin_build.BuildError, "unsupported artifact kind"):
            skin_build.load_recipe(self.path)

    def test_a_recipe_without_commands_is_refused(self):
        self.write(
            "build: x\nsource:\n  url: https://example.invalid/x.tar.gz\n"
            "artifacts:\n  - path: x\n    kind: theme\n    target: X\n"
        )
        with self.assertRaisesRegex(skin_build.BuildError, "commands"):
            skin_build.load_recipe(self.path)


@needs_yaml
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.recipe = Path(self.temporary.name) / "materia.yaml"
        self.recipe.write_text(
            "build: materia\nsource:\n  url: https://example.invalid/src.tar.gz\n"
            "commands: [meson setup build]\n"
            "artifacts:\n  - path: build/Materia\n    kind: theme\n    target: Materia\n",
            encoding="utf-8",
        )

    def rootfs(self) -> Path:
        return self.paths.prefix / "var" / "lib" / "proot-distro" / "containers" / "ubuntu" / "rootfs"

    def test_building_requires_yes(self):
        out = io.StringIO()
        self.assertEqual(skin_build.run_skin_build(self.paths, self.recipe, out=out), 1)
        self.assertIn("--yes", out.getvalue())

    def test_a_recipe_is_run_inside_the_container_and_copied_out(self):
        runner = FakeRunner(self.rootfs(), Path("build/Materia"))
        out = io.StringIO()
        code = skin_build.run_skin_build(
            self.paths,
            self.recipe,
            yes=True,
            out=out,
            fetch=lambda url, digest: b"archive",
            runner=runner,
        )
        self.assertEqual(code, 0)
        login = [command for command in runner.commands if command[1] == "login"][0]
        script = login[-1]
        self.assertIn("cd /home/termux/desk-build/materia", script)
        self.assertIn("meson setup build", script)
        built = self.paths.xdg_data_home / "termux-desktop" / "builds" / "materia" / "Materia"
        self.assertTrue(built.is_file())
        self.assertIn("path:", out.getvalue())
