"""E2 recovery: limited-timeline handling, merge, dedup, exactly-once."""

from __future__ import annotations

import json
from pathlib import Path

from fam.agent.recovery import RecoveryLedger, Source, compare
from fam.common.validity import InteractionOutcome

SENT = {f"$e{i}" for i in range(1, 101)}


def ledger_with(sync_ids: set[str], history_ids: set[str]) -> RecoveryLedger:
    ledger = RecoveryLedger()
    ledger.limited_timeline = True
    ledger.prev_batch = "t42-0"
    for event_id in sorted(sync_ids):
        ledger.observe(event_id, Source.SYNC)
    if history_ids:
        ledger.note_pagination(pages=2)
    for event_id in sorted(history_ids):
        ledger.observe(event_id, Source.HISTORY)
    return ledger


# ------------------------------------------------------ limited detection


def test_limited_timeline_and_recovery_token_are_recorded():
    ledger = ledger_with({"$a"}, {"$b"})
    assert ledger.limited_timeline is True
    summary = ledger.summary()
    assert summary["limited_timeline"] is True
    assert summary["prev_batch_present"] is True
    assert summary["pagination_invoked"] is True
    assert summary["history_pages_fetched"] == 2


def test_unlimited_timeline_does_not_claim_pagination():
    ledger = RecoveryLedger()
    for event_id in SENT:
        ledger.observe(event_id, Source.SYNC)
    assert ledger.summary()["pagination_invoked"] is False
    assert ledger.from_history == set()


# --------------------------------------------------------- merge and split


def test_sync_and_history_merge_to_the_full_sent_set():
    sync_part = {f"$e{i}" for i in range(91, 101)}
    history_part = SENT - sync_part
    ledger = ledger_with(sync_part, history_part)

    assert ledger.recovered_event_ids == SENT
    assert ledger.from_sync == sync_part
    assert ledger.from_history == history_part
    assert len(ledger.from_sync) + len(ledger.from_history) == 100


def test_first_source_is_retained_when_an_event_arrives_twice():
    """Seen in the truncated sync AND returned again by pagination."""
    ledger = RecoveryLedger()
    ledger.observe("$x", Source.SYNC)
    first_again = ledger.observe("$x", Source.HISTORY)

    assert first_again is False
    assert ledger.from_sync == {"$x"}
    assert ledger.from_history == set()
    assert ledger.duplicate_observation_count == 1


def test_duplicate_transport_observations_are_counted_not_hidden():
    ledger = RecoveryLedger()
    for _ in range(3):
        ledger.observe("$x", Source.HISTORY)
    assert ledger.duplicate_observation_count == 2
    assert ledger.recovered_event_ids == {"$x"}


# ------------------------------------------------------- exactly-once path


def test_duplicate_observation_does_not_become_duplicate_processing():
    ledger = RecoveryLedger()
    ledger.observe("$x", Source.SYNC)
    ledger.observe("$x", Source.HISTORY)

    assert ledger.should_process("$x") is True
    ledger.mark_processed("$x")
    assert ledger.should_process("$x") is False
    assert ledger.processed_count == 1


def test_one_hundred_recovered_requests_process_exactly_once():
    ledger = ledger_with({f"$e{i}" for i in range(91, 101)}, SENT)
    for event_id in sorted(ledger.recovered_event_ids):
        if ledger.should_process(event_id):
            ledger.mark_processed(event_id)
    # Replaying every observation must add no further processing.
    for event_id in sorted(ledger.recovered_event_ids):
        assert ledger.should_process(event_id) is False
    assert ledger.processed_count == 100


# ------------------------------------------------------- exact comparison


def test_exact_equality_passes():
    result = compare(SENT, set(SENT))
    assert result["equal"] is True
    assert result["missing_from_recovery"] == []
    assert result["unexpected_in_recovery"] == []


def test_missing_recovered_event_is_detected():
    result = compare(SENT, SENT - {"$e7"})
    assert result["equal"] is False
    assert result["missing_from_recovery"] == ["$e7"]


def test_unexpected_recovered_event_is_detected():
    result = compare(SENT, SENT | {"$rogue"})
    assert result["equal"] is False
    assert result["unexpected_in_recovery"] == ["$rogue"]


def test_equal_counts_with_different_members_still_fail():
    swapped = (SENT - {"$e1"}) | {"$other"}
    result = compare(SENT, swapped)
    assert result["sent_count"] == result["recovered_count"] == 100
    assert result["equal"] is False
    assert result["missing_from_recovery"] == ["$e1"]
    assert result["unexpected_in_recovery"] == ["$other"]


# ------------------------------------------------- offline-send semantics


def test_offline_send_is_a_distinct_outcome_not_a_timeout():
    """The deliberate absence of a runtime must not read as a failure."""
    assert InteractionOutcome.OFFLINE_SEND.value == "offline_send"
    assert InteractionOutcome.OFFLINE_SEND is not InteractionOutcome.TIMEOUT


# --------------------------------------------- comparison artifact shape


def test_recovery_comparison_artifact_carries_provenance(tmp_path: Path):
    ledger = ledger_with({f"$e{i}" for i in range(91, 101)}, SENT)
    payload = {
        "analysis_spec_version": "1.1-dev",
        "analysis_code_commit": "task-03-working-tree",
        "protocol_git_commit": "cafe1234",
        "source_run_id": "e2-test-01",
        "source_raw_digests": {"agent_telemetry_stream": "0" * 64},
        "run_id": "e2-test-01",
        "room_id": "!room",
        "offline_request_count": 100,
        "timeline_limit": 10,
        "sync_limited": ledger.limited_timeline,
        "sent_event_ids": sorted(SENT),
        "recovered_event_ids": sorted(ledger.recovered_event_ids),
        "missing_from_recovery": [],
        "unexpected_in_recovery": [],
        "pagination_invoked": ledger.pagination_invoked,
        "history_pages_fetched": ledger.history_pages_fetched,
        "duplicate_observation_count": ledger.duplicate_observation_count,
        "same_agent_identity": True,
        "checkpoint_resumed": True,
        "overall_result": "PASS",
    }
    path = tmp_path / "e2-test-01.recovery-comparison.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    for required in (
        "analysis_spec_version",
        "analysis_code_commit",
        "protocol_git_commit",
        "source_run_id",
        "source_raw_digests",
        "timeline_limit",
        "sync_limited",
        "sent_event_ids",
        "recovered_event_ids",
        "missing_from_recovery",
        "unexpected_in_recovery",
        "pagination_invoked",
        "checkpoint_resumed",
        "overall_result",
    ):
        assert required in loaded, required
    assert len(loaded["recovered_event_ids"]) == 100
    assert loaded["timeline_limit"] < loaded["offline_request_count"]
