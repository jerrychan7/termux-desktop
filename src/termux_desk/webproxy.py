"""Serve a VNC display to a browser through noVNC and a WebSocket proxy.

The X server speaks RFB over TCP; a browser cannot open that socket. The page
is static noVNC JavaScript, and a WebSocket proxy turns the browser's frames
back into the RFB stream. Both proxies are optional downloads: the Rust one is
small and is used by default, the Python one is the fallback for architectures
the Rust binary does not cover. The page stays on the loopback interface
unless the user asks for another address.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from . import vnc
from .config import DeskError, Paths
from .runner import CommandRunner
from .session import pid_alive, session_status

NOVNC_VERSION = "v1.7.0"
NOVNC_URL = f"https://github.com/novnc/noVNC/archive/refs/tags/{NOVNC_VERSION}.tar.gz"
NOVNC_SHA256 = "b1003a11b6e6e8d8f7f5e5586daae7f8ca651d8aee0aa155ff9ac841c48f52c6"

WEBSOCKIFY_VERSION = "0.13.0"
WEBSOCKIFY_WHEEL = f"websockify-{WEBSOCKIFY_VERSION}-py3-none-any.whl"
WEBSOCKIFY_SHA256 = "ac497a8dafc7f51d28ca989f01cdf5f8add663a497d425a56b159dcb8d6a314c"

RS_VERSION = "v0.1.0"
RS_ASSETS = {
    "aarch64": (
        "websockify_rs-aarch64",
        "27c23c440a9184efbb566549c866625eab628978cc93f14111e58d6959004f08",
    ),
    "armv7": (
        "websockify_rs-armv7",
        "999aae0cd87a19b4ef16ba0a0e906ac061aa784391ae276d3631c8edaec9c905",
    ),
    "x86_64": (
        "websockify_rs-x86_64",
        "65ace8a356d593ba4938ffa7899b9cb8a1ba23820d4b6b6d50434a5df5c76105",
    ),
}
RS_URL_TEMPLATE = (
    "https://github.com/bajrangCoder/websockify_rs/releases/download/"
    f"{RS_VERSION}/{{asset}}"
)

WEB_DIR_NAME = "vnc-web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6080
WEB_STATE_VERSION = 1

PROXY_PY = "py"
PROXY_RS = "rs"
PROXY_CHOICES = (PROXY_PY, PROXY_RS, "both")

ARCH_ALIASES = {
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv7l": "armv7",
    "armv8l": "armv7",
    "x86_64": "x86_64",
    "amd64": "x86_64",
}

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class WebProxyError(DeskError):
    """The browser proxy cannot be installed, started, or stopped."""


@dataclass(frozen=True)
class WebProxyState:
    """The recorded state of a running browser proxy."""

    display: str
    backend: str
    host: str
    port: int
    web_pid: int
    static_pid: int | None = None
    started_at: float = 0.0

    def to_json(self) -> str:
        record = {
            "version": WEB_STATE_VERSION,
            "display": self.display,
            "backend": self.backend,
            "host": self.host,
            "port": self.port,
            "web_pid": self.web_pid,
            "static_pid": self.static_pid,
            "started_at": self.started_at,
        }
        return json.dumps(record, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "WebProxyState":
        try:
            record = json.loads(text)
        except json.JSONDecodeError as error:
            raise WebProxyError(f"the web proxy state is not valid JSON: {error}") from error
        if not isinstance(record, dict) or record.get("version") != WEB_STATE_VERSION:
            raise WebProxyError("the web proxy state has an unknown version")
        try:
            return cls(
                display=str(record["display"]),
                backend=str(record["backend"]),
                host=str(record["host"]),
                port=int(record["port"]),
                web_pid=int(record["web_pid"]),
                static_pid=(
                    None if record.get("static_pid") is None else int(record["static_pid"])
                ),
                started_at=float(record.get("started_at", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WebProxyError(f"the web proxy state is incomplete: {error}") from error


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------


def web_root(paths: Paths) -> Path:
    """Return the directory that holds the downloaded noVNC and proxies."""
    if paths.prefix is None:
        raise WebProxyError("PREFIX is not set. desk must run inside Termux.")
    return paths.prefix / "share" / "termux-desktop" / WEB_DIR_NAME


def novnc_dir(paths: Paths) -> Path:
    return web_root(paths) / "novnc"


def python_proxy_dir(paths: Paths) -> Path:
    return web_root(paths) / "websockify"


def rs_proxy_path(paths: Paths) -> Path:
    return web_root(paths) / "websockify_rs"


def state_path(paths: Paths, display: str) -> Path:
    number = display[1:] if display.startswith(":") else display
    return paths.state_dir / f"web-{number}.json"


def log_path(paths: Paths, display: str) -> Path:
    number = display[1:] if display.startswith(":") else display
    return paths.state_dir / f"web-{number}.log"


def machine_arch(machine: str | None = None) -> str:
    """Return the release asset name for the current CPU, or raise."""
    name = (machine or platform.machine()).lower()
    arch = ARCH_ALIASES.get(name)
    if arch is None:
        raise WebProxyError(
            f"websockify_rs has no build for {name!r}; install the Python proxy "
            "with 'desk vnc web setup --proxy py'"
        )
    return arch


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise WebProxyError(
            f"the {label} checksum does not match: got {actual}, expected {expected}. "
            "The download was rejected; run the setup again."
        )


def download(url: str, destination: Path) -> None:
    """Download url to destination, raising WebProxyError on failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except OSError as error:
        raise WebProxyError(f"could not download {url}: {error}") from error


