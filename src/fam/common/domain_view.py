"""What one federation domain shows, through an ordinary participant.

A pure data structure, deliberately free of Matrix transport imports so the
comparison logic can be exercised without a homeserver. Collection lives in
:mod:`fam.matrix.rooms`.

The experimental event set is defined narrowly: persisted ``m.room.message``
events whose body is a valid FAM/1 message for this experiment and run.
Everything else in the room — membership, create, power levels, ordinary chat
— is counted as non-experimental and is not compared, because Matrix
housekeeping can legitimately differ in presentation between servers.

Nothing is discarded silently: a FAM/1 message belonging to this run that the
experiment did not generate lands in the experimental set and surfaces as
unexpected.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DomainView:
    domain: str
    observer: str
    request_event_ids: set[str] = field(default_factory=set)
    ack_event_ids: set[str] = field(default_factory=set)
    experimental_event_ids: set[str] = field(default_factory=set)
    non_experimental_events: int = 0
    total_events_seen: int = 0
    membership: list[str] = field(default_factory=list)
    #: event_id -> (kind, sender, sequence_id), for reporting
    detail: dict[str, tuple[str, str, int]] = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "domain": self.domain,
            "observer": self.observer,
            "experimental_events": len(self.experimental_event_ids),
            "requests": len(self.request_event_ids),
            "acks": len(self.ack_event_ids),
            "non_experimental_events": self.non_experimental_events,
            "total_events_seen": self.total_events_seen,
            "membership": self.membership,
        }
