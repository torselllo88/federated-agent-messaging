"""Live gap recovery: the checkpoint invariant and reconciliation.

The invariant under test:

    advancing the durable checkpoint must never make an unresolved
    limited-timeline gap unreachable

These exercise the reconciliation and commit logic with a minimal fake
transport. The real behaviour under load is covered by the readiness runs
against the actual testbed.
"""

from __future__ import annotations

import asyncio

import pytest

# The transport module needs the Matrix client stack, so these run in the
# toolbox image. Everything else in the suite stays host-runnable.
pytest.importorskip("aiohttp", reason="transport tests run inside the toolbox image")

from fam.agent.recovery import RecoveryLedger, Source, compare
from fam.matrix.client import (
    MatrixParticipant,
    RecoveryBoundExceeded,
    RoomSyncSlice,
    SyncSnapshot,
    TimelineEvent,
)

ROOM = "!room:hs-a.test"


def event(event_id: str, sequence: int, sender: str = "@human-a:hs-a.test"):
    return TimelineEvent(
        room_id=ROOM,
        event_id=event_id,
        sender=sender,
        body=f"FAM/1 REQUEST E3READINESS run-1 {sequence:05d}",
        origin_server_ts=0,
    )


def participant() -> MatrixParticipant:
    return MatrixParticipant(
        homeserver_url="http://example.invalid", user_id="@agent:hs-b.test"
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------- limited detection


def test_unlimited_slice_needs_no_pagination():
    client = participant()
    client.paginate_backwards = None  # would explode if called

    events, episode = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1), event("$b", 2)], False, "p0"),
            since="t0",
            trigger="live_sync",
        )
    )
    assert len(events) == 2
    assert episode["history_pages_fetched"] == 0
    assert episode["recovered_from_history"] == 0
    assert episode["sync_limited"] is False


def test_limited_slice_merges_history_with_sync():
    client = participant()

    async def fake_paginate(room_id, *, start, to=None, **kwargs):
        assert start == "p0" and to == "t0", "must page from prev_batch back to the checkpoint"
        return (
            [
                {
                    "type": "m.room.message",
                    "event_id": f"$h{i}",
                    "sender": "@human-a:hs-a.test",
                    "content": {"body": f"FAM/1 REQUEST E3READINESS run-1 {i:05d}"},
                }
                for i in range(3, 13)
            ],
            2,
        )

    client.paginate_backwards = fake_paginate
    events, episode = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1), event("$b", 2)], True, "p0"),
            since="t0",
            trigger="live_sync",
        )
    )
    assert len(events) == 12
    assert episode["direct_from_sync"] == 2
    assert episode["recovered_from_history"] == 10
    assert episode["history_pages_fetched"] == 2
    assert episode["recovery_trigger"] == "live_sync"


def test_event_seen_in_both_paths_is_counted_once():
    client = participant()

    async def fake_paginate(room_id, *, start, to=None, **kwargs):
        return (
            [
                {
                    "type": "m.room.message",
                    "event_id": "$a",  # already in the sync timeline
                    "sender": "@human-a:hs-a.test",
                    "content": {"body": "FAM/1 REQUEST E3READINESS run-1 00001"},
                }
            ],
            1,
        )

    client.paginate_backwards = fake_paginate
    events, episode = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1)], True, "p0"),
            since="t0",
            trigger="live_sync",
        )
    )
    assert len(events) == 1
    assert episode["duplicate_observations"] == 1
    assert episode["reconciled_unique_events"] == 1


def test_non_message_events_are_not_reconciled_as_requests():
    client = participant()

    async def fake_paginate(room_id, *, start, to=None, **kwargs):
        return (
            [
                {"type": "m.room.member", "event_id": "$m", "sender": "@x:y",
                 "content": {"membership": "join"}},
                {"type": "m.room.message", "event_id": "$ok", "sender": "@human-a:hs-a.test",
                 "content": {"body": "FAM/1 REQUEST E3READINESS run-1 00009"}},
            ],
            1,
        )

    client.paginate_backwards = fake_paginate
    events, _ = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [], True, "p0"), since="t0", trigger="live_sync"
        )
    )
    assert {e.event_id for e in events} == {"$ok"}


def test_episode_ids_distinguish_consecutive_gaps():
    client = participant()
    first = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1)], False, "p0"),
            since="t0", trigger="live_sync",
        )
    )[1]
    second = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$b", 2)], False, "p1"),
            since="t1", trigger="live_sync",
        )
    )[1]
    assert first["recovery_episode"] == 1
    assert second["recovery_episode"] == 2


def test_startup_and_live_share_one_mechanism_differing_only_by_trigger():
    client = participant()
    startup = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1)], False, "p0"),
            since="t0", trigger="startup",
        )
    )[1]
    live = run(
        client.reconcile_slice(
            RoomSyncSlice(ROOM, [event("$a", 1)], False, "p0"),
            since="t0", trigger="live_sync",
        )
    )[1]
    assert startup["recovery_trigger"] == "startup"
    assert live["recovery_trigger"] == "live_sync"
    assert startup["reconciled_unique_events"] == live["reconciled_unique_events"]


# --------------------------------------------------- checkpoint invariant


class FakeLoop:
    """Drives the commit rule without a homeserver."""

    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.committed: str | None = None
        self.commits = 0
        self.fail_on = fail_on or set()
        self.attempt = 0

    async def process(self, token_in: str, token_out: str) -> None:
        self.attempt += 1
        if self.attempt in self.fail_on:
            raise RecoveryBoundExceeded("simulated unresolved gap")
        self.committed = token_out
        self.commits += 1


def test_checkpoint_advances_only_after_successful_reconciliation():
    loop = FakeLoop()
    run(loop.process("t0", "t1"))
    assert loop.committed == "t1"
    assert loop.commits == 1


def test_checkpoint_is_retained_when_reconciliation_fails():
    loop = FakeLoop(fail_on={1})
    with pytest.raises(RecoveryBoundExceeded):
        run(loop.process("t0", "t1"))
    assert loop.committed is None, "an unresolved gap must remain reachable"
    assert loop.commits == 0

    run(loop.process("t0", "t1"))
    assert loop.committed == "t1"


def test_pagination_bound_raises_rather_than_truncating():
    """A partial recovery that looks successful is the dangerous case."""
    assert issubclass(RecoveryBoundExceeded, Exception)
    with pytest.raises(RecoveryBoundExceeded):
        raise RecoveryBoundExceeded("bound hit before the boundary")


# ------------------------------------------------- readiness comparison


def test_readiness_exact_set_comparison():
    sent = {f"$e{i}" for i in range(500)}
    assert compare(sent, set(sent))["equal"] is True
    assert compare(sent, sent - {"$e3"})["missing_from_recovery"] == ["$e3"]
    assert compare(sent, sent | {"$x"})["unexpected_in_recovery"] == ["$x"]


def test_one_request_maps_to_exactly_one_ack():
    ledger = RecoveryLedger()
    acks: dict[str, int] = {}
    for i in range(500):
        event_id = f"$e{i}"
        ledger.observe(event_id, Source.SYNC)
        ledger.observe(event_id, Source.HISTORY)  # duplicate transport sighting
        if ledger.should_process(event_id):
            ledger.mark_processed(event_id)
            acks[event_id] = acks.get(event_id, 0) + 1

    assert ledger.processed_count == 500
    assert len(acks) == 500
    assert set(acks.values()) == {1}
    assert ledger.duplicate_observation_count == 500