def extract_tarball(archive: Path, destination: Path) -> None:
    """Extract a .tar.gz and strip the single top-level directory."""
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            if not members:
                raise WebProxyError(f"{archive} is empty")
            prefix = members[0].name.split("/", 1)[0]
            if not all(member.name == prefix or member.name.startswith(prefix + "/") for member in members):
                raise WebProxyError(f"{archive} does not have a single top-level directory")
            for member in members:
                if member.name == prefix:
                    continue
                member.name = member.name[len(prefix) + 1 :]
                handle.extract(member, destination)
    except (tarfile.TarError, OSError) as error:
        raise WebProxyError(f"could not extract {archive}: {error}") from error


def extract_wheel(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(destination)
    except (zipfile.BadZipFile, OSError) as error:
        raise WebProxyError(f"could not extract {archive}: {error}") from error


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_novnc(
    paths: Paths,
    *,
    fetcher: Callable[[str, Path], None] = download,
    force: bool = False,
    out: TextIO | None = None,
) -> Path:
    """Download and unpack the pinned noVNC tree."""
    output = sys.stdout if out is None else out
    target = novnc_dir(paths)
    if target.is_dir() and not force:
        if (target / "vnc.html").is_file():
            print(f"noVNC {NOVNC_VERSION} is already in place at {target}.", file=output)
            return target
        raise WebProxyError(
            f"{target} exists but has no vnc.html; run 'desk vnc web setup --force'"
        )
    root = web_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(root)) as temporary:
        archive = Path(temporary) / "novnc.tar.gz"
        print(f"Downloading noVNC {NOVNC_VERSION} ...", file=output, flush=True)
        fetcher(NOVNC_URL, archive)
        verify_sha256(archive, NOVNC_SHA256, f"noVNC {NOVNC_VERSION}")
        unpacked = Path(temporary) / "unpacked"
        unpacked.mkdir()
        extract_tarball(archive, unpacked)
        if not (unpacked / "vnc.html").is_file():
            raise WebProxyError("the noVNC archive does not contain vnc.html")
        if target.exists():
            shutil.rmtree(target)
        unpacked.rename(target)
    print(f"noVNC {NOVNC_VERSION} is installed at {target}.", file=output)
    return target


