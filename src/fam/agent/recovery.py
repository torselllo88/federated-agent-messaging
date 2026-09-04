"""Gap recovery: merging a limited sync timeline with paginated history.

Pure bookkeeping, free of Matrix transport imports so it can be exercised
without a homeserver. The transport calls live in
:class:`fam.matrix.client.MatrixParticipant`; the decision logic lives here.

Correctness for E2 is event-set equality plus exactly-once logical processing.
No ordering property is claimed or required: Matrix room history is an event
graph, not a globally ordered queue (testbed-architecture.md §19, §20).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Source(str, Enum):
    """Where an experimental request was first observed."""

    SYNC = "sync"
    HISTORY = "history"


@dataclass
class Observation:
    event_id: str
    first_source: Source
    times_observed: int = 1

    @property
    def duplicate_observation(self) -> bool:
        return self.times_observed > 1


@dataclass
class RecoveryLedger:
    """Tracks what the restarted runtime saw, and by which path.

    An event may legitimately arrive through more than one path — present in
    the truncated sync timeline *and* returned again by history pagination.
    That is a duplicate transport observation, which is recorded rather than
    hidden, and must not become duplicate logical processing.
    """

    observations: dict[str, Observation] = field(default_factory=dict)
    processed: set[str] = field(default_factory=set)
    limited_timeline: bool = False
    prev_batch: str | None = None
    pagination_invoked: bool = False
    history_pages_fetched: int = 0

    # ----------------------------------------------------------- observation

    def observe(self, event_id: str, source: Source) -> bool:
        """Record an observation. Returns True if this is the first sighting."""
        existing = self.observations.get(event_id)
        if existing is None:
            self.observations[event_id] = Observation(event_id, source)
            return True
        existing.times_observed += 1
        return False

    def note_pagination(self, pages: int) -> None:
        self.pagination_invoked = True
        self.history_pages_fetched += pages

    # -------------------------------------------------------------- querying

    @property
    def recovered_event_ids(self) -> set[str]:
        return set(self.observations)

    @property
    def from_sync(self) -> set[str]:
        return {
            key
            for key, item in self.observations.items()
            if item.first_source is Source.SYNC
        }

    @property
    def from_history(self) -> set[str]:
        return {
            key
            for key, item in self.observations.items()
            if item.first_source is Source.HISTORY
        }

    @property
    def duplicate_observation_count(self) -> int:
        return sum(
            item.times_observed - 1
            for item in self.observations.values()
            if item.times_observed > 1
        )

    # ------------------------------------------------------------ processing

    def should_process(self, event_id: str) -> bool:
        """Exactly-once: an event already processed is never processed again."""
        return event_id not in self.processed

    def mark_processed(self, event_id: str) -> None:
        self.processed.add(event_id)

    @property
    def processed_count(self) -> int:
        return len(self.processed)

    # --------------------------------------------------------------- summary

    def summary(self) -> dict:
        return {
            "limited_timeline": self.limited_timeline,
            "prev_batch_present": self.prev_batch is not None,
            "pagination_invoked": self.pagination_invoked,
            "history_pages_fetched": self.history_pages_fetched,
            "recovered_total": len(self.observations),
            "recovered_from_sync": len(self.from_sync),
            "recovered_from_history": len(self.from_history),
            "duplicate_observations": self.duplicate_observation_count,
            "logically_processed": self.processed_count,
        }


def compare(sent: set[str], recovered: set[str]) -> dict:
    """Exact set comparison. Counts are reported but never substitute for it."""
    missing = sent - recovered
    unexpected = recovered - sent
    return {
        "sent_count": len(sent),
        "recovered_count": len(recovered),
        "missing_from_recovery": sorted(missing),
        "unexpected_in_recovery": sorted(unexpected),
        "equal": not missing and not unexpected,
    }
