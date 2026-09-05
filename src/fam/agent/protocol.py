"""What counts as a request, and how it correlates.

E0-E3 carry the controlled FAM/1 envelope (experimental-protocol.md §7), whose
`experiment + run + sequence` triple is the logical identity of an interaction
(§9, §13). E4 carries none of that: the human writes ordinary prose in a
standard Matrix client, and there is no envelope to parse.

Rather than teach the runtime two sets of rules, both live here behind one
small interface. The runtime asks the same questions in both cases — is this
event a request for me, what is its logical identity, what transaction id
should the reply carry — and nothing else about the runtime changes. That is
what keeps E4 on the same communication runtime as E0-E3 with only the
decision function replaced (testbed-architecture.md §14.2).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from fam.common.message import parse
from fam.executors.base import ExecutionRequest

CONTROLLED = "controlled"
NATURAL_LANGUAGE = "natural_language"


@dataclass(frozen=True)
class InboundRequest:
    """One request the agent should answer."""

    #: Logical identity, used for exactly-once processing. For the controlled
    #: protocol this is the frozen experiment/run/sequence triple; for natural
    #: language the Matrix event id is the only identity there is.
    correlation_key: tuple
    #: Deterministic transaction id for the reply, so a client-side
    #: retransmission is idempotent (testbed-architecture.md §18).
    response_txn_id: str
    #: What the executor sees. Carries no Matrix concerns.
    execution: ExecutionRequest
    #: Telemetry only; absent for natural language.
    sequence_id: int | None = None


class RequestProtocol(Protocol):
    name: str

    def inbound(
        self, *, event_id: str, sender: str, body: str
    ) -> InboundRequest | None:
        """Return the request this event carries, or ``None`` to ignore it."""
        ...


class ControlledProtocol:
    """FAM/1, used by E0-E3.

    Anything that is not a well-formed FAM/1 REQUEST is ignored: the agent
    shares rooms with ordinary traffic and must not answer it
    (experimental-protocol.md §7).
    """

    name = CONTROLLED

    def inbound(
        self, *, event_id: str, sender: str, body: str
    ) -> InboundRequest | None:
        message = parse(body)
        if message is None or not message.is_request:
            return None
        return InboundRequest(
            correlation_key=message.correlation.key(),
            response_txn_id=message.correlation.txn_id("response"),
            execution=ExecutionRequest(text=body, message=message),
            sequence_id=message.correlation.sequence_id,
        )


class NaturalLanguageProtocol:
    """Ordinary prose, used by E4.

    Every non-empty text message from another participant is a request. There
    is no envelope, so the Matrix ``event_id`` is the logical identity — sound
    here because E4 has no logical interaction spanning several events, and
    ``event_id`` is already the frozen event identity
    (experimental-protocol.md §13).

    Identity is the event, not the text. A human who asks the same question
    twice has asked two questions and gets two answers; deduplicating on
    content would silently drop the second, which is not what exactly-once
    processing means.

    Nothing is filtered by sender beyond excluding the agent itself. In a
    three-party room the human-role participant may also speak, and answering
    it is ordinary behaviour rather than a defect.
    """

    name = NATURAL_LANGUAGE

    def __init__(self, agent_mxid: str) -> None:
        self.agent_mxid = agent_mxid

    def inbound(
        self, *, event_id: str, sender: str, body: str
    ) -> InboundRequest | None:
        if sender == self.agent_mxid:
            return None
        text = body.strip()
        if not text:
            return None
        return InboundRequest(
            correlation_key=(NATURAL_LANGUAGE, event_id, 0),
            response_txn_id=response_txn_for_event(event_id),
            execution=ExecutionRequest(text=text, message=None),
            sequence_id=None,
        )


def response_txn_for_event(event_id: str) -> str:
    """A deterministic reply transaction id derived from the request event.

    Natural language has no sequence number to build one from, so the request
    event id — unique, and already the frozen event identity — is hashed into
    the same ``fam-`` shaped namespace the controlled protocol uses.
    Retransmitting the same reply reuses this value.
    """
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32]
    return f"fam-e4-response-{digest}"


def build(name: str, *, agent_mxid: str) -> RequestProtocol:
    if name == CONTROLLED:
        return ControlledProtocol()
    if name == NATURAL_LANGUAGE:
        return NaturalLanguageProtocol(agent_mxid)
    raise ValueError(
        f"unknown request protocol {name!r}; expected {CONTROLLED!r} or "
        f"{NATURAL_LANGUAGE!r}"
    )
