"""H2: terminal outcomes are immutable; duplicate ACKs are separate evidence.

The contradiction these close: §11 fixed the terminal outcome when the
interaction terminated, while §12 assigned `duplicate_response` from a fact
that can only be known afterwards. Both could not hold.

Everything that builds an ``Interaction`` runs inside a loop: the object holds
an ``asyncio.Future``, and the real code only ever creates one from within a
coroutine.
"""

from __future__ import annotations

import asyncio

import pytest

from fam.common.message import Correlation, build_ack
from fam.common.validity import InteractionOutcome, failure_rate

pytest.importorskip("aiohttp", reason="transport tests run in the toolbox image")

SECOND = 1_000_000_000
CORRELATION = Correlation("E3", "run-1", 1)


def _interaction():
    from fam.participants.human import Interaction

    return Interaction(
        correlation=CORRELATION,
        request_txn_id=CORRELATION.txn_id("request"),
        initiated_monotonic_ns=1000,
    )


def _human_with_pending():
    """A sender holding one in-flight interaction, ready to receive ACKs."""
    from fam.participants.human import HumanParticipant

    human = HumanParticipant(
        homeserver_url="http://example.invalid", user_id="@h:hs-a.test", password="x"
    )
    human.bind_room("!r:hs-a.test")
    item = _interaction()
    human._pending[CORRELATION.key()] = item
    return human, item


def _ack(event_id: str):
    from fam.matrix.client import TimelineEvent

    return TimelineEvent(
        room_id="!r:hs-a.test",
        event_id=event_id,
        sender="@agent:hs-b.test",
        body=build_ack(CORRELATION),
        origin_server_ts=0,
    )


# ------------------------------------------------------------- taxonomy


def test_duplicate_response_is_gone_from_the_terminal_taxonomy():
    values = {o.value for o in InteractionOutcome}
    assert "duplicate_response" not in values
    assert values == {
        "success",
        "timeout",
        "send_error",
        "malformed_response",
        "unexpected_response",
        "runner_error",
        "offline_send",
    }


# ------------------------------------------------------------- counting


def test_normal_success_is_one_ack_and_no_duplicate():
    async def body():
        item = _interaction()
        item.ack_event_ids.append("$a")
        assert item.ack_count == 1
        assert item.duplicate_ack_count == 0
        assert item.duplicate_ack_event_ids == []

    asyncio.run(body())


def test_three_acks_are_one_terminal_and_two_duplicates():
    async def body():
        item = _interaction()
        item.ack_event_ids.extend(["$a", "$b", "$c"])
        assert item.ack_count == 3
        assert item.duplicate_ack_count == 2
        assert item.duplicate_ack_event_ids == ["$b", "$c"]

    asyncio.run(body())


# ------------------------------------------------------------ behaviour


def test_the_same_event_delivered_twice_is_not_a_duplicate():
    """§13: identity is event_id, so redelivery is one event.

    Counting callback invocations instead would manufacture an integrity
    defect out of ordinary gap recovery, which merges timelines by event id.
    """

    async def body():
        human, item = _human_with_pending()
        event = _ack("$same")
        await human._handle(event)
        await human._handle(event)
        assert item.ack_count == 1
        assert item.duplicate_ack_count == 0
        assert human.duplicate_acks() == 0

    asyncio.run(body())


def test_a_distinct_second_ack_is_recorded_without_moving_the_outcome():
    async def body():
        human, item = _human_with_pending()
        await human._handle(_ack("$first"))
        fixed_t3 = item.completed_monotonic_ns
        fixed_response = item.response_event_id

        await human._handle(_ack("$second"))

        assert item.ack_count == 2
        assert item.duplicate_ack_count == 1
        assert item.duplicate_ack_event_ids == ["$second"]
        # Nothing the first ACK fixed may move.
        assert item.completed_monotonic_ns == fixed_t3
        assert item.response_event_id == fixed_response == "$first"
        assert human.duplicate_acks() == 1
        assert human.requests_with_duplicate_acks() == 1

    asyncio.run(body())


def test_a_duplicate_does_not_change_the_terminal_outcome():
    from fam.benchmark.engine import _outcome_of

    async def body():
        human, item = _human_with_pending()
        await human._handle(_ack("$first"))
        assert _outcome_of(item).value == "success"
        await human._handle(_ack("$second"))
        await human._handle(_ack("$third"))
        assert _outcome_of(item).value == "success"
        assert item.duplicate_ack_count == 2

    asyncio.run(body())


def test_a_late_ack_after_timeout_is_not_a_duplicate_after_success():
    """Two different facts. Neither touches the terminal outcome."""
    from fam.benchmark.engine import _outcome_of

    async def body():
        human, item = _human_with_pending()
        item.timed_out = True  # the deadline already fixed the outcome
        await human._handle(_ack("$late"))

        assert item.completed_monotonic_ns is None
        assert item.late_ack_monotonic_ns is not None
        assert item.duplicate_ack_count == 0
        assert _outcome_of(item).value == "timeout"

    asyncio.run(body())


# ---------------------------------------------------------- failure rate


def test_a_success_with_duplicates_stays_a_success_for_the_failure_rate():
    """§12 now reads only the terminal outcome taxonomy."""
    assert failure_rate(["success", "success", "success"]) == 0.0


def test_duplicates_are_reported_separately_from_the_failure_rate():
    from fam.analysis.e3 import RunRecords

    run = RunRecords(
        run_id="r", workload="throughput", topology="local", block_id="b",
        concurrency=8, campaign_id="c", valid=True, invalid_class=None,
        window_start_ns=0, window_end_ns=60 * SECOND,
        records=[
            {"initiated_monotonic_ns": 0, "completed_monotonic_ns": SECOND,
             "outcome": "success", "phase": "window", "duplicate_ack_count": 2},
            {"initiated_monotonic_ns": 0, "completed_monotonic_ns": SECOND,
             "outcome": "success", "phase": "window", "duplicate_ack_count": 0},
        ],
    )
    assert run.failure_rate() == 0.0, "duplicates are not interaction failures"
    assert run.duplicate_ack_observations() == 2
    assert run.requests_with_duplicate_acks() == 1
    assert run.carries_duplicate_evidence() is True


def test_data_predating_the_evidence_fields_is_not_reported_as_zero():
    """Silence is not the same fact as an observed zero."""
    from fam.analysis.e3 import RunRecords

    historical = RunRecords(
        run_id="r3-run", workload="throughput", topology="local", block_id="b",
        concurrency=8, campaign_id="c", valid=True, invalid_class=None,
        window_start_ns=0, window_end_ns=60 * SECOND,
        records=[{"initiated_monotonic_ns": 0, "completed_monotonic_ns": SECOND,
                  "outcome": "success", "phase": "window"}],
    )
    assert historical.carries_duplicate_evidence() is False
