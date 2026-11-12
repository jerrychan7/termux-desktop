"""Install, update, and remove the desk command.

desk runs from a checkout before it is installed. Installing copies the
Python package and the session script template into
$PREFIX/lib/termux-desktop, copies the entry file to $PREFIX/bin/desk, and
records the copied version in a VERSION file next to the package.

Updating downloads the source archive of one release tag and replaces the
install directory with a single rename, so an update that fails before the
rename leaves the previous copy in place. The archive is a tar.gz file, so
updating needs neither git nor a package manager. Every network operation
raises an InstallError with the address it tried, because a network failure
is the most likely failure of an update.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from . import fonts
from .config import ConfigError, DeskError, Paths
from .menu import GENERATED_PREFIX

REPOSITORY = "jerrychan7/termux-desktop"
RELEASES_LATEST_URL = f"https://github.com/{REPOSITORY}/releases/latest"
ARCHIVE_URL = "https://github.com/{repository}/archive/refs/tags/{tag}.tar.gz"
NETWORK_TIMEOUT = 60

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

SOURCE_CHECKOUT = "checkout"
SOURCE_RELEASE = "release"

LAUNCHER_MARKER = "termux_desk"


class InstallError(DeskError):
    """A mistake or a failed step that desk reports without a traceback."""


@dataclass(frozen=True)
class InstalledRelease:
    """The version and the origin recorded by the last install."""

    version: str
    source: str

    @property
    def from_release(self) -> bool:
        """Return whether a release archive installed this copy."""
        return self.source.startswith(f"{SOURCE_RELEASE}:")


def parse_version(text: str) -> tuple[int, int, int]:
    """Return the three numbers of a version written as X.Y.Z."""
    match = VERSION_PATTERN.match(text.strip())
    if match is None:
        raise InstallError(f"{text!r} is not a version of the form X.Y.Z")
    return tuple(int(part) for part in match.groups())


def version_text(text: str) -> str:
    """Return a version without a leading v, for example 0.0.1."""
    return ".".join(str(part) for part in parse_version(text))


def tag_for_version(text: str) -> str:
    """Return the release tag of a version, for example v0.0.1."""
    return f"v{version_text(text)}"


def tag_from_location(location: str) -> str:
    """Return the tag at the end of a GitHub release address.

    GitHub answers a request for the latest release with a redirect whose
    Location header ends in /releases/tag/v0.0.1, so the tag needs no JSON
    parsing and the request does not use the API rate limit.
    """
    tag = location.rstrip("/").rsplit("/", 1)[-1]
    if not VERSION_PATTERN.match(tag):
        raise InstallError(f"{location} does not end in a release tag")
    return tag


def find_source_root(directory: Path) -> Path:
    """Return the directory that holds the desk sources.

    A release archive puts the sources in one subdirectory named after the
    repository and the tag, so the subdirectories are searched as well.
    """
    if not directory.is_dir():
        raise InstallError(f"{directory} is not a directory")
    candidates = [directory, *sorted(path for path in directory.iterdir() if path.is_dir())]
    for candidate in candidates:
        if _is_source_root(candidate):
            return candidate
    raise InstallError(
        f"{directory} does not contain the desk sources "
        "(desk, src/termux_desk/cli.py, and templates/session.sh)"
    )


def _is_source_root(directory: Path) -> bool:
    return (
        (directory / "desk").is_file()
        and (directory / "src" / "termux_desk" / "cli.py").is_file()
        and (directory / "templates" / "session.sh").is_file()
    )


def source_version(root: Path) -> str:
    """Return the __version__ value of the package below root."""
    init_file = root / "src" / "termux_desk" / "__init__.py"
    try:
        text = init_file.read_text(encoding="utf-8")
    except OSError as error:
        raise InstallError(f"could not read {init_file}: {error}") from error
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise InstallError(f"{init_file} has no __version__ value")
    return version_text(match.group(1))


def read_installed(paths: Paths) -> InstalledRelease | None:
    """Return the recorded version, or None when desk is not installed."""
    try:
        version_file = paths.version_file
    except ConfigError:
        return None
    try:
        text = version_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise InstallError(f"could not read {version_file}: {error}") from error
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    version = values.get("version", "")
    source = values.get("source", "")
    if not version or not source:
        raise InstallError(
            f"{paths.version_file} does not record a version and a source"
        )
    return InstalledRelease(version=version, source=source)


def write_version_file(directory: Path, *, version: str, source: str) -> Path:
    """Write the VERSION file of one install directory."""
    target = directory / "VERSION"
    target.write_text(f"version={version}\nsource={source}\n", encoding="utf-8")
    return target


def install_from_source(
    paths: Paths,
    source_root: Path,
    *,
    version: str,
    source: str,
    out: TextIO,
) -> None:
    """Copy the sources into the install directory and the launcher.

    The new copy is built in a staging directory and then renamed into
    place, so the install directory is never half written. The previous
    copy is kept as lib/termux-desktop.previous until the next successful
    install replaces it.
    """
    root = find_source_root(source_root)
    staging = paths.install_root.with_name(paths.install_root.name + ".new")
    _remove_tree(staging)
    paths.install_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        package_target = staging / "src" / "termux_desk"
        package_target.parent.mkdir(parents=True)
        shutil.copytree(
            root / "src" / "termux_desk",
            package_target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        templates_target = staging / "templates"
        templates_target.mkdir()
        shutil.copyfile(root / "templates" / "session.sh", templates_target / "session.sh")
        write_version_file(staging, version=version, source=source)
        _set_modes(staging)
        _replace_install_dir(paths, staging)
        paths.launcher.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / "desk", paths.launcher)
        paths.launcher.chmod(0o755)
    except OSError as error:
        _remove_tree(staging)
        raise InstallError(f"could not install into {paths.install_root}: {error}") from error
    except BaseException:
        _remove_tree(staging)
        raise
    print(f"installed desk {version} ({source})", file=out)
    print(f"  package  {paths.install_root}", file=out)
    print(f"  entry    {paths.launcher}", file=out)


def _replace_install_dir(paths: Paths, staging: Path) -> None:
    backup = paths.install_previous
    _remove_tree(backup)
    if paths.install_root.exists():
        os.replace(paths.install_root, backup)
    try:
        os.replace(staging, paths.install_root)
    except OSError:
        if not paths.install_root.exists() and backup.exists():
            os.replace(backup, paths.install_root)
        raise


def _set_modes(directory: Path) -> None:
    for path in [directory, *directory.rglob("*")]:
        if path.is_dir():
            path.chmod(0o755)
        else:
            path.chmod(0o644)


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _launcher_is_mine(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return LAUNCHER_MARKER in text


def check_launcher(paths: Paths, *, force: bool) -> None:
    """Refuse to overwrite an entry file that desk did not install."""
    if not paths.launcher.exists():
        return
    if force or _launcher_is_mine(paths.launcher):
        return
    raise InstallError(
        f"{paths.launcher} already exists and was not installed by desk; "
        "pass --force to replace it"
    )


def run_install(
    paths: Paths,
    source: Path,
    *,
    force: bool = False,
    release: str | None = None,
    out: TextIO,
) -> None:
    """Install desk from a directory that holds the sources.

    install.sh passes release when it downloaded a release archive, so the
    installed copy records that tag instead of checkout and desk update
    keeps working.
    """
    root = find_source_root(source)
    check_launcher(paths, force=force)
    version = source_version(root)
    if release is not None:
        expected = version_text(release)
        if version != expected:
            raise InstallError(
                f"{source} contains version {version}, not {expected}"
            )
        label = f"{SOURCE_RELEASE}:{tag_for_version(release)}"
    else:
        label = SOURCE_CHECKOUT
    install_from_source(paths, root, version=version, source=label, out=out)
    _print_refresh_hint(out)


def run_uninstall(paths: Paths, *, purge: bool = False, out: TextIO) -> None:
    """Remove the installed files and, with purge, what desk wrote in $HOME."""
    if purge and paths.session_file.exists():
        raise InstallError(
            "a session is recorded; run desk stop before uninstalling with --purge"
        )
    removed = False
    for directory in (paths.install_root, paths.install_previous):
        if directory.exists():
            _remove_tree(directory)
            removed = True
    if paths.launcher.exists():
        if _launcher_is_mine(paths.launcher):
            paths.launcher.unlink()
            removed = True
        else:
            print(
                f"kept {paths.launcher}: it was not installed by desk",
                file=out,
            )
    if purge:
        if paths.state_dir.exists():
            _remove_tree(paths.state_dir)
            print(f"removed state and logs in {paths.state_dir}", file=out)
        count = _remove_generated_entries(paths)
        if count:
            print(f"removed {count} generated menu entries in {paths.menu_dir}", file=out)
        fonts.remove_preference(paths, out=out)
    if not removed:
        print("desk is not installed.", file=out)
        return
    print(f"removed desk from {paths.install_root}", file=out)


def _remove_generated_entries(paths: Paths) -> int:
    if not paths.menu_dir.is_dir():
        return 0
    count = 0
    for entry in sorted(paths.menu_dir.glob(f"{GENERATED_PREFIX}*.desktop")):
        try:
            entry.unlink()
        except OSError:
            continue
        count += 1
    return count


def run_update(
    paths: Paths,
    *,
    version: str | None = None,
    check: bool = False,
    force: bool = False,
    out: TextIO,
    resolve_latest: Callable[[], str] | None = None,
    fetch_release: Callable[[str, Path], Path] | None = None,
) -> None:
    """Replace the installed copy with another release.

    resolve_latest and fetch_release exist so tests can answer the network
    steps; the command itself always uses GitHub.
    """
    installed = read_installed(paths)
    if installed is None:
        raise InstallError(
            "desk is not installed; run ./desk install --source . from a checkout"
        )
    resolver = latest_release_tag if resolve_latest is None else resolve_latest
    tag = tag_for_version(version) if version is not None else resolver()
    target = version_text(tag)
    if check:
        print(f"installed: {installed.version} ({installed.source})", file=out)
        print(f"available: {target}", file=out)
        return
    if not installed.from_release and not force:
        raise InstallError(
            f"this copy was installed from a checkout ({installed.source}); "
            "run ./desk install again, or pass --force to replace it with a release"
        )
    if target == installed.version and not force:
        print(f"desk {target} is already installed.", file=out)
        return
    check_launcher(paths, force=force)
    fetcher = download_release if fetch_release is None else fetch_release
    with tempfile.TemporaryDirectory(prefix="termux-desktop-update-") as workdir:
        root = find_source_root(Path(fetcher(tag, Path(workdir))))
        found = source_version(root)
        if found != target:
            raise InstallError(
                f"release {tag} contains version {found}, not {target}"
            )
        install_from_source(
            paths,
            root,
            version=target,
            source=f"{SOURCE_RELEASE}:{tag}",
            out=out,
        )
    _print_refresh_hint(out)


def latest_release_tag() -> str:
    """Return the tag of the latest GitHub release."""
    location = http_location(RELEASES_LATEST_URL)
    if not location:
        raise InstallError(
            "no release was found; run ./install.sh from a checkout instead"
        )
    return tag_from_location(location.rstrip("/"))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_location(url: str) -> str:
    """Return the redirect Location of a HEAD request, or an empty string."""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    request = urllib.request.Request(url, method="HEAD")
    try:
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            return response.headers.get("Location") or ""
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") if error.headers is not None else None
        if location:
            return location
        if error.code == 404:
            return ""
        raise InstallError(f"GitHub answered {error.code} for {url}") from error
    except urllib.error.URLError as error:
        raise InstallError(f"could not reach {url}: {error.reason}") from error


def download_release(tag: str, workdir: Path) -> Path:
    """Download the archive of one tag, extract it, and return its root."""
    url = ARCHIVE_URL.format(repository=REPOSITORY, tag=tag)
    archive = workdir / f"{tag}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT) as response:
            with archive.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except (urllib.error.URLError, OSError) as error:
        raise InstallError(f"could not download {url}: {error}") from error
    try:
        extract_archive(archive, workdir)
    finally:
        archive.unlink(missing_ok=True)
    return find_source_root(workdir)


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract a release archive, refusing paths outside the destination.

    The members are checked first so that a damaged or hostile archive
    fails with a message instead of writing outside the directory.
    """
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with tarfile.open(archive, "r:gz") as handle:
            for member in handle.getmembers():
                target = (destination / member.name).resolve()
                if target != root and not str(target).startswith(str(root) + os.sep):
                    raise InstallError(
                        f"{archive} contains a path outside the archive: {member.name}"
                    )
                if member.issym() or member.islnk():
                    raise InstallError(
                        f"{archive} contains a link, which desk does not install: {member.name}"
                    )
            handle.extractall(destination, filter="data")
    except tarfile.TarError as error:
        raise InstallError(f"could not read {archive}: {error}") from error


def _print_refresh_hint(out: TextIO) -> None:
    print(
        "run desk app refresh to point the container menu entries at this copy",
        file=out,
    )
