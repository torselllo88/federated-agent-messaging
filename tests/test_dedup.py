"""Exactly-once logical processing.

    one logical request -> one processing operation -> one logical ACK
"""

from __future__ import annotations

from fam.common.dedup import Decision, ProcessedRegistry
from fam.common.message import Correlation


def key(sequence: int) -> tuple[str, str, int]:
    return Correlation("E0", "run-1", sequence).key()


def test_first_sight_of_a_request_is_processed():
    registry = ProcessedRegistry()
    assert registry.decide(event_id="$a", correlation_key=key(1)) is Decision.PROCESS


def test_same_event_delivered_twice_is_one_logical_request():
    """Sync can redeliver; that must not produce a second ACK."""
    registry = ProcessedRegistry()
    registry.commit(event_id="$a", correlation_key=key(1))
    assert (
        registry.decide(event_id="$a", correlation_key=key(1))
        is Decision.SKIP_DUPLICATE
    )


def test_distinct_events_carrying_the_same_correlation_are_one_logical_request():
    """A resend under a new event id is still the same logical request."""
    registry = ProcessedRegistry()
    registry.commit(event_id="$a", correlation_key=key(1))
    assert (
        registry.decide(event_id="$b", correlation_key=key(1))
        is Decision.SKIP_DUPLICATE
    )


def test_same_event_id_with_a_different_correlation_is_still_a_duplicate_event():
    registry = ProcessedRegistry()
    registry.commit(event_id="$a", correlation_key=key(1))
    assert (
        registry.decide(event_id="$a", correlation_key=key(2))
        is Decision.SKIP_DUPLICATE
    )


def test_different_requests_are_processed_independently():
    registry = ProcessedRegistry()
    for sequence in range(1, 21):
        assert (
            registry.decide(event_id=f"$e{sequence}", correlation_key=key(sequence))
            is Decision.PROCESS
        )
        registry.commit(event_id=f"$e{sequence}", correlation_key=key(sequence))
    assert registry.processed_count == 20


def test_replaying_a_whole_run_produces_no_additional_processing():
    registry = ProcessedRegistry()
    for sequence in range(1, 41):
        registry.commit(event_id=f"$e{sequence}", correlation_key=key(sequence))
    before = registry.processed_count
    for sequence in range(1, 41):
        assert (
            registry.decide(event_id=f"$e{sequence}", correlation_key=key(sequence))
            is Decision.SKIP_DUPLICATE
        )
    assert registry.processed_count == before == 40
