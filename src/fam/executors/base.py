"""Executor interface.

The architectural distinction between communication and decision/execution is
explicit (testbed-architecture.md §13). Everything above this interface is
Matrix transport; everything below it is the decision function, which the
communication substrate knows nothing about.

Two implementations are frozen: ``DeterministicExecutor`` for E0-E3, and an
LLM-backed executor for E4. Only the first exists in this slice.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fam.common.message import ParsedMessage


@runtime_checkable
class Executor(Protocol):
    """Decides what, if anything, to say in response to an event."""

    name: str

    def decide(self, message: ParsedMessage) -> str | None:
        """Return a response body, or ``None`` to stay silent.

        Implementations must be free of Matrix concerns: no room ids, no
        event ids, no transaction ids.
        """
        ...
