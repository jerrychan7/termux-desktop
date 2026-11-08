"""Run external commands and return what they printed."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CommandResult:
    """The outcome of one external command."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """Return True when the command exited with status 0."""
        return self.returncode == 0


class CommandRunner:
    """Run a command and either show its output or capture it.

    Unit tests replace this class with a stand-in that records the calls and
    returns prepared results, so no real package manager runs during tests.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        capture: bool = False,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        arguments = [str(item) for item in argv]
        if capture:
            completed = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                env=dict(env) if env is not None else None,
                cwd=cwd,
                timeout=timeout,
                check=False,
            )
            return CommandResult(
                argv=tuple(arguments),
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        completed = subprocess.run(
            arguments,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            argv=tuple(arguments),
            returncode=completed.returncode,
            stdout="",
            stderr="",
        )
