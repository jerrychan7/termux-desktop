"""Download, verify and install the assets that a theme manifest names."""

from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping, TextIO

from .config import Paths
from .theme_model import MAX_DOWNLOAD_BYTES as MAX_DOWNLOAD_BYTES
from .theme_model import TOFU_WARNING as TOFU_WARNING
from .theme_model import (
    Theme,
    ThemeAsset,
    ThemeError,
    ThemeState,
    _printable,
    assets_directory,
)

Fetcher = Callable[[str], bytes]

MAX_DOWNLOAD_SECONDS = 30 * 60
DOWNLOAD_CHUNK = 64 * 1024


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects that leave HTTPS, so a 302 cannot downgrade the download."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - urllib API
        if not newurl.lower().startswith("https://"):
            raise ThemeError(f"{req.full_url} redirected to {newurl!r}, which is not HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_fetcher(url: str) -> bytes:
    """Download one asset into memory, refusing non-HTTPS URLs and huge files."""
    if not url.lower().startswith("https://"):
        raise ThemeError(f"{url} is not an HTTPS URL")
    request = urllib.request.Request(url, headers={"User-Agent": "desk-theme"})
    opener = urllib.request.build_opener(_HttpsRedirect)
    deadline = time.monotonic() + MAX_DOWNLOAD_SECONDS
    try:
        with opener.open(request, timeout=60) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
                raise ThemeError(f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes")
            chunks: list[bytes] = []
            total = 0
            while True:
                if time.monotonic() > deadline:
                    raise ThemeError(f"could not download {url}: timed out")
                chunk = response.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ThemeError(f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes")
                chunks.append(chunk)
                if total % (4 * 1024 * 1024) < DOWNLOAD_CHUNK:
                    print(f"downloaded {total / 1048576:.1f} MiB of {url.rsplit('/', 1)[-1]}", file=sys.stderr, flush=True)
    except urllib.error.URLError as error:
        raise ThemeError(f"could not download {url}: {error.reason}") from error
    except TimeoutError as error:
        raise ThemeError(f"could not download {url}: timed out") from error
    return b"".join(chunks)


def _sha256(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of some bytes."""
    return hashlib.sha256(data).hexdigest()



def _cache_name(asset: ThemeAsset) -> str:
    """Return the file name used for an asset in the cache.

    The URL query is folded into a short digest so two URLs that only differ
    in their parameters do not share one cache file.
    """
    if asset.is_url:
        parsed = urllib.parse.urlparse(asset.source)
        name = Path(parsed.path).name
        if parsed.query:
            digest = hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()[:8]
            stem, dot, suffix = name.rpartition(".")
            name = f"{stem}-{digest}.{suffix}" if dot else f"{name}-{digest}"
    else:
        name = Path(asset.source).name
    if not name or name in (".", ".."):
        name = f"{asset.kind}.bin"
    return name


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write bytes through a temporary file and a rename."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise ThemeError(f"could not write {_printable(str(path))}: {error}") from error


def _local_source(asset: ThemeAsset, manifest: Path) -> Path:
    """Return the local file a 'path' asset points at, after checking its hash."""
    local = Path(asset.source).expanduser()
    if not local.is_absolute():
        local = (manifest.parent / local).resolve()
    if not local.is_file():
        raise ThemeError(
            f"{_printable(asset.source)} does not exist; "
            "declare 'url' to download the asset or fix the 'path'"
        )
    if asset.sha256 is not None:
        try:
            data = local.read_bytes()
        except OSError as error:
            raise ThemeError(f"could not read {_printable(str(local))}: {error}") from error
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ThemeError(f"{_printable(str(local))} is larger than {MAX_DOWNLOAD_BYTES} bytes")
        if _sha256(data) != asset.sha256:
            raise ThemeError(f"sha256 mismatch for {_printable(str(local))}")
    return local


def _download_source(
    paths: Paths,
    asset: ThemeAsset,
    theme_name: str,
    allow_unverified: bool,
    hashes: Mapping[str, str],
    fetch: Fetcher,
    out: TextIO,
) -> tuple[Path, str | None]:
    """Download one URL asset, verify it and keep a cached copy."""
    expected = asset.sha256 or hashes.get(asset.source)
    if expected is None and not allow_unverified:
        raise ThemeError(f"{_printable(asset.source)}: {TOFU_WARNING}")
    cache = assets_directory(paths, theme_name) / "cache"
    cached = cache / _cache_name(asset)
    if expected is not None and cached.is_file():
        try:
            if _sha256(cached.read_bytes()) == expected:
                print(f"reused {_printable(str(cached))}", file=out)
                return cached, None
        except OSError:
            pass
    data = fetch(asset.source)
    digest = _sha256(data)
    if expected is not None and digest != expected:
        raise ThemeError(f"sha256 mismatch for {_printable(asset.source)}")
    cache.mkdir(parents=True, exist_ok=True)
    _write_bytes_atomic(cached, data)
    print(f"downloaded {_printable(asset.source)}", file=out)
    return cached, digest


def _locate_source(
    paths: Paths,
    asset: ThemeAsset,
    manifest: Path,
    theme_name: str,
    allow_unverified: bool,
    hashes: Mapping[str, str],
    fetch: Fetcher,
    out: TextIO,
) -> tuple[Path, str | None]:
    """Return the local file to install and the hash of a fresh download."""
    if asset.is_url:
        return _download_source(paths, asset, theme_name, allow_unverified, hashes, fetch, out)
    return _local_source(asset, manifest), None


def _install_file(
    paths: Paths, asset: ThemeAsset, source: Path, theme_name: str, *, force: bool, out: TextIO
) -> Path:
    """Copy a font or wallpaper into the directory that reads it."""
    if asset.kind == "font":
        destination = paths.home / ".fonts" / source.name
    else:
        destination = assets_directory(paths, theme_name) / "wallpapers" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        print(f"kept {destination}", file=out)
        return destination
    shutil.copyfile(source, destination)
    if asset.kind == "font" and shutil.which("fc-cache") is None:
        print(
            "the font is installed; restart the session (or run fc-cache -f) "
            "if applications do not see it yet",
            file=out,
        )
    print(f"installed {destination}", file=out)
    return destination



def _member_bytes(source: Path, wanted: str) -> bytes:
    """Return one member of an archive, matched by full path or by name."""
    wanted = posixpath.normpath(wanted)
    with tarfile.open(source, "r:*") as archive:
        fallback = None
        for member in archive.getmembers():
            name = posixpath.normpath(member.name).lstrip("./")
            if name == wanted:
                return archive.extractfile(member).read()
            if fallback is None and name.endswith("/" + wanted):
                fallback = member
        if fallback is not None and archive.extractfile(fallback) is not None:
            return archive.extractfile(fallback).read()
    raise ThemeError(f"{source.name} does not contain {wanted!r}")


def _install_font_archive(
    paths: Paths, asset: ThemeAsset, source: Path, theme_name: str, *, force: bool, out: TextIO
) -> Path:
    """Install one font file that lives inside a larger archive."""
    destination = paths.home / ".fonts" / asset.target
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        print(f"kept {destination}", file=out)
        return destination
    data = _member_bytes(source, asset.pick or asset.target)
    destination.write_bytes(data)
    if shutil.which("fc-cache") is None:
        print(
            "the font is installed; restart the session (or run fc-cache -f) "
            "if applications do not see it yet",
            file=out,
        )
    print(f"installed {destination}", file=out)
    return destination

def _install_tree(paths: Paths, asset: ThemeAsset, source: Path, *, force: bool, out: TextIO) -> Path:
    """Install one theme or icon directory and its legacy symlink."""
    data_home = paths.xdg_data_home
    if asset.kind == "theme":
        destination = data_home / "themes" / asset.target
        alias = paths.home / ".themes" / asset.target
    else:
        destination = data_home / "icons" / asset.target
        alias = paths.home / ".icons" / asset.target
    if destination.exists() and not force:
        if not alias.exists() and not alias.is_symlink():
            _make_alias(alias, destination, out, force=False)
        print(f"kept {destination}", file=out)
        return destination
    if source.is_dir():
        _copy_tree(source, destination, force=force)
    else:
        _extract_archive(source, destination, force=force, pick=asset.pick or asset.target)
    _make_alias(alias, destination, out, force=force)
    if asset.kind == "icons":
        _refresh_icon_cache(destination, out)
    print(f"installed {destination}", file=out)
    return destination


def _refresh_icon_cache(directory: Path, out: TextIO) -> None:
    """Update the icon cache when the tool is available."""
    tool = shutil.which("gtk-update-icon-cache")
    if tool is None:
        return
    try:
        subprocess.run(
            [tool, "-f", "-t", str(directory)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        print(
            "could not update the icon cache; restart the session to see the icons",
            file=out,
        )


def _install_asset(
    paths: Paths,
    asset: ThemeAsset,
    *,
    theme_name: str,
    manifest: Path,
    allow_unverified: bool,
    force: bool,
    hashes: Mapping[str, str],
    fetch: Fetcher,
    out: TextIO,
) -> tuple[str, str | None]:
    """Install one asset and return (installed path, new hash)."""
    assets_directory(paths, theme_name).mkdir(parents=True, exist_ok=True)
    source, digest = _locate_source(
        paths, asset, manifest, theme_name, allow_unverified, hashes, fetch, out
    )
    if asset.kind == "config":
        destination = _install_config(paths, asset, source, theme_name, force=force, out=out)
    elif asset.kind == "wallpaper" and _is_archive(source.name):
        destination = _install_wallpaper_archive(
            paths, asset, source, theme_name, force=force, out=out
        )
    elif asset.kind == "font" and _is_archive(source.name):
        destination = _install_font_archive(
            paths, asset, source, theme_name, force=force, out=out
        )
    elif asset.kind in ("font", "wallpaper"):
        destination = _install_file(paths, asset, source, theme_name, force=force, out=out)
    else:
        destination = _install_tree(paths, asset, source, force=force, out=out)
    return str(destination), digest


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".bmp", ".gif")


def _config_bytes(source: Path, target: str) -> bytes:
    """Return one config file, either the asset itself or a member of its archive."""
    if not _is_archive(source.name):
        return source.read_bytes()
    with tarfile.open(source, "r:*") as archive:
        for member in archive.getmembers():
            name = posixpath.normpath(member.name).lstrip("./")
            if name == target or name.endswith("/" + target):
                handle = archive.extractfile(member)
                if handle is not None:
                    return handle.read()
                break
    raise ThemeError(f"{source.name} does not contain {target!r}")


def config_backup_path(paths: Paths, theme_name: str, target: str) -> Path:
    """Return where the previous version of one config file is kept."""
    return assets_directory(paths, theme_name) / "config-backup" / target


def _install_config(
    paths: Paths, asset: ThemeAsset, source: Path, theme_name: str, *, force: bool, out: TextIO
) -> Path:
    """Replace one whitelisted application config file, keeping the old copy."""
    destination = paths.home / ".config" / asset.target
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = _config_bytes(source, asset.target)
    if destination.exists():
        backup = config_backup_path(paths, theme_name, asset.target)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination, backup)
    _write_bytes_atomic(destination, data)
    print(f"installed {destination}", file=out)
    return destination
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")


def _is_archive(name: str) -> bool:
    """Return True when this file name looks like a tarball."""
    return name.lower().endswith(ARCHIVE_SUFFIXES)


def _install_wallpaper_archive(
    paths: Paths, asset: ThemeAsset, source: Path, theme_name: str, *, force: bool, out: TextIO
) -> Path:
    """Pick one image from a wallpaper tarball and install it under its own name."""
    destination_dir = assets_directory(paths, theme_name) / "wallpapers"
    destination_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="desk-wallpaper-") as temporary:
        extracted = Path(temporary) / "unpacked"
        extracted.mkdir()
        _extract_archive(source, extracted, force=True)
        images = [entry for entry in extracted.rglob("*") if entry.is_file()]
        wanted = asset.target
        if asset.pick:
            picked_name = posixpath.basename(asset.pick)
            matches = [entry for entry in images if entry.name == picked_name]
        else:
            matches = [entry for entry in images if entry.name == wanted]
        if not matches:
            names = ", ".join(sorted(entry.name for entry in images)[:8]) or "(none)"
            raise ThemeError(
                f"{source.name} does not contain {wanted!r}; it has: {names}"
            )
        picked = matches[0]
        destination = destination_dir / picked.name
        if destination.exists() and not force:
            print(f"kept {destination}", file=out)
            return destination
        shutil.copyfile(picked, destination)
    print(f"installed {destination}", file=out)
    return destination


MAX_ARCHIVE_ENTRIES = 200_000
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def _clear_path(path: Path) -> None:
    """Remove whatever currently occupies a destination, file or directory."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _inside(root: Path, entry: Path) -> bool:
    """Return True when entry still resolves inside root."""
    try:
        resolved = entry.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _verify_links(root: Path, archive: Path) -> None:
    """Refuse links that point outside the trees an archive just installed."""
    for entry in root.rglob("*"):
        if entry.is_symlink() and not _inside(root, entry):
            raise ThemeError(f"{archive.name} contains a link that points outside the theme")


def _copy_tree(source: Path, destination: Path, *, force: bool) -> None:
    """Copy a directory tree, replacing the destination when forced."""
    if destination.exists() or destination.is_symlink():
        if not force:
            return
        _clear_path(destination)
    shutil.copytree(source, destination, symlinks=True)
    try:
        _verify_links(destination, source)
    except ThemeError:
        _clear_path(destination)
        raise


def _make_alias(alias: Path, target: Path, out: TextIO, *, force: bool) -> None:
    """Create the legacy ~/.themes or ~/.icons symlink when it is free."""
    if alias.exists() or alias.is_symlink():
        if not force:
            return
        _clear_path(alias)
    alias.parent.mkdir(parents=True, exist_ok=True)
    try:
        alias.symlink_to(target)
    except OSError as error:
        print(f"warning: could not create {alias}: {error}", file=out)


def _extract_archive(
    source: Path, destination: Path, *, force: bool, pick: str | None = None
) -> None:
    """Extract a theme archive without allowing it to escape the destination."""
    name = source.name.lower()
    if not name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")):
        raise ThemeError(f"{source} is not a supported archive; use .tar.gz, .tgz, .tar.xz or .tar.bz2")
    with tempfile.TemporaryDirectory(prefix="desk-theme-") as temporary:
        root = Path(temporary)
        with tarfile.open(source, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_ENTRIES:
                raise ThemeError(
                    f"{source} has {len(members)} entries; the limit is {MAX_ARCHIVE_ENTRIES}"
                )
            expanded = 0
            for member in members:
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ThemeError(f"{source} contains an unsafe path")
                if member.issym():
                    link = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                    if member.linkname.startswith("/") or link.startswith(".."):
                        raise ThemeError(f"{source} contains a link that points outside the archive")
                elif not (member.isfile() or member.isdir()):
                    raise ThemeError(f"{source} contains a link or device entry")
                expanded += max(member.size, 0)
            if expanded > MAX_ARCHIVE_BYTES:
                raise ThemeError(f"{source} unpacks to {expanded} bytes; the limit is {MAX_ARCHIVE_BYTES}")
            archive.extractall(root, members=members)
        entries = [entry for entry in root.iterdir()]
        if pick is not None and (root / pick).is_dir():
            content = root / pick
        elif len(entries) == 1 and entries[0].is_dir():
            content = entries[0]
        elif pick is not None:
            names = ", ".join(sorted(entry.name for entry in entries if entry.is_dir())[:8])
            raise ThemeError(
                f"{source.name} does not contain {pick!r}; it has: {names or '(nothing)'}"
            )
        else:
            content = root
        if destination.exists() or destination.is_symlink():
            if not force:
                return
            _clear_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(content, destination, symlinks=True)
        _verify_links(destination, source)


def _install_assets(
    paths: Paths,
    theme: Theme,
    *,
    allow_unverified: bool,
    force: bool,
    state: ThemeState,
    fetch: Fetcher,
    out: TextIO,
) -> tuple[ThemeState, Mapping[str, str]]:
    """Install every declared asset and return the new state and paths."""
    installed = list(state.installed)
    hashes = dict(state.hashes)
    configs = list(state.configs)
    installed_paths: dict[str, str] = {}
    for asset in theme.assets:
        path, digest = _install_asset(
            paths,
            asset,
            theme_name=theme.name,
            manifest=theme.path,
            allow_unverified=allow_unverified,
            force=force,
            hashes=hashes,
            fetch=fetch,
            out=out,
        )
        installed_paths[asset.target] = path
        if path not in installed:
            installed.append(path)
        if asset.kind == "config" and asset.target not in configs:
            configs.append(asset.target)
        if digest:
            hashes[asset.source] = digest
    return (
        ThemeState(
            active=state.active,
            snapshot=state.snapshot,
            installed=tuple(installed),
            hashes=hashes,
            display=state.display,
            configs=tuple(configs),
        ),
        installed_paths,
    )


