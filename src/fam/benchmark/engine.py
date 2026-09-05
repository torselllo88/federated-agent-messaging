"""The E3 benchmark engine: one implementation, two topologies.

experimental-protocol.md §27 requires the same runner, agent, executor, sync
loop, message format, timeout, schema, room version and SDK in both
conditions. That requirement is met here structurally rather than by
discipline: there is exactly one engine, and the only thing a caller may vary
is which :class:`~fam.benchmark.topology.BenchmarkTopology` it receives.

Two implementation notes that matter for the validity of the numbers:

*Records are buffered, not streamed.* ``JsonlStream`` fsyncs every record.
Writing inside the measurement loop would put a synchronous disk flush on the
completion path of every interaction, at a rate that grows with throughput —
a self-inflicted confound that would penalise whichever topology is faster.
Records are accumulated in memory and written once the run has ended.

*The workload never pauses.* A throughput run is one continuous closed loop
(§22). Warm-up is not drained, concurrency never falls, and the measurement
window is a pair of timestamps taken across a loop that does not notice them.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from fam.common.frozen import (
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E3_BODY_BYTES,
    E3_DRAIN_SECONDS,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_WARMUP_SECONDS,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
)
from fam.common.message import Correlation
from fam.common.validity import InteractionOutcome
from fam.instrumentation.streams import benchmark_record, monotonic_ns

if TYPE_CHECKING:  # pragma: no cover
    # Only ever used as annotations. Importing the participant eagerly would
    # drag the whole Matrix transport stack in behind it, which makes the
    # outcome and phase rules — the parts most worth testing — impossible to
    # exercise without a live client library installed.
    from fam.participants.human import HumanParticipant, Interaction

EXPERIMENT = "E3"

#: Phase labels, experimental-protocol.md §22. Latency runs use the first two
#: of their own pair (``warmup``/``measured``); throughput runs use these.
PHASE_WARMUP = "warmup"
PHASE_WINDOW = "window"
PHASE_DRAIN = "drain"
PHASE_MEASURED = "measured"


@dataclass
class RunConfig:
    """Everything one benchmark run needs, and nothing topology-specific."""

    run_id: str
    workload: str
    block_id: str
    within_block_order: int
    topology_name: str
    receiver_role: str
    sender: str
    room_id: str
    concurrency: int
    body_bytes: int = E3_BODY_BYTES
    timeout_seconds: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS
    warmup_interactions: int = E3_LATENCY_WARMUP_INTERACTIONS
    measured_interactions: int = E3_LATENCY_MEASURED_INTERACTIONS
    warmup_seconds: float = E3_WARMUP_SECONDS
    measurement_seconds: float = E3_MEASUREMENT_SECONDS
    drain_seconds: float = E3_DRAIN_SECONDS


@dataclass
class WorkloadResult:
    """Raw execution facts. Contains no metric and no classification."""

    records: list[dict[str, Any]] = field(default_factory=list)
    window_start_ns: int | None = None
    window_end_ns: int | None = None
    drain_end_ns: int | None = None
    loop_start_ns: int | None = None
    initiated: int = 0
    warmup_initiated: int = 0
    rate_limited_sends: int = 0
    send_errors: int = 0
    rate_limit_errcodes: list[str] = field(default_factory=list)
    outstanding_at_window_start: int = 0
    outstanding_at_window_end: int = 0

    @property
    def measured_records(self) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("phase") != PHASE_WARMUP]


def _outcome_of(interaction: "Interaction") -> InteractionOutcome:
    """§11: exactly one outcome, decided when the interaction terminated.

    ``timed_out`` is checked before the completion timestamp. An ACK that
    arrived after the deadline is preserved on the interaction, but it cannot
    turn a timeout into a success — that would record RTTs longer than the
    timeout that ended them, and would do so most often in whichever
    condition is slowest.
    """
    if interaction.send_errcode:
        return InteractionOutcome.SEND_ERROR
    if interaction.timed_out or interaction.completed_monotonic_ns is None:
        return InteractionOutcome.TIMEOUT
    return InteractionOutcome.SUCCESS


def _record(
    config: RunConfig,
    interaction: "Interaction",
    *,
    phase: str,
    window_start_ns: int | None,
    window_end_ns: int | None,
) -> dict[str, Any]:
    correlation = interaction.correlation
    return benchmark_record(
        workload=config.workload,
        block_id=config.block_id,
        within_block_order=config.within_block_order,
        concurrency=config.concurrency,
        message_body_bytes=config.body_bytes,
        phase=phase,
        window_start_ns=window_start_ns,
        window_end_ns=window_end_ns,
        live_recovery_episode=interaction.recovery_episode,
        send_errcode=interaction.send_errcode,
        late_ack_monotonic_ns=interaction.late_ack_monotonic_ns,
        experiment=EXPERIMENT,
        topology=config.topology_name,
        run_id=config.run_id,
        sequence_id=correlation.sequence_id,
        run_phase=phase,
        request_class=config.workload,
        room_id=config.room_id,
        sender=config.sender,
        receiver_role=config.receiver_role,
        request_txn_id=interaction.request_txn_id,
        request_event_id=interaction.request_event_id,
        response_txn_id=correlation.txn_id("response"),
        response_event_id=interaction.response_event_id,
        initiated_monotonic_ns=interaction.initiated_monotonic_ns,
        completed_monotonic_ns=interaction.completed_monotonic_ns,
        outcome=_outcome_of(interaction).value,
        note=interaction.send_error,
    )


# ------------------------------------------------------- Workload A: latency


async def run_latency_workload(
    *, human: "HumanParticipant", config: RunConfig
) -> WorkloadResult:
    """Sequential RTT workload, experimental-protocol.md §21.

    ``max_in_flight = 1``: a request is initiated only after the previous one
    succeeded or timed out, which ``await`` gives us for free. Warm-up runs
    first and is fully drained before measurement begins — §21 requires it
    explicitly, and unlike Workload B nothing here depends on the loop
    staying continuous.
    """
    result = WorkloadResult()
    result.loop_start_ns = monotonic_ns()
    sequence = itertools.count(1)

    for _ in range(config.warmup_interactions):
        correlation = Correlation(EXPERIMENT, config.run_id, next(sequence))
        interaction = await human.request(
            correlation,
            body_bytes=config.body_bytes,
            timeout=config.timeout_seconds,
        )
        result.initiated += 1
        result.warmup_initiated += 1
        _tally_send(result, interaction)
        result.records.append(
            _record(
                config,
                interaction,
                phase=PHASE_WARMUP,
                window_start_ns=None,
                window_end_ns=None,
            )
        )

    # §21: every warm-up interaction has completed or timed out, and nothing
    # is left in flight. With max_in_flight = 1 that is already true when the
    # loop above returns; asserted rather than assumed.
    outstanding = human.outstanding_count()
    if outstanding:
        raise RuntimeError(
            f"{outstanding} warm-up interactions still in flight at the start "
            "of the measured phase"
        )
    result.window_start_ns = monotonic_ns()

    for _ in range(config.measured_interactions):
        correlation = Correlation(EXPERIMENT, config.run_id, next(sequence))
        interaction = await human.request(
            correlation,
            body_bytes=config.body_bytes,
            timeout=config.timeout_seconds,
        )
        result.initiated += 1
        _tally_send(result, interaction)
        result.records.append(
            _record(
                config,
                interaction,
                phase=PHASE_MEASURED,
                window_start_ns=None,
                window_end_ns=None,
            )
        )

    result.window_end_ns = monotonic_ns()
    result.drain_end_ns = result.window_end_ns
    return result


# --------------------------------------------------- Workload B: throughput


async def run_throughput_workload(
    *,
    human: "HumanParticipant",
    config: RunConfig,
    on_tick: Callable[[str], None] | None = None,
) -> WorkloadResult:
    """Bounded-concurrency closed loop, experimental-protocol.md §22.

    ``C`` logical client slots each run send → await → send with no pause. The
    window is opened and closed by wall time on the monotonic clock; the loop
    itself is never drained, restarted or throttled at those boundaries, so at
    ``window_start_ns`` the system is already at concurrency ``C``.

    The only thing that happens at ``window_end_ns`` is that slots stop
    initiating. Whatever is outstanding then completes or times out during the
    drain, and is accounted for — but not counted toward the numerator.
    """
    result = WorkloadResult()
    sequence = itertools.count(1)
    initiated_before_window: list["Interaction"] = []
    pending_at_window_end: list["Interaction"] = []

    result.loop_start_ns = monotonic_ns()
    window_start_ns = result.loop_start_ns + int(config.warmup_seconds * 1e9)
    window_end_ns = window_start_ns + int(config.measurement_seconds * 1e9)
    drain_end_ns = window_end_ns + int(config.drain_seconds * 1e9)
    result.window_start_ns = window_start_ns
    result.window_end_ns = window_end_ns
    result.drain_end_ns = drain_end_ns

    completed: list[tuple["Interaction", int]] = []

    async def slot(slot_index: int) -> None:
        while monotonic_ns() < window_end_ns:
            correlation = Correlation(EXPERIMENT, config.run_id, next(sequence))
            initiated_ns = monotonic_ns()
            interaction = await human.request(
                correlation,
                body_bytes=config.body_bytes,
                timeout=config.timeout_seconds,
            )
            completed.append((interaction, initiated_ns))

    await asyncio.gather(*(slot(index) for index in range(config.concurrency)))

    if on_tick is not None:
        on_tick("closed loop stopped initiating; drain accounting")

    # Every interaction is already resolved: each slot awaited its own last
    # one under the ordinary per-interaction timeout, which is the drain.
    for interaction, initiated_ns in completed:
        result.initiated += 1
        _tally_send(result, interaction)

        finished = interaction.completed_monotonic_ns
        if finished is None:
            # Never completed. It belongs to the period it was initiated in;
            # the analysis reads the raw timestamps, not this label.
            phase = PHASE_WARMUP if initiated_ns < window_start_ns else PHASE_WINDOW
        elif finished < window_start_ns:
            phase = PHASE_WARMUP
        elif finished < window_end_ns:
            phase = PHASE_WINDOW
        else:
            phase = PHASE_DRAIN

        if phase == PHASE_WARMUP:
            result.warmup_initiated += 1
        if initiated_ns < window_start_ns and (
            finished is None or finished >= window_start_ns
        ):
            initiated_before_window.append(interaction)
        if initiated_ns < window_end_ns and (
            finished is None or finished >= window_end_ns
        ):
            pending_at_window_end.append(interaction)

        result.records.append(
            _record(
                config,
                interaction,
                phase=phase,
                window_start_ns=window_start_ns,
                window_end_ns=window_end_ns,
            )
        )

    result.outstanding_at_window_start = len(initiated_before_window)
    result.outstanding_at_window_end = len(pending_at_window_end)
    return result


def _tally_send(result: WorkloadResult, interaction: "Interaction") -> None:
    if not interaction.send_errcode:
        return
    result.send_errors += 1
    result.rate_limit_errcodes.append(interaction.send_errcode)
    if interaction.rate_limited:
        result.rate_limited_sends += 1


async def run_workload(
    *, human: "HumanParticipant", config: RunConfig
) -> WorkloadResult:
    if config.workload == E3_WORKLOAD_LATENCY:
        return await run_latency_workload(human=human, config=config)
    if config.workload == E3_WORKLOAD_THROUGHPUT:
        return await run_throughput_workload(human=human, config=config)
    raise ValueError(f"unknown E3 workload {config.workload!r}")
