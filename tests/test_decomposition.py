"""The round-trip decomposition and the premise it rests on.

The decomposition subtracts stamps taken by two different processes. That is
only meaningful while both read one clock, and the whole table becomes
arithmetic on unrelated numbers the moment they do not. Nothing crashes when
that happens, which is why the check is a test and not a comment.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from fam.analysis.e3 import RunRecords

ROOT = Path(__file__).resolve().parents[1]
MS = 1_000_000


def _load_decompose():
    """Import scripts/decompose.py, which is a script rather than a module."""
    spec = importlib.util.spec_from_file_location(
        "fam_decompose", ROOT / "scripts" / "decompose.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


decompose = _load_decompose()


def _write_run(
    tmp_path: Path,
    *,
    run_id: str,
    topology: str,
    workload: str,
    interactions: list[tuple[int, int, int, int]],
    window: tuple[int, int] | None = None,
    phase: str = "measured",
) -> RunRecords:
    """One run on disk: a runner stream in memory, an agent stream in a file.

    The agent side is written out because the code under test resolves it
    through the manifest, which is the behaviour worth exercising.
    """
    stream = tmp_path / f"{run_id}.agent.jsonl"
    runner_records = []
    agent_lines = []
    for sequence_id, (t0, t1, t2, t3) in enumerate(interactions, start=1):
        runner_records.append(
            {
                "record_type": "interaction",
                "sequence_id": sequence_id,
                "phase": phase,
                "outcome": "success",
                "initiated_monotonic_ns": t0,
                "completed_monotonic_ns": t3,
            }
        )
        agent_lines.append(
            json.dumps(
                {
                    "action": "responded",
                    "sequence_id": sequence_id,
                    "received_monotonic_ns": t1,
                    "processed_monotonic_ns": t2,
                }
            )
        )
    stream.write_text("\n".join(agent_lines) + "\n", encoding="utf-8")

    return RunRecords(
        run_id=run_id,
        workload=workload,
        topology=topology,
        block_id="block01",
        concurrency=1 if workload == "latency" else 8,
        campaign_id="test",
        valid=True,
        invalid_class=None,
        window_start_ns=window[0] if window else None,
        window_end_ns=window[1] if window else None,
        records=runner_records,
        manifest={
            "raw_artifacts": [
                {"role": "agent_telemetry_stream", "path": stream.name},
                {"role": "runner_interaction_stream", "path": f"{run_id}.runner.jsonl"},
            ],
            "protocol_git_commit": "c0ffee",
        },
    )


def _latency_pair(tmp_path: Path, interactions) -> list[RunRecords]:
    return [
        _write_run(
            tmp_path,
            run_id=f"lat-{topology}",
            topology=topology,
            workload="latency",
            interactions=interactions,
        )
        for topology in ("local", "federated")
    ]


def test_out_of_order_stamps_are_counted_not_absorbed(tmp_path):
    """A run whose agent clock disagrees must surface, not average in.

    Two containers on one kernel share CLOCK_MONOTONIC, so T0 <= T1 <= T2 <= T3
    holds by construction. If a future topology breaks that — separate hosts,
    a clock adjustment — the components become differences between unrelated
    timelines and every number in the table is meaningless. The script reports
    the count and exits non-zero rather than publishing the table anyway.
    """
    good = (0, 10 * MS, 11 * MS, 50 * MS)
    # T1 before T0: the agent's clock reads earlier than the sender's.
    skewed = (100 * MS, 40 * MS, 41 * MS, 150 * MS)
    runs = _latency_pair(tmp_path, [good, skewed])

    result = decompose.decompose_latency(tmp_path, runs)
    check = result["clock_consistency"]

    assert check["interactions_checked"] == 4
    assert check["ordering_violations"] == 2
    assert check["violation_examples"], "a violation must be identifiable, not just counted"


def test_clean_stamps_report_no_violation(tmp_path):
    runs = _latency_pair(tmp_path, [(0, 10 * MS, 11 * MS, 50 * MS)])
    check = decompose.decompose_latency(tmp_path, runs)["clock_consistency"]
    assert check["ordering_violations"] == 0
    assert check["interactions_without_a_matching_agent_record"] == 0


def test_components_are_the_intervals_they_name(tmp_path):
    """Each component measures its own leg, not a running total."""
    runs = _latency_pair(tmp_path, [(0, 10 * MS, 11 * MS, 50 * MS)])
    local = decompose.decompose_latency(tmp_path, runs)["by_topology"]["local"]

    assert local["request_path_t0_t1"]["p50_ms"] == pytest.approx(10.0)
    assert local["executor_t1_t2"]["p50_ms"] == pytest.approx(1.0)
    assert local["response_path_t2_t3"]["p50_ms"] == pytest.approx(39.0)
    assert local["end_to_end_t0_t3"]["p50_ms"] == pytest.approx(50.0)


def test_additivity_shortfall_is_measured_rather_than_asserted(tmp_path):
    """The residual is reported because component quantiles need not sum.

    With one observation there is nothing to disagree about and the shortfall
    is zero; the point of the field is that it is computed from the same table
    the reader is checking, so it cannot drift away from it.
    """
    runs = _latency_pair(tmp_path, [(0, 10 * MS, 11 * MS, 50 * MS)])
    result = decompose.decompose_latency(tmp_path, runs)
    assert result["p50_additivity_shortfall_ms"]["local"] == pytest.approx(0.0)


def test_interactions_without_an_agent_record_are_reported_not_dropped(tmp_path):
    """A missing agent record shrinks the sample, so it has to be visible."""
    runs = _latency_pair(tmp_path, [(0, 10 * MS, 11 * MS, 50 * MS)])
    for run in runs:
        run.records.append(
            {
                "record_type": "interaction",
                "sequence_id": 99,
                "phase": "measured",
                "outcome": "success",
                "initiated_monotonic_ns": 0,
                "completed_monotonic_ns": 50 * MS,
            }
        )

    result = decompose.decompose_latency(tmp_path, runs)
    assert result["clock_consistency"]["interactions_without_a_matching_agent_record"] == 2
    assert result["by_topology"]["local"]["end_to_end_t0_t3"]["observations"] == 1


def test_service_interval_ignores_arrivals_outside_the_window(tmp_path):
    """§22: the window that defines throughput also bounds this.

    Warm-up and drain run at the same concurrency, so including them would not
    look wrong — it would quietly shift the interval the throughput plateau is
    compared against. Here the out-of-window arrivals are deliberately spaced
    differently from the in-window ones, so leaking them in changes the answer.
    """
    window = (1000 * MS, 2000 * MS)
    inside = [1000, 1100, 1200, 1300]
    outside = [500, 600, 2500]
    interactions = [
        (t * MS, t * MS, t * MS + MS, t * MS + 2 * MS) for t in outside + inside
    ]
    runs = [
        _write_run(
            tmp_path,
            run_id=f"thr-{topology}",
            topology=topology,
            workload="throughput",
            interactions=interactions,
            window=window,
            phase="window",
        )
        for topology in ("local", "federated")
    ]

    block = decompose.agent_service_interval(tmp_path, runs)["by_topology"]["local"]["all"]

    # Four arrivals 100 ms apart inside the window: three gaps of 100 ms each.
    assert block["service_interval_ms"] == pytest.approx(100.0)
    assert block["implied_service_rate_per_second"] == pytest.approx(10.0)
    assert block["observations"] == len(inside)


def test_the_table_is_written_with_lf_only(tmp_path):
    """The csv dialect terminates lines, not the platform.

    `DictWriter` emits CRLF everywhere unless pinned — inside the Linux image
    too — while the copy committed to the repository is LF. The two matched
    only by accident: `core.autocrlf` normalised on commit and converted back
    on checkout, so a Windows working tree held CRLF and compared equal to its
    own output. Pinning line endings in `.gitattributes` removed the second
    half of that accident and this table stopped reproducing byte for byte
    from a clean clone.
    """
    path = tmp_path / "rtt_decomposition.csv"
    decompose.write_table(
        [{"workload": "latency", "component": "request_path_t0_t1", "local": 1.0}],
        path,
    )
    raw = path.read_bytes()
    assert b"\r\n" not in raw, "rtt_decomposition.csv carries CRLF"
    assert raw.endswith(b"\n")


def test_table_carries_both_topologies_and_their_difference(tmp_path):
    runs = _latency_pair(tmp_path, [(0, 10 * MS, 11 * MS, 50 * MS)])
    latency = decompose.decompose_latency(tmp_path, runs)
    interval = decompose.agent_service_interval(tmp_path, [])
    rows = decompose.rows(latency, interval)

    p50 = [r for r in rows if r["statistic"] == "p50" and r["workload"] == "latency"]
    assert {r["component"] for r in p50} == {key for key, _ in decompose.COMPONENTS}
    for row in p50:
        assert row["difference"] == pytest.approx(row["federated"] - row["local"])
        assert row["unit"] == "ms"
