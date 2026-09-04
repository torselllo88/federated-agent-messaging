"""Exactly-once logical processing.

The agent must establish, after the fact::

    one logical request -> one processing operation -> one logical ACK

Two identities are tracked, because they answer different questions
(experimental-protocol.md §13). Matrix ``event_id`` is event identity: the
same event delivered twice by sync is one event. The correlation triple is
application-level identity: two distinct events carrying the same logical
request are still one logical request.

Kept free of Matrix imports so it can be exercised without a homeserver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    PROCESS = "processed"
    SKIP_DUPLICATE = "skipped_duplicate"
    SKIP_OWN_EVENT = "skipped_own_event"
    SKIP_NOT_REQUEST = "skipped_not_request"


@dataclass
class ProcessedRegistry:
    """Remembers what has already been processed, by both identities."""

    logical: set[tuple[str, str, int]] = field(default_factory=set)
    events: set[str] = field(default_factory=set)

    def decide(
        self, *, event_id: str, correlation_key: tuple[str, str, int]
    ) -> Decision:
        if event_id in self.events or correlation_key in self.logical:
            return Decision.SKIP_DUPLICATE
        return Decision.PROCESS

    def commit(self, *, event_id: str, correlation_key: tuple[str, str, int]) -> None:
        self.events.add(event_id)
        self.logical.add(correlation_key)

    @property
    def processed_count(self) -> int:
        return len(self.logical)