def install_python_proxy(
    paths: Paths,
    *,
    runner: CommandRunner | None = None,
    force: bool = False,
    out: TextIO | None = None,
) -> Path:
    """Download the pinned websockify wheel and unpack it without installing it."""
    output = sys.stdout if out is None else out
    target = python_proxy_dir(paths)
    if target.is_dir() and not force:
        if (target / "websockify" / "__init__.py").is_file():
            print(f"Python websockify {WEBSOCKIFY_VERSION} is already in place.", file=output)
            return target
        raise WebProxyError(
            f"{target} exists but is not unpacked; run 'desk vnc web setup --force'"
        )
    commands = runner if runner is not None else CommandRunner()
    root = web_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(root)) as temporary:
        destination = Path(temporary) / "wheel"
        destination.mkdir()
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--dest",
            str(destination),
            f"websockify=={WEBSOCKIFY_VERSION}",
        ]
        print(f"Downloading Python websockify {WEBSOCKIFY_VERSION} ...", file=output, flush=True)
        try:
            result = commands.run(command)
        except OSError as error:
            raise WebProxyError(f"could not run pip download: {error}") from error
        if not result.succeeded:
            raise WebProxyError(
                "pip download failed; check the network connection and that pip is installed"
            )
        wheels = sorted(glob.glob(str(destination / "websockify-*.whl")))
        if not wheels:
            raise WebProxyError("pip downloaded no websockify wheel")
        wheel = Path(wheels[0])
        verify_sha256(wheel, WEBSOCKIFY_SHA256, f"websockify {WEBSOCKIFY_VERSION}")
        unpacked = Path(temporary) / "unpacked"
        unpacked.mkdir()
        extract_wheel(wheel, unpacked)
        if not (unpacked / "websockify" / "__init__.py").is_file():
            raise WebProxyError("the websockify wheel does not contain the package")
        if target.exists():
            shutil.rmtree(target)
        unpacked.rename(target)
    print(f"Python websockify {WEBSOCKIFY_VERSION} is installed at {target}.", file=output)
    return target


