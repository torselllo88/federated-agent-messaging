"""E3 benchmark: schedule, estimator, bootstrap and instrumentation discipline.

These test the parts that decide what the numbers mean. A defect in the run
order, the window estimator or the resampling unit does not crash anything —
it quietly produces a plausible wrong answer, which is the failure mode this
file exists to prevent.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from fam.analysis.e3 import (
    DEFAULT_BOOTSTRAP_SEED,
    PairedBlock,
    RunRecords,
    incomplete_blocks,
    pair_blocks,
    paired_bootstrap_latency,
    paired_bootstrap_throughput,
    percentile,
)
from fam.benchmark.schedule import (
    CampaignState,
    ScheduledRun,
    campaign_id,
    campaign_parameters,
    fingerprint,
    generate_campaign_schedule,
    generate_workload_schedule,
    pending,
)
from fam.benchmark.topology import FEDERATED, LOCAL, topology
from fam.common.frozen import (
    E3_BODY_BYTES,
    E3_CONCURRENCY_LEVELS,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_PAIRED_BLOCKS,
)
from fam.common.message import (
    Correlation,
    MessageFormatError,
    assert_body_length,
    build_ack,
    build_request,
)

SECOND = 1_000_000_000


# ------------------------------------------------------------------ frozen


def test_frozen_e3_parameters_match_the_protocol():
    assert E3_BODY_BYTES == 256
    assert E3_LATENCY_WARMUP_INTERACTIONS == 50
    assert E3_LATENCY_MEASURED_INTERACTIONS == 500
    assert E3_CONCURRENCY_LEVELS == (8, 32)
    assert 1 not in E3_CONCURRENCY_LEVELS, "C=1 is Workload A, not a throughput level"
    assert E3_MEASUREMENT_SECONDS == 60
    assert E3_PAIRED_BLOCKS == 20


def test_request_and_ack_bodies_are_exactly_256_bytes():
    correlation = Correlation("E3", "run-1", 7)
    request = build_request(correlation, body_bytes=E3_BODY_BYTES)
    ack = build_ack(correlation, body_bytes=E3_BODY_BYTES)
    assert len(request.encode("utf-8")) == 256
    assert len(ack.encode("utf-8")) == 256
    assert_body_length(request)
    assert_body_length(ack)


def test_a_body_of_the_wrong_size_is_a_defect_not_a_tolerance():
    with pytest.raises(MessageFormatError):
        assert_body_length("FAM/1 REQUEST E3 run-1 00007")


# ---------------------------------------------------------------- schedule


def test_schedule_is_deterministic_for_a_seed():
    assert generate_campaign_schedule(seed=7) == generate_campaign_schedule(seed=7)


def test_a_different_seed_changes_the_order():
    first = [r.topology for r in generate_campaign_schedule(seed=7)]
    other = [r.topology for r in generate_campaign_schedule(seed=8)]
    assert first != other


def test_campaign_has_120_runs_at_the_frozen_replication():
    runs = generate_campaign_schedule(seed=7)
    assert len(runs) == 120
    latency = [r for r in runs if r.workload == "latency"]
    assert len(latency) == 40
    for level in E3_CONCURRENCY_LEVELS:
        assert len([r for r in runs if r.concurrency == level]) == 40


def test_every_block_pairs_one_local_with_one_federated():
    blocks: dict[str, list[str]] = {}
    for run in generate_campaign_schedule(seed=7):
        blocks.setdefault(run.block_id, []).append(run.topology)
    assert blocks
    for topologies in blocks.values():
        assert sorted(topologies) == ["federated", "local"]


def test_order_is_counterbalanced_within_each_workload():
    for workload, concurrency in (("latency", 1), ("throughput", 8), ("throughput", 32)):
        runs = generate_workload_schedule(
            workload=workload, concurrency=concurrency, seed=7
        )
        leaders = [r.topology for r in runs if r.within_block_order == 1]
        assert leaders.count("local") == leaders.count("federated") == 10


def test_all_local_runs_are_not_executed_before_all_federated_runs():
    """The specific mistake §13 forbids: blocking by topology, not by pair."""
    order = [r.topology for r in generate_workload_schedule(
        workload="latency", concurrency=1, seed=7
    )]
    first_federated = order.index("federated")
    last_local = len(order) - 1 - order[::-1].index("local")
    assert first_federated < last_local


def test_adding_a_concurrency_level_does_not_reshuffle_another():
    eight = generate_workload_schedule(workload="throughput", concurrency=8, seed=7)
    again = generate_workload_schedule(workload="throughput", concurrency=8, seed=7)
    thirty_two = generate_workload_schedule(
        workload="throughput", concurrency=32, seed=7
    )
    assert eight == again
    assert [r.topology for r in eight] != [r.topology for r in thirty_two]


# ------------------------------------------------------------- fingerprint


def _parameters(**overrides):
    base = dict(
        seed=7,
        sync_timeline_limit=500,
        sync_timeout_ms=30_000,
        protocol_git_commit="abc123",
        config_hashes={"A": "hashA", "B": "hashB"},
        rate_limits={"A": {"rc_message": {"per_second": 1000}}},
    )
    base.update(overrides)
    return campaign_parameters(**base)


def test_fingerprint_is_stable_for_identical_parameters():
    assert fingerprint(_parameters()) == fingerprint(_parameters())


@pytest.mark.parametrize(
    "override",
    [
        {"seed": 8},
        {"sync_timeline_limit": 100},
        {"sync_timeout_ms": 10_000},
        {"protocol_git_commit": "def456"},
        {"config_hashes": {"A": "changed", "B": "hashB"}},
    ],
)
def test_any_meaningful_parameter_change_produces_a_new_campaign(override):
    assert campaign_id(_parameters()) != campaign_id(_parameters(**override))


# ----------------------------------------------------------------- resume


def _scheduled(block="lat-block01", top="local"):
    return ScheduledRun(
        workload="latency",
        block_id=block,
        block_index=1,
        within_block_order=1,
        topology=top,
        concurrency=1,
    )


def test_campaign_resume_skips_completed_runs(tmp_path):
    parameters = _parameters()
    state = CampaignState.open(tmp_path, parameters)
    runs = [_scheduled("lat-block01"), _scheduled("lat-block02")]
    assert list(pending(runs, state)) == runs

    state.record(runs[0], run_id="r1", digests={}, status="complete", manifest="m.json")
    reopened = CampaignState.open(tmp_path, parameters)
    assert reopened.resumed
    assert list(pending(runs, reopened)) == [runs[1]]


def test_resume_refuses_when_recorded_evidence_is_missing(tmp_path):
    state = CampaignState.open(tmp_path, _parameters())
    state.record(
        _scheduled(), run_id="r1", digests={}, status="complete",
        manifest="manifests/gone.manifest.json",
    )
    problems = state.verify_completed(tmp_path)
    assert problems and "missing" in problems[0]


def test_resume_detects_raw_evidence_that_changed_on_disk(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    stream = raw / "r1.runner.jsonl"
    stream.write_text("{}\n", encoding="utf-8")

    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = manifests / "r1.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "raw_artifacts": [
                    {"role": "runner_interaction_stream", "path": "raw/r1.runner.jsonl",
                     "sha256": "0" * 64}
                ]
            }
        ),
        encoding="utf-8",
    )
    state = CampaignState.open(tmp_path, _parameters())
    state.record(
        _scheduled(), run_id="r1", digests={}, status="complete",
        manifest="manifests/r1.manifest.json",
    )
    problems = state.verify_completed(tmp_path)
    assert problems and "changed on disk" in problems[0]


# -------------------------------------------------------------- topology


def test_only_the_agent_differs_between_topologies():
    assert LOCAL.sender == FEDERATED.sender
    assert LOCAL.agent != FEDERATED.agent
    assert LOCAL.agent.endswith(":hs-a.test")
    assert FEDERATED.agent.endswith(":hs-b.test")
    assert FEDERATED.crosses_federation and not LOCAL.crosses_federation


def test_unknown_topology_is_rejected():
    with pytest.raises(ValueError):
        topology("wan")


# ------------------------------------------------------------- estimator


def _run(
    *,
    topology_name="local",
    block="thr-c08-block01",
    workload="throughput",
    window=(1000 * SECOND, 1060 * SECOND),
    records=(),
    concurrency=8,
    valid=True,
):
    return RunRecords(
        run_id=f"{block}-{topology_name}",
        workload=workload,
        topology=topology_name,
        block_id=block,
        concurrency=concurrency,
        campaign_id="test",
        valid=valid,
        invalid_class=None,
        window_start_ns=window[0] if window else None,
        window_end_ns=window[1] if window else None,
        records=list(records),
    )


def _interaction(initiated, completed, *, outcome="success", phase="window"):
    return {
        "initiated_monotonic_ns": initiated,
        "completed_monotonic_ns": completed,
        "outcome": outcome,
        "phase": phase,
    }


def test_throughput_counts_departures_not_matched_pairs():
    """§19.2: an interaction started before the window still counts."""
    start, end = 1000 * SECOND, 1060 * SECOND
    run = _run(
        records=[
            # started in warm-up, completed inside: counts
            _interaction(start - SECOND, start + SECOND, phase="window"),
            # started and completed inside: counts
            _interaction(start + 2 * SECOND, start + 3 * SECOND),
            # started inside, completed after: does not count
            _interaction(end - SECOND, end + SECOND, phase="drain"),
            # completed before the window opened
            _interaction(start - 5 * SECOND, start - SECOND, phase="warmup"),
        ]
    )
    assert len(run.counted_in_window()) == 2
    assert run.observed_throughput() == pytest.approx(2 / 60)


def test_unsuccessful_completions_never_enter_the_numerator():
    start, end = 1000 * SECOND, 1060 * SECOND
    run = _run(
        records=[
            _interaction(start, start + SECOND),
            _interaction(start, start + SECOND, outcome="timeout"),
            _interaction(start, start + SECOND, outcome="duplicate_response"),
        ]
    )
    assert len(run.counted_in_window()) == 1


def test_window_membership_is_half_open():
    start, end = 1000 * SECOND, 1060 * SECOND
    run = _run(
        records=[
            _interaction(start, start),  # exactly at the open edge: counts
            _interaction(start, end),  # exactly at the close edge: does not
        ]
    )
    assert len(run.counted_in_window()) == 1


def test_counted_in_window_is_never_read_from_a_raw_field():
    """A raw record claiming membership must not be believed."""
    start, end = 1000 * SECOND, 1060 * SECOND
    lying = _interaction(start, end + 10 * SECOND, phase="drain")
    lying["counted_in_window"] = True
    run = _run(records=[lying])
    assert run.counted_in_window() == []


def test_failure_rate_excludes_warmup_for_latency():
    run = _run(
        workload="latency",
        window=None,
        records=[
            _interaction(0, 1, phase="warmup", outcome="timeout"),
            _interaction(2, 3, phase="measured"),
            _interaction(4, None, phase="measured", outcome="timeout"),
        ],
    )
    assert run.failure_rate() == pytest.approx(0.5)
    assert len(run.measured) == 2


def test_stationarity_splits_the_window_in_half():
    start = 1000 * SECOND
    run = _run(
        records=[_interaction(start, start + i * SECOND) for i in range(0, 30)]
        + [_interaction(start, start + i * SECOND) for i in range(30, 45)]
    )
    result = run.stationarity()
    assert result["first_half_completions"] == 30
    assert result["second_half_completions"] == 15
    assert result["second_over_first"] == pytest.approx(0.5)


def test_boundary_diagnostics_report_the_trailing_edge():
    start, end = 1000 * SECOND, 1060 * SECOND
    run = _run(
        records=[
            _interaction(start - SECOND, start + SECOND),
            _interaction(end - SECOND, end + 2 * SECOND, phase="drain"),
            _interaction(end - SECOND, None, phase="drain", outcome="timeout"),
        ]
    )
    diagnostics = run.boundary_diagnostics()
    assert diagnostics["outstanding_at_window_start"] == 1
    assert diagnostics["outstanding_at_window_end"] == 2
    assert diagnostics["drain_completions"] == 1
    assert diagnostics["unresolved_after_drain"] == 1


def test_slow_successful_interactions_are_never_trimmed():
    """§34: no outlier removal, in any form."""
    start = 1000 * SECOND
    run = _run(
        workload="latency",
        window=None,
        records=[_interaction(0, 1_000_000, phase="measured")]
        + [_interaction(0, 9_000_000_000, phase="measured")],
    )
    rtts = run.rtts_ns()
    assert len(rtts) == 2
    assert max(rtts) == 9_000_000_000


# ------------------------------------------------------------ percentiles


def test_percentile_is_nearest_rank_and_consistent():
    values = list(range(1, 101))
    assert percentile(values, 0.50) == 50
    assert percentile(values, 0.95) == 95
    assert percentile(values, 0.99) == 99
    assert percentile([], 0.5) is None
    assert percentile([42], 0.99) == 42


# --------------------------------------------------------------- pairing


def test_blocks_missing_a_topology_are_reported_not_silently_dropped():
    runs = [
        _run(block="b1", topology_name="local"),
        _run(block="b1", topology_name="federated"),
        _run(block="b2", topology_name="local"),
    ]
    assert [b.block_id for b in pair_blocks(runs)] == ["b1"]
    assert incomplete_blocks(runs) == ["b2"]


def test_invalid_runs_do_not_enter_paired_comparison():
    runs = [
        _run(block="b1", topology_name="local"),
        _run(block="b1", topology_name="federated", valid=False),
    ]
    assert pair_blocks(runs) == []
    assert incomplete_blocks(runs) == ["b1"]


# -------------------------------------------------------------- bootstrap


MS = 1_000_000


def _latency_block(block_id, local_rtts_ms, federated_rtts_ms):
    local_rtts = [v * MS for v in local_rtts_ms]
    federated_rtts = [v * MS for v in federated_rtts_ms]
    return PairedBlock(
        block_id,
        _run(
            block=block_id,
            topology_name="local",
            workload="latency",
            window=None,
            records=[
                _interaction(0, v, phase="measured") for v in local_rtts
            ],
        ),
        _run(
            block=block_id,
            topology_name="federated",
            workload="latency",
            window=None,
            records=[
                _interaction(0, v, phase="measured") for v in federated_rtts
            ],
        ),
    )


def test_latency_bootstrap_is_deterministic_and_paired():
    blocks = [
        _latency_block(f"b{i}", [10 + i, 12 + i, 14 + i], [20 + i, 24 + i, 28 + i])
        for i in range(20)
    ]
    first = paired_bootstrap_latency(blocks, replicates=200, seed=DEFAULT_BOOTSTRAP_SEED)
    second = paired_bootstrap_latency(blocks, replicates=200, seed=DEFAULT_BOOTSTRAP_SEED)
    assert first == second
    assert first["resampling_unit"] == "paired run block"
    assert first["blocks"] == 20

    p50 = first["percentiles"]["p50"]
    assert p50["federated_ms"] > p50["local_ms"]
    assert p50["ratio"] > 1
    assert p50["difference_ci_ms"]["low"] <= p50["difference_ms"] <= p50["difference_ci_ms"]["high"]


def test_latency_bootstrap_resamples_blocks_not_messages():
    """One extreme block must be able to leave the sample entirely.

    If messages were the resampling unit, an outlier block would appear in
    essentially every replicate and the interval would collapse. Resampling
    blocks keeps its influence visible in the interval width.
    """
    ordinary = [_latency_block(f"b{i}", [10, 10, 10], [20, 20, 20]) for i in range(19)]
    extreme = _latency_block("b-extreme", [10, 10, 10], [2000, 2000, 2000])
    wide = paired_bootstrap_latency(
        ordinary + [extreme], replicates=400, seed=DEFAULT_BOOTSTRAP_SEED
    )["percentiles"]["p99"]
    narrow = paired_bootstrap_latency(
        ordinary + [_latency_block("b19", [10, 10, 10], [20, 20, 20])],
        replicates=400,
        seed=DEFAULT_BOOTSTRAP_SEED,
    )["percentiles"]["p99"]

    wide_width = wide["difference_ci_ms"]["high"] - wide["difference_ci_ms"]["low"]
    narrow_width = narrow["difference_ci_ms"]["high"] - narrow["difference_ci_ms"]["low"]
    assert wide_width > narrow_width


def test_throughput_bootstrap_uses_run_estimates_not_completion_events():
    start, end = 1000 * SECOND, 1060 * SECOND
    blocks = []
    for i in range(20):
        local = _run(
            block=f"b{i}",
            topology_name="local",
            records=[_interaction(start, start + SECOND) for _ in range(60 + i)],
        )
        federated = _run(
            block=f"b{i}",
            topology_name="federated",
            records=[_interaction(start, start + SECOND) for _ in range(30 + i)],
        )
        blocks.append(PairedBlock(f"b{i}", local, federated))

    result = paired_bootstrap_throughput(blocks, replicates=300)
    assert result["resampling_unit"] == "paired run block"
    assert result["statistic"] == "median run observed throughput"
    assert result["blocks"] == 20
    assert len(result["local_run_throughputs"]) == 20
    assert result["ratio"] < 1
    assert result["ratio_ci"]["low"] <= result["ratio"] <= result["ratio_ci"]["high"]
    assert result == paired_bootstrap_throughput(blocks, replicates=300)


# ------------------------------------------ T3 instrumentation discipline


def _source(relative: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / relative).read_text(encoding="utf-8")


def test_t3_is_the_first_statement_of_the_ack_callback():
    """§8: nothing may sit between the callback entry and the T3 stamp.

    Checked structurally rather than by timing, because the defect this
    guards against — a lookup, a log line or a parse creeping above the
    clock read — is invisible in the output and shows up only as latency
    that grows with the runner rather than with the network.
    """
    tree = ast.parse(_source("src/fam/participants/human.py"))
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_handle"
    )
    body = handler.body
    if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # docstring

    first = body[0]
    assert isinstance(first, ast.Assign), "T3 must be the first statement"
    assert [t.id for t in first.targets] == ["t3"]
    assert isinstance(first.value, ast.Call)
    assert first.value.func.id == "monotonic_ns"


def test_t0_is_stamped_immediately_before_the_send():
    """§7: nothing between the T0 stamp and the room_send call."""
    tree = ast.parse(_source("src/fam/participants/human.py"))
    inner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request_inner"
    )
    statements = [n for n in ast.walk(inner)]
    stamp = next(
        n
        for n in statements
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", "") == "monotonic_ns"
    )
    assert getattr(stamp.targets[0], "attr", "") == "initiated_monotonic_ns"

    # The very next statement in that block must be the send.
    parent = next(
        node
        for node in ast.walk(inner)
        if hasattr(node, "body") and stamp in getattr(node, "body", [])
    )
    index = parent.body.index(stamp)
    following = parent.body[index + 1]
    assert "send_text" in ast.dump(following)


def test_records_are_buffered_rather_than_fsynced_inside_the_loop():
    """An fsync per completion would scale with throughput and bias the run."""
    tree = ast.parse(_source("src/fam/benchmark/engine.py"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "JsonlStream" not in imported, (
        "the engine must not hold a per-record fsynced stream: writing inside "
        "the measurement loop would bias whichever topology is faster"
    )


# ------------------------------------------- one outcome, fixed at the end


class _FakeInteraction:
    """Just enough of Interaction for the outcome rule."""

    def __init__(self, **kw):
        self.send_errcode = kw.get("send_errcode", "")
        self.completed_monotonic_ns = kw.get("completed_monotonic_ns")
        self.timed_out = kw.get("timed_out", False)
        self.late_ack_monotonic_ns = kw.get("late_ack_monotonic_ns")


def _outcome(**kw):
    from fam.benchmark.engine import _outcome_of

    return _outcome_of(_FakeInteraction(**kw)).value


def test_a_late_ack_cannot_turn_a_timeout_into_a_success():
    """§11: exactly one outcome, decided when the interaction terminated.

    asyncio.wait_for shields the pending future, so an ACK arriving after the
    deadline still reaches the callback. Deriving the outcome afterwards from
    the completion timestamp recorded RTTs longer than the timeout that ended
    them — and did so overwhelmingly in the slowest condition, biasing the
    comparison.
    """
    assert _outcome(timed_out=True, completed_monotonic_ns=None) == "timeout"
    assert (
        _outcome(timed_out=True, completed_monotonic_ns=16_640_000_000) == "timeout"
    )
    assert _outcome(completed_monotonic_ns=120_000_000) == "success"
    assert _outcome(completed_monotonic_ns=None) == "timeout"
    assert _outcome(send_errcode="M_LIMIT_EXCEEDED") == "send_error"


def test_no_successful_interaction_may_exceed_the_frozen_timeout():
    """The invariant the defect violated, stated directly."""
    from fam.common.frozen import DEFAULT_INTERACTION_TIMEOUT_SECONDS

    budget = int(DEFAULT_INTERACTION_TIMEOUT_SECONDS * 1e9)
    for rtt in (budget - 1, budget, budget + 1, budget * 2):
        interaction = _FakeInteraction(
            completed_monotonic_ns=rtt, timed_out=rtt >= budget
        )
        from fam.benchmark.engine import _outcome_of

        if _outcome_of(interaction).value == "success":
            assert rtt < budget, "a success may never exceed the frozen timeout"


def test_late_ack_is_preserved_rather_than_discarded():
    """The observation is evidence even though it is not a success."""
    interaction = _FakeInteraction(timed_out=True, late_ack_monotonic_ns=16_640_000_000)
    assert interaction.late_ack_monotonic_ns == 16_640_000_000
    assert _outcome(timed_out=True, late_ack_monotonic_ns=1) == "timeout"


def test_a_runtime_change_produces_a_new_campaign():
    """Data from two runtime revisions must never resume into one another."""
    import fam.benchmark.schedule as schedule

    before = campaign_id(_parameters())
    original = schedule.RUNTIME_CODE_REVISION
    try:
        schedule.RUNTIME_CODE_REVISION = "task-05-r99"
        after = campaign_id(_parameters())
    finally:
        schedule.RUNTIME_CODE_REVISION = original
    assert before != after


def test_the_interaction_deadline_is_budgeted_from_initiation():
    """§11 bounds the logical interaction, which §9/§10 start at the send.

    Arming a full timeout after the send makes the effective bound
    `send_duration + timeout`, so a slow send lets a "success" report an RTT
    longer than the timeout that was supposed to end it — and slow sends
    happen under load, which is exactly when it matters.
    """
    source = _source("src/fam/participants/human.py")
    tree = ast.parse(source)
    inner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_request_inner"
    )
    dumped = ast.dump(inner)
    assert "remaining" in dumped, "the ACK wait must use a budget, not the raw timeout"
    # wait_for must receive the remaining budget, never the full timeout.
    wait_calls = [
        node
        for node in ast.walk(inner)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "wait_for"
    ]
    assert wait_calls, "the ACK wait must be bounded"
    for call in wait_calls:
        budget = call.args[1] if len(call.args) > 1 else None
        assert isinstance(budget, ast.Name) and budget.id == "remaining", (
            "wait_for must be given the budget remaining from T0"
        )


# ------------------------------------------------------- data integrity


def test_integrity_check_catches_a_success_above_the_timeout():
    """The r1 defect, as a dataset the analysis must refuse."""
    from fam.analysis.e3 import integrity_problems

    run = _run(
        workload="latency",
        window=None,
        records=[
            _interaction(0, 16_640_000_000, phase="measured"),
            _interaction(0, 120_000_000, phase="measured"),
        ],
    )
    problems = integrity_problems([run])
    assert len(problems) == 1
    assert "above the frozen" in problems[0]
    assert problems[0].startswith(run.run_id)


def test_integrity_check_passes_a_clean_dataset():
    from fam.analysis.e3 import integrity_problems

    run = _run(
        workload="latency",
        window=None,
        records=[_interaction(0, 120_000_000, phase="measured")],
    )
    assert integrity_problems([run]) == []


def test_integrity_check_catches_impossible_records():
    from fam.analysis.e3 import integrity_problems

    reversed_clock = _run(
        workload="latency", window=None,
        records=[_interaction(500, 100, phase="measured")],
    )
    assert any("before they were initiated" in p for p in integrity_problems([reversed_clock]))

    timeout_with_completion = _run(
        workload="latency", window=None,
        records=[_interaction(0, 50, phase="measured", outcome="timeout")],
    )
    assert any(
        "timeouts carrying a completion timestamp" in p
        for p in integrity_problems([timeout_with_completion])
    )

    no_window = _run(window=None, records=[_interaction(0, 50)])
    assert any(
        "without window bounds" in p for p in integrity_problems([no_window])
    )
