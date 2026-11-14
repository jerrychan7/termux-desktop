"""Decide whether a recorded session still owns a live process group."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..state import SessionState

if TYPE_CHECKING:
    from .runtime import SessionRuntime


VERIFICATION_RUNNING = "running"
VERIFICATION_STALE = "stale"
VERIFICATION_UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class Verification:
    """Whether a recorded session still belongs to a live process group."""

    state: str
    detail: str


def verify_session(state: SessionState, runtime: SessionRuntime) -> Verification:
    """Determine whether the recorded session still owns a live process group."""
    current_boot_id = runtime.read_boot_id()
    if current_boot_id != state.boot_id:
        return Verification(
            VERIFICATION_STALE,
            f"the state file was written during an earlier boot ({state.boot_id})",
        )

    members = runtime.group_members(state.pgid)
    if not members:
        return Verification(
            VERIFICATION_STALE,
            f"process group {state.pgid} has no live processes",
        )

    if runtime.session_in_group(
        state.pgid,
        state.session_id,
        state.pid,
        state.leader_starttime,
    ):
        return Verification(
            VERIFICATION_RUNNING,
            f"process group {state.pgid} belongs to session {state.session_id}",
        )

    return Verification(
        VERIFICATION_UNVERIFIABLE,
        f"process group {state.pgid} has processes, but none of them carry "
        f"session id {state.session_id}",
    )
