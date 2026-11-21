"""Reading layout manifests from disk."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .layout_errors import LayoutError
from .theme_model import _printable

MAX_MANIFEST_BYTES = 64 * 1024


def read_manifest(path: Path) -> str:
    """Read one layout manifest, refusing links, huge files and non-UTF-8 bytes."""
    try:
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise LayoutError(f"could not read {_printable(str(path))}: {error}") from error
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise LayoutError(f"{_printable(str(path))} is not a regular file")
        data = os.read(handle, MAX_MANIFEST_BYTES + 1)
    except OSError as error:
        raise LayoutError(f"could not read {_printable(str(path))}: {error}") from error
    finally:
        os.close(handle)
    if len(data) > MAX_MANIFEST_BYTES:
        raise LayoutError(
            f"{_printable(str(path))} is larger than {MAX_MANIFEST_BYTES} bytes; it is not a layout manifest"
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LayoutError(f"{_printable(str(path))} is not UTF-8; save it as UTF-8") from error