def install_rs_proxy(
    paths: Paths,
    *,
    fetcher: Callable[[str, Path], None] = download,
    machine: str | None = None,
    force: bool = False,
    out: TextIO | None = None,
) -> Path:
    """Download the Rust proxy binary for this CPU."""
    output = sys.stdout if out is None else out
    arch = machine_arch(machine)
    asset, expected = RS_ASSETS[arch]
    target = rs_proxy_path(paths)
    if target.is_file() and not force:
        if sha256_file(target) == expected:
            print(f"websockify_rs {RS_VERSION} ({arch}) is already in place.", file=output)
            return target
        print(f"websockify_rs at {target} does not match the recorded checksum; replacing it.", file=output)
    root = web_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(root)) as temporary:
        archive = Path(temporary) / asset
        url = RS_URL_TEMPLATE.format(asset=asset)
        print(f"Downloading websockify_rs {RS_VERSION} ({arch}) ...", file=output, flush=True)
        fetcher(url, archive)
        verify_sha256(archive, expected, f"websockify_rs {arch}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        shutil.move(str(archive), str(target))
        target.chmod(0o755)
    print(f"websockify_rs {RS_VERSION} ({arch}) is installed at {target}.", file=output)
    return target


def run_web_setup(
    paths: Paths,
    *,
    proxy: str = PROXY_RS,
    force: bool = False,
    fetcher: Callable[[str, Path], None] = download,
    runner: CommandRunner | None = None,
    machine: str | None = None,
    out: TextIO | None = None,
) -> None:
    """Install noVNC and the chosen proxy or proxies."""
    output = sys.stdout if out is None else out
    if proxy not in PROXY_CHOICES:
        raise WebProxyError(
            f"unknown proxy {proxy!r}; choose one of {', '.join(PROXY_CHOICES)}"
        )
    print(f"Web directory: {web_root(paths)}", file=output)
    install_novnc(paths, fetcher=fetcher, force=force, out=output)
    if proxy in (PROXY_RS, "both"):
        install_rs_proxy(paths, fetcher=fetcher, machine=machine, force=force, out=output)
    if proxy in (PROXY_PY, "both"):
        install_python_proxy(paths, runner=runner, force=force, out=output)
    print("The browser page is ready; start it with 'desk vnc web start'.", file=output)


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def web_url(state: WebProxyState) -> str:
    """Return the page URL, including the WebSocket port for the Rust proxy."""
    if state.backend == PROXY_RS:
        ws_port = state.port + 1
        return (
            f"http://{state.host}:{state.port}/vnc.html"
            f"?host={state.host}&port={ws_port}"
        )
    return f"http://{state.host}:{state.port}/vnc.html"


def read_web_state(paths: Paths, display: str) -> WebProxyState | None:
    path = state_path(paths, display)
    if not path.is_file():
        return None
    try:
        return WebProxyState.from_json(path.read_text())
    except WebProxyError:
        return None


def write_web_state(paths: Paths, state: WebProxyState) -> None:
    state_path(paths, state.display).write_text(state.to_json())


def remove_web_state(paths: Paths, display: str) -> None:
    try:
        state_path(paths, display).unlink()
    except FileNotFoundError:
        pass


def port_is_free(host: str, port: int) -> bool:
    bind_host = "" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind_host, port))
        except OSError:
            return False
    return True


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    address = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex((address, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _ensure_vnc_display(
    paths: Paths,
    *,
    display: str,
    out: TextIO,
    color: bool,
) -> None:
    status = session_status(paths.with_display(display))
    if status.running:
        print(f"The VNC session on {display} is already running.", file=out)
        return
    print(f"Starting the VNC session on {display} ...", file=out, flush=True)
    try:
        vnc.run_vnc_start(paths, display=display, out=out, color=color)
    except vnc.VncError as error:
        raise WebProxyError(f"could not start the VNC session on {display}: {error}") from error


def _proxy_command(
    paths: Paths,
    *,
    backend: str,
    display: str,
    host: str,
    port: int,
) -> tuple[list[str], dict[str, str]]:
    rfb = vnc.rfb_port(display)
    environment = dict(os.environ)
    if backend == PROXY_PY:
        directory = python_proxy_dir(paths)
        if not (directory / "websockify" / "__init__.py").is_file():
            raise WebProxyError(
                "the Python proxy is not installed; run 'desk vnc web setup --proxy py'"
            )
        environment["PYTHONPATH"] = str(directory)
        return (
            [
                sys.executable,
                "-m",
                "websockify",
                "--web",
                str(novnc_dir(paths)),
                f"{host}:{port}",
                f"127.0.0.1:{rfb}",
            ],
            environment,
        )
    binary = rs_proxy_path(paths)
    if not binary.is_file():
        raise WebProxyError(
            "the Rust proxy is not installed; run 'desk vnc web setup'"
        )
    return ([str(binary), f"{host}:{port + 1}", f"127.0.0.1:{rfb}"], environment)


def run_web_start(
    paths: Paths,
    *,
    display: str = vnc.VNC_DISPLAY,
    proxy: str = PROXY_RS,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    color: bool = False,
    out: TextIO | None = None,
) -> WebProxyState:
    """Ensure the VNC session, start the WebSocket proxy, print the page URL."""
    output = sys.stdout if out is None else out
    if proxy not in (PROXY_PY, PROXY_RS):
        raise WebProxyError(f"unknown proxy {proxy!r}; choose py or rs")
    if not (1 <= port <= 65535):
        raise WebProxyError(f"invalid port {port}; expected 1..65535")
    if not (novnc_dir(paths) / "vnc.html").is_file():
        raise WebProxyError(
            "the browser page is not installed; run 'desk vnc web setup'"
        )
    existing = read_web_state(paths, display)
    if existing is not None and pid_alive(existing.web_pid):
        raise WebProxyError(
            f"the web proxy for {display} is already running at {web_url(existing)}"
        )
    remove_web_state(paths, display)

    _ensure_vnc_display(paths, display=display, out=output, color=color)

    if not port_is_free(host, port):
        raise WebProxyError(f"{host}:{port} is already in use")
    if proxy == PROXY_RS and not port_is_free(host, port + 1):
        raise WebProxyError(f"{host}:{port + 1} is already in use (needed for the Rust proxy)")

    darkhttpd: str | None = None
    if proxy == PROXY_RS:
        darkhttpd = shutil.which("darkhttpd")
        if darkhttpd is None:
            raise WebProxyError(
                "darkhttpd was not found; run 'desk vnc setup' to install it"
            )

    command, environment = _proxy_command(
        paths, backend=proxy, display=display, host=host, port=port
    )
    log = log_path(paths, display)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as handle:
        web = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
    static: subprocess.Popen | None = None
    if proxy == PROXY_RS:
        static_command = [
            darkhttpd,
            str(novnc_dir(paths)),
            "--port",
            str(port),
            "--addr",
            host,
        ]
        with log.open("ab") as handle:
            static = subprocess.Popen(
                static_command,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )
    try:
        page_port = port
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        if not _wait_for_port(probe_host, page_port):
            raise WebProxyError(
                f"the page did not start on {host}:{port}; see {log} for details"
            )
        if proxy == PROXY_RS and not _wait_for_port(probe_host, port + 1):
            raise WebProxyError(
                f"the proxy did not start on {host}:{port + 1}; see {log} for details"
            )
    except WebProxyError:
        for process in (web, static):
            if process is not None:
                process.terminate()
        raise

    state = WebProxyState(
        display=display,
        backend=proxy,
        host=host,
        port=port,
        web_pid=web.pid,
        static_pid=None if static is None else static.pid,
        started_at=time.time(),
    )
    write_web_state(paths, state)
    print(f"Browser page: {web_url(state)}", file=output)
    if host not in LOOPBACK_HOSTS:
        print(
            f"Warning: the page and the proxy listen on {host}, which is not "
            "loopback. Anyone who can reach this address can open the desktop.",
            file=output,
        )
    print(f"Logs: {log}", file=output)
    return state


def run_web_stop(
    paths: Paths,
    *,
    display: str = vnc.VNC_DISPLAY,
    out: TextIO | None = None,
    session_stopped: bool = False,
) -> bool:
    """Stop the web proxy for display; the VNC session keeps running by default."""
    output = sys.stdout if out is None else out
    state = read_web_state(paths, display)
    if state is None:
        print(f"There is no web proxy for {display}.", file=output)
        return False
    stopped = False
    for pid in (state.web_pid, state.static_pid):
        if pid is not None and pid_alive(pid):
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            stopped = True
    deadline = time.monotonic() + 5.0
    while stopped and time.monotonic() < deadline:
        if not any(pid_alive(pid) for pid in (state.web_pid, state.static_pid) if pid is not None):
            break
        time.sleep(0.1)
    for pid in (state.web_pid, state.static_pid):
        if pid is not None and pid_alive(pid):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
    remove_web_state(paths, display)
    if session_stopped:
        print(f"Stopped the web proxy for {display}.", file=output)
    else:
        print(f"Stopped the web proxy for {display}; the VNC session was not changed.", file=output)
    return True


def run_web_status(
    paths: Paths,
    *,
    display: str = vnc.VNC_DISPLAY,
    out: TextIO | None = None,
) -> bool:
    """Print whether the web proxy for display is running."""
    output = sys.stdout if out is None else out
    state = read_web_state(paths, display)
    if state is None or not pid_alive(state.web_pid):
        print(f"The web proxy for {display} is not running.", file=output)
        return False
    print(
        f"The web proxy for {display} is running: {state.backend} at "
        f"{web_url(state)} (pid {state.web_pid})",
        file=output,
    )
    return True
