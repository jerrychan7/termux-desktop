"""Tests for installing, updating, and removing the desk command.

Every test writes into a temporary HOME and PREFIX. Network steps are
answered by plain functions, so no test reaches GitHub.
"""

from __future__ import annotations

import contextlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import Workspace, make_state
from termux_desk import cli
from termux_desk.install import (
    InstallError,
    extract_archive,
    find_source_root,
    parse_version,
    read_installed,
    run_install,
    run_uninstall,
    run_update,
    tag_for_version,
    tag_from_location,
    version_text,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_DESK = "#!/bin/sh\nexec python3 -m termux_desk \"$@\"\n"


def make_fake_source(root: Path, version: str = "0.0.1") -> Path:
    """Write a directory with the layout that install expects."""
    source = root / "checkout"
    package = source / "src" / "termux_desk"
    package.mkdir(parents=True)
    (source / "templates").mkdir()
    (source / "desk").write_text(CHECKOUT_DESK, encoding="utf-8")
    (source / "desk").chmod(0o755)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (package / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    stale = package / "__pycache__"
    stale.mkdir()
    (stale / "cli.cpython-314.pyc").write_bytes(b"stale")
    (source / "templates" / "session.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return source


def make_tarball(path: Path, members: list[tuple[str, bytes, int]]) -> Path:
    """Write a tar.gz file from (name, data, type) middle entries."""
    with tarfile.open(path, "w:gz") as handle:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "/etc/passwd"
                handle.addfile(info)
                continue
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return path


class VersionTextTest(unittest.TestCase):
    def test_plain_version_is_accepted(self):
        self.assertEqual(parse_version("0.0.1"), (0, 0, 1))

    def test_tag_with_leading_v_is_accepted(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))

    def test_version_with_two_parts_is_rejected(self):
        with self.assertRaises(InstallError):
            parse_version("0.1")

    def test_version_with_a_suffix_is_rejected(self):
        with self.assertRaises(InstallError):
            parse_version("0.1.0-beta")

    def test_version_text_drops_the_leading_v(self):
        self.assertEqual(version_text("v0.0.1"), "0.0.1")

    def test_tag_is_a_version_with_a_leading_v(self):
        self.assertEqual(tag_for_version("0.0.1"), "v0.0.1")

    def test_tag_from_location_reads_the_last_path_part(self):
        location = "https://github.com/jerrychan7/termux-desktop/releases/tag/v0.0.1"
        self.assertEqual(tag_from_location(location), "v0.0.1")

    def test_tag_from_location_rejects_other_addresses(self):
        with self.assertRaises(InstallError):
            tag_from_location("https://github.com/jerrychan7/termux-desktop")


class SourceRootTest(unittest.TestCase):
    def test_repository_root_is_a_source_root(self):
        self.assertEqual(find_source_root(REPOSITORY_ROOT), REPOSITORY_ROOT)

    def test_release_archive_layout_is_searched_one_level_down(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = make_fake_source(Path(temporary))
            self.assertEqual(find_source_root(Path(temporary)), source)

    def test_missing_sources_are_reported_with_the_expected_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(InstallError) as raised:
                find_source_root(Path(temporary))
        self.assertIn("src/termux_desk/cli.py", str(raised.exception))

    def test_missing_directory_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(InstallError):
                find_source_root(Path(temporary) / "nowhere")


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.source = make_fake_source(Path(self.temporary.name))

    def test_install_copies_package_template_and_launcher(self):
        out = io.StringIO()
        run_install(self.paths, self.source, out=out)
        self.assertEqual(
            (self.paths.install_root / "src" / "termux_desk" / "cli.py").is_file(), True
        )
        self.assertEqual(
            (self.paths.install_root / "templates" / "session.sh").read_text(),
            "#!/bin/sh\n",
        )
        self.assertFalse(
            (self.paths.install_root / "src" / "termux_desk" / "__pycache__").exists()
        )
        self.assertEqual(self.paths.launcher.read_text(), CHECKOUT_DESK)
        self.assertTrue(self.paths.launcher.stat().st_mode & 0o100)
        self.assertIn("installed desk 0.0.1", out.getvalue())

    def test_install_writes_the_version_file(self):
        run_install(self.paths, self.source, out=io.StringIO())
        self.assertEqual(
            self.paths.version_file.read_text(),
            "version=0.0.1\nsource=checkout\n",
        )
        installed = read_installed(self.paths)
        self.assertEqual(installed.version, "0.0.1")
        self.assertFalse(installed.from_release)

    def test_install_with_a_release_tag_records_the_release(self):
        run_install(self.paths, self.source, release="v0.0.1", out=io.StringIO())
        installed = read_installed(self.paths)
        self.assertEqual(installed.version, "0.0.1")
        self.assertEqual(installed.source, "release:v0.0.1")

    def test_install_rejects_a_release_tag_of_another_version(self):
        with self.assertRaises(InstallError) as raised:
            run_install(self.paths, self.source, release="v0.0.2", out=io.StringIO())
        self.assertIn("0.0.1", str(raised.exception))

    def test_second_install_keeps_the_previous_copy(self):
        run_install(self.paths, self.source, out=io.StringIO())
        updated = make_fake_source(Path(self.temporary.name) / "second", version="0.0.2")
        run_install(self.paths, updated, out=io.StringIO())
        self.assertEqual(
            (self.paths.install_previous / "VERSION").read_text(),
            "version=0.0.1\nsource=checkout\n",
        )
        self.assertEqual(
            (self.paths.install_root / "src" / "termux_desk" / "__init__.py").read_text(),
            '__version__ = "0.0.2"\n',
        )

    def test_foreign_launcher_is_refused(self):
        self.paths.launcher.write_text("#!/bin/sh\necho other desk\n", encoding="utf-8")
        with self.assertRaises(InstallError) as raised:
            run_install(self.paths, self.source, out=io.StringIO())
        self.assertIn("--force", str(raised.exception))

    def test_foreign_launcher_is_replaced_with_force(self):
        self.paths.launcher.write_text("#!/bin/sh\necho other desk\n", encoding="utf-8")
        run_install(self.paths, self.source, out=io.StringIO(), force=True)
        self.assertEqual(self.paths.launcher.read_text(), CHECKOUT_DESK)

    def test_missing_source_directory_is_reported(self):
        with self.assertRaises(InstallError):
            run_install(self.paths, Path(self.temporary.name) / "nowhere", out=io.StringIO())


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.source = make_fake_source(Path(self.temporary.name))
        run_install(self.paths, self.source, out=io.StringIO())

    def fake_release(self, version: str):
        def fetch(tag: str, workdir: Path) -> Path:
            return make_fake_source(workdir, version=version)

        return fetch

    def test_update_replaces_a_checkout_install_only_with_force(self):
        with self.assertRaises(InstallError) as raised:
            run_update(
                self.paths,
                out=io.StringIO(),
                resolve_latest=lambda: "v0.0.2",
                fetch_release=self.fake_release("0.0.2"),
            )
        self.assertIn("installed from a checkout", str(raised.exception))

    def test_update_with_force_records_the_release(self):
        out = io.StringIO()
        run_update(
            self.paths,
            out=out,
            force=True,
            resolve_latest=lambda: "v0.0.2",
            fetch_release=self.fake_release("0.0.2"),
        )
        installed = read_installed(self.paths)
        self.assertEqual(installed.version, "0.0.2")
        self.assertEqual(installed.source, "release:v0.0.2")
        self.assertEqual(
            (self.paths.install_previous / "VERSION").read_text(),
            "version=0.0.1\nsource=checkout\n",
        )
        self.assertIn("installed desk 0.0.2", out.getvalue())

    def test_update_accepts_a_requested_version(self):
        run_update(
            self.paths,
            version="0.0.2",
            force=True,
            out=io.StringIO(),
            fetch_release=self.fake_release("0.0.2"),
        )
        self.assertEqual(read_installed(self.paths).source, "release:v0.0.2")

    def test_update_rejects_an_archive_with_another_version(self):
        with self.assertRaises(InstallError) as raised:
            run_update(
                self.paths,
                force=True,
                out=io.StringIO(),
                resolve_latest=lambda: "v0.0.2",
                fetch_release=self.fake_release("0.0.9"),
            )
        self.assertIn("0.0.9", str(raised.exception))

    def test_check_prints_both_versions_and_changes_nothing(self):
        out = io.StringIO()
        run_update(
            self.paths,
            check=True,
            out=out,
            resolve_latest=lambda: "v0.0.2",
        )
        self.assertIn("installed: 0.0.1 (checkout)", out.getvalue())
        self.assertIn("available: 0.0.2", out.getvalue())
        self.assertEqual(read_installed(self.paths).version, "0.0.1")

    def test_same_version_is_not_installed_again(self):
        run_update(
            self.paths,
            out=io.StringIO(),
            force=True,
            resolve_latest=lambda: "v0.0.1",
            fetch_release=self.fake_release("0.0.1"),
        )
        out = io.StringIO()
        run_update(self.paths, out=out, resolve_latest=lambda: "v0.0.1")
        self.assertIn("already installed", out.getvalue())

    def test_update_without_an_install_is_reported(self):
        with tempfile.TemporaryDirectory() as other:
            paths = Workspace(Path(other)).paths
            with self.assertRaises(InstallError) as raised:
                run_update(paths, out=io.StringIO(), resolve_latest=lambda: "v0.0.2")
        self.assertIn("not installed", str(raised.exception))


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        self.paths = self.workspace.paths
        self.source = make_fake_source(Path(self.temporary.name))
        run_install(self.paths, self.source, out=io.StringIO())
        self.menu_entry = self.paths.menu_dir / "desk-ubuntu-xterm.desktop"
        self.menu_entry.parent.mkdir(parents=True, exist_ok=True)
        self.menu_entry.write_text("[Desktop Entry]\n", encoding="utf-8")
        self.other_entry = self.paths.menu_dir / "firefox.desktop"
        self.other_entry.write_text("[Desktop Entry]\n", encoding="utf-8")

    def test_uninstall_removes_the_command_and_keeps_state(self):
        out = io.StringIO()
        run_uninstall(self.paths, out=out)
        self.assertFalse(self.paths.install_root.exists())
        self.assertFalse(self.paths.launcher.exists())
        self.assertTrue(self.menu_entry.exists())
        self.assertIn("removed desk", out.getvalue())

    def test_uninstall_with_purge_removes_state_and_generated_entries(self):
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.state_dir / "desk.log").write_text("", encoding="utf-8")
        run_uninstall(self.paths, purge=True, out=io.StringIO())
        self.assertFalse(self.paths.state_dir.exists())
        self.assertFalse(self.menu_entry.exists())
        self.assertTrue(self.other_entry.exists())

    def test_purge_refuses_while_a_session_is_recorded(self):
        self.workspace.write_state(make_state())
        with self.assertRaises(InstallError) as raised:
            run_uninstall(self.paths, purge=True, out=io.StringIO())
        self.assertIn("desk stop", str(raised.exception))
        self.assertTrue(self.paths.install_root.exists())

    def test_foreign_launcher_is_kept(self):
        self.paths.launcher.write_text("#!/bin/sh\necho other desk\n", encoding="utf-8")
        out = io.StringIO()
        run_uninstall(self.paths, out=out)
        self.assertTrue(self.paths.launcher.exists())
        self.assertIn("kept", out.getvalue())

    def test_uninstall_without_an_install_is_reported(self):
        run_uninstall(self.paths, out=io.StringIO())
        out = io.StringIO()
        run_uninstall(self.paths, out=out)
        self.assertIn("not installed", out.getvalue())


class ArchiveTest(unittest.TestCase):
    def test_plain_archive_is_extracted(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_tarball(
                Path(temporary) / "release.tar.gz",
                [("termux-desktop-0.0.1/readme", b"text", tarfile.REGTYPE)],
            )
            destination = Path(temporary) / "out"
            extract_archive(archive, destination)
            self.assertEqual(
                (destination / "termux-desktop-0.0.1" / "readme").read_text(), "text"
            )

    def test_path_outside_the_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_tarball(
                Path(temporary) / "release.tar.gz",
                [("../evil", b"text", tarfile.REGTYPE)],
            )
            with self.assertRaises(InstallError):
                extract_archive(archive, Path(temporary) / "out")

    def test_link_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_tarball(
                Path(temporary) / "release.tar.gz",
                [("link", b"", tarfile.SYMTYPE)],
            )
            with self.assertRaises(InstallError):
                extract_archive(archive, Path(temporary) / "out")


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Workspace(Path(self.temporary.name))
        patched = mock.patch.dict(os.environ, self.workspace.environ, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def test_pipeline_version_flag_prints_the_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("desk 0.0.1", output.getvalue())

    def test_version_command_reports_an_uninstalled_checkout(self):
        out = io.StringIO()
        self.assertEqual(cli.main(["version"], out=out), 0)
        self.assertIn("desk 0.0.1", out.getvalue())
        self.assertIn("not installed", out.getvalue())

    def test_install_command_installs_the_checkout(self):
        out = io.StringIO()
        self.assertEqual(cli.main(["install", "--source", str(REPOSITORY_ROOT)], out=out), 0)
        self.assertTrue(self.workspace.paths.launcher.exists())
        self.assertIn("version=0.0.1", self.workspace.paths.version_file.read_text())

    def test_install_command_records_a_release_tag(self):
        out = io.StringIO()
        self.assertEqual(
            cli.main(
                ["install", "--source", str(REPOSITORY_ROOT), "--release", "v0.0.1"],
                out=out,
            ),
            0,
        )
        self.assertIn("source=release:v0.0.1", self.workspace.paths.version_file.read_text())

    def test_help_lists_the_install_commands(self):
        help_text = cli.build_parser().format_help()
        for command in ("install", "uninstall", "update", "version"):
            self.assertIn(command, help_text)


if __name__ == "__main__":
    unittest.main()
