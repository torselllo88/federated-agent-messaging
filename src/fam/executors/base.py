"""Executor interface.

The architectural distinction between communication and decision/execution is
explicit (testbed-architecture.md §13). Everything above this interface is
Matrix transport; everything below it is the decision function, which the
communication substrate knows nothing about.

Two implementations are frozen: ``DeterministicExecutor`` for E0-E3, and
``LLMExecutor`` for E4. The runtime treats them identically — it hands over an
:class:`ExecutionRequest` and receives a response body or ``None``. It never
asks which executor it is holding, which is exactly the substitution E4 exists
to demonstrate (testbed-architecture.md §14.2).

``decide`` may be synchronous or asynchronous. E0-E3 need a cheap, stable
local decision; E4 needs a network call to a provider. Allowing both keeps one
interface for both without forcing the deterministic path through machinery it
does not need.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from fam.common.message import ParsedMessage


@dataclass(frozen=True)
class ExecutionRequest:
    """What the decision function sees.

    Deliberately free of Matrix concerns: no room ids, no event ids, no
    transaction ids. ``text`` is the request as the executor should read it.

    ``message`` carries the parsed FAM/1 envelope when the controlled protocol
    is in use, and is ``None`` for E4, where the human writes ordinary prose
    and there is no correlation envelope to parse. An executor that needs the
    envelope reads this field; one that does not, ignores it.
    """

    text: str
    message: ParsedMessage | None = None


@runtime_checkable
class Executor(Protocol):
    """Decides what, if anything, to say in response to a request."""

    name: str

    def decide(self, request: ExecutionRequest) -> Any:
        """Return a response body, ``None`` to stay silent, or an awaitable.

        Implementations must be free of Matrix concerns.
        """
        ...


async def decide(executor: Executor, request: ExecutionRequest) -> str | None:
    """Invoke an executor without caring whether it is synchronous.

    The runtime calls this rather than ``executor.decide`` directly, so that
    replacing the deterministic executor with the LLM-backed one changes
    nothing above the interface.
    """
    result = executor.decide(request)
    if inspect.isawaitable(result):
        result = await result
    return result
