"""A run of a command: holds the lock, and records what came of it in the run log.

Failures the user (or the agent) has to act on are :class:`Problem` subclasses
with an :class:`Outcome`; callers branch on the outcome, never on message text.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from . import store
from .ledger import Ledger


class Outcome(StrEnum):
    OK = "ok"
    AULA_LOGIN = "aula_login"  # the saved Aula session is gone: someone has to log in with MitID
    GOOGLE_LOGIN = "google_login"  # Google access is missing or revoked
    BAD_EVENTS = "bad_events"  # the agent's events document was rejected
    ERROR = "error"


class Problem(RuntimeError):
    """A failure with a known cause. Subclasses set ``outcome`` and ``label``."""

    outcome = Outcome.ERROR
    label = "Fejl"


@dataclass
class Run:
    command: str
    summary: str = ""  # set by the caller when the run succeeds


def record(command: str, outcome: Outcome, summary: str) -> None:
    """Add one entry to the run log (takes the lock - don't call it while holding it)."""
    with store.lock():
        _append(command, outcome, summary)


def _append(command: str, outcome: Outcome, summary: str) -> None:
    ledger = Ledger.load()
    ledger.log_run(command, outcome, summary)
    ledger.save()


@contextlib.contextmanager
def run(command: str, *, log: bool = True, wait: bool = True) -> Iterator[Run]:
    """Run ``command`` under the lock and log its outcome (unless ``log`` is false).

    Exceptions propagate unchanged after being logged; :class:`store.Busy` is
    raised without logging when ``wait`` is false and another run holds the lock.
    """
    r = Run(command)
    with store.lock(wait=wait):
        try:
            yield r
        except Exception as exc:
            if log:
                outcome = exc.outcome if isinstance(exc, Problem) else Outcome.ERROR
                label = exc.label if isinstance(exc, Problem) else "Fejl"
                _append(command, outcome, f"{label}: {exc}")
            raise
        if log:
            _append(command, Outcome.OK, r.summary)
