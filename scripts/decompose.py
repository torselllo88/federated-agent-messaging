#!/usr/bin/env python3
"""Post-collection decomposition of the E3 round trip (experimental-protocol.md §33).

    raw -> this script -> processed/e3-tables/rtt_decomposition.csv

The runner stamps T0 before transmitting a request and T3 when it begins
processing the first matching acknowledgement. The agent stamps T1 on entering
its handler for that request and T2 after the executor returns, immediately
before the response send begins. Both processes run in containers on one Linux
kernel, so CLOCK_MONOTONIC is shared and the four stamps lie on one timeline.

That premise is checked rather than assumed: every measured interaction must
satisfy T0 <= T1 <= T2 <= T3, and a single violation makes the decomposition
meaningless. The check is reported in the companion JSON.

**This is explanatory, not primary.** The predefined primary latency measure is
T0 -> T3 and nothing here modifies it, the acceptance criteria, or any frozen
parameter. It exists because the paper reports the decomposition and every
other reported quantity is regenerable from the archive; this one was not.

Two quantities are produced:

*Latency components.* Nearest-rank percentiles of each interval over the
measured successful interactions of the latency workload, matching the
estimator the frozen protocol fixes for latency (§31). **Component percentiles
do not sum to the percentile of the total** — each is an independent order
statistic over its own sample, so the rows of the decomposition are close to,
but not exactly, additive. This is a property of quantiles, not a defect, and
it is the reason the arithmetic in the published table does not close. The
residual is measured and reported rather than explained away.

*Agent service interval.* The interval between consecutive requests entering
the agent handler during the throughput workload, bounded by the frozen
measurement window and by nothing else, then reduced per run and across runs by
the sample median the protocol fixes for run-level values (§31). Its reciprocal
is the rate the serialized handler can sustain, which is the quantity the
observed throughput plateau is compared against.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fam.analysis import e3 as e3_analysis  # noqa: E402
from fam.common.env import (  # noqa: E402
    analysis_code_commit,
    publication_data,
)
from fam.common.frozen import EXECUTION_ANALYSIS_SPEC_VERSION  # noqa: E402
from fam.common.results import resolve_results_dir  # noqa: E402

SUCCESS = "success"

#: The four intervals, in the order they occur.
COMPONENTS = (
    ("request_path_t0_t1", "T0->T1 request path"),
    ("executor_t1_t2", "T1->T2 executor"),
    ("response_path_t2_t3", "T2->T3 response path"),
    ("end_to_end_t0_t3", "T0->T3 end to end"),
)

TOPOLOGIES = ("local", "federated")


def _agent_stream(root: Path, run: e3_analysis.RunRecords) -> Path | None:
    """The agent telemetry path this run's manifest names.

    Resolved through the manifest rather than by globbing the raw directory,
    so a run whose stream is missing or renamed is a loud absence instead of a
    silently smaller sample.
    """
    artifact = next(
        (
            a
            for a in run.manifest.get("raw_artifacts", [])
            if a.get("role") == "agent_telemetry_stream"
        ),
        None,
    )
    if artifact is None:
        return None
    path = root / artifact["path"]
    return path if path.exists() else None


def _agent_responses(root: Path, run: e3_analysis.RunRecords) -> dict[Any, dict]:
    """Responded records of one run, keyed by sequence id."""
    path = _agent_stream(root, run)
    if path is None:
        return {}
    out: dict[Any, dict] = {}
    for record in e3_analysis._load_agent_records(path):
        if record.get("action") != "responded":
            continue
        if record.get("received_monotonic_ns") is None:
            continue
        if record.get("processed_monotonic_ns") is None:
            continue
        out[record.get("sequence_id")] = record
    return out


def decompose_latency(root: Path, runs: list[e3_analysis.RunRecords]) -> dict[str, Any]:
    """Per-topology samples of the four intervals, plus the ordering check."""
    samples: dict[str, dict[str, list[float]]] = {
        topology: {key: [] for key, _ in COMPONENTS} for topology in TOPOLOGIES
    }
    checked = 0
    violations: list[dict[str, Any]] = []
    unmatched = 0

    for run in runs:
        if run.workload != "latency" or not run.valid:
            continue
        if run.topology not in samples:
            continue
        responses = _agent_responses(root, run)
        for record in run.measured:
            if record.get("outcome") != SUCCESS:
                continue
            agent = responses.get(record.get("sequence_id"))
            if agent is None:
                unmatched += 1
                continue
            t0 = record.get("initiated_monotonic_ns")
            t3 = record.get("completed_monotonic_ns")
            t1 = agent.get("received_monotonic_ns")
            t2 = agent.get("processed_monotonic_ns")
            if None in (t0, t1, t2, t3):
                unmatched += 1
                continue
            checked += 1
            if not t0 <= t1 <= t2 <= t3:
                violations.append(
                    {
                        "run_id": run.run_id,
                        "sequence_id": record.get("sequence_id"),
                        "t0": t0,
                        "t1": t1,
                        "t2": t2,
                        "t3": t3,
                    }
                )
            bucket = samples[run.topology]
            bucket["request_path_t0_t1"].append((t1 - t0) / 1e6)
            bucket["executor_t1_t2"].append((t2 - t1) / 1e6)
            bucket["response_path_t2_t3"].append((t3 - t2) / 1e6)
            bucket["end_to_end_t0_t3"].append((t3 - t0) / 1e6)

    percentiles: dict[str, dict[str, dict[str, float | None]]] = {}
    for topology, bucket in samples.items():
        percentiles[topology] = {}
        for key, _ in COMPONENTS:
            ordered = sorted(bucket[key])
            percentiles[topology][key] = {
                "p50_ms": _round(e3_analysis.percentile(ordered, 0.50)),
                "p95_ms": _round(e3_analysis.percentile(ordered, 0.95)),
                "observations": len(ordered),
            }

    # Measured, not asserted: a note claiming a shortfall of its own would be
    # one more hand-written number in a file that exists to remove them.
    shortfall: dict[str, float | None] = {}
    for topology, block in percentiles.items():
        parts = [
            block[key]["p50_ms"]
            for key, _ in COMPONENTS
            if key != "end_to_end_t0_t3"
        ]
        total = block["end_to_end_t0_t3"]["p50_ms"]
        shortfall[topology] = (
            None if total is None or None in parts else round(total - sum(parts), 4)
        )

    return {
        "estimator": "nearest rank, the estimator the frozen protocol fixes for latency (§31)",
        "population": "measured successful interactions of the latency workload",
        "by_topology": percentiles,
        "p50_additivity_shortfall_ms": shortfall,
        "clock_consistency": {
            "interactions_checked": checked,
            "ordering_violations": len(violations),
            "violation_examples": violations[:5],
            "interactions_without_a_matching_agent_record": unmatched,
            "premise": (
                "T0 and T3 are stamped by the runner, T1 and T2 by the agent. "
                "One host kernel and one CLOCK_MONOTONIC make them comparable; "
                "any ordering violation would disprove that and invalidate "
                "every component below."
            ),
        },
        "additivity_note": (
            "Component percentiles are independent order statistics over "
            "separate samples and are not required to sum to the percentile of "
            "the total. The residual is reported as "
            "p50_additivity_shortfall_ms rather than described, so the table "
            "can be checked against it."
        ),
    }


def agent_service_interval(
    root: Path, runs: list[e3_analysis.RunRecords]
) -> dict[str, Any]:
    """Interval between consecutive requests entering the agent handler.

    Bounded by the frozen measurement window on the agent's own receipt stamp,
    so warm-up and drain contribute nothing. Reduced per run first, then across
    runs, because a run is the unit the protocol treats as one observation for
    throughput (§22, §31) and pooling raw gaps would weight a fast run more
    heavily than a slow one.
    """
    per_run: dict[str, dict[int, list[float]]] = {t: {} for t in TOPOLOGIES}
    executor: dict[str, dict[int, list[float]]] = {t: {} for t in TOPOLOGIES}

    for run in runs:
        if run.workload != "throughput" or not run.valid:
            continue
        if run.topology not in per_run:
            continue
        start, end = run.window_start_ns, run.window_end_ns
        if start is None or end is None:
            continue
        responses = _agent_responses(root, run)
        inside = [
            record
            for record in responses.values()
            if start <= record["received_monotonic_ns"] < end
        ]
        if len(inside) < 2:
            continue
        inside.sort(key=lambda r: r["received_monotonic_ns"])
        stamps = [r["received_monotonic_ns"] for r in inside]
        gaps = [(b - a) / 1e6 for a, b in zip(stamps, stamps[1:])]
        work = [
            (r["processed_monotonic_ns"] - r["received_monotonic_ns"]) / 1e6
            for r in inside
        ]
        per_run[run.topology].setdefault(run.concurrency, []).append(
            e3_analysis.median(gaps)
        )
        executor[run.topology].setdefault(run.concurrency, []).extend(work)

    out: dict[str, Any] = {
        "estimator": (
            "per-run sample median of consecutive gaps, then the sample median "
            "across runs (§31)"
        ),
        "population": (
            "requests entering the agent handler inside the frozen measurement "
            "window of the throughput workload"
        ),
        "by_topology": {},
    }
    for topology in TOPOLOGIES:
        levels: dict[str, Any] = {}
        pooled_runs: list[float] = []
        pooled_work: list[float] = []
        for concurrency in sorted(per_run[topology]):
            run_medians = per_run[topology][concurrency]
            work = executor[topology][concurrency]
            pooled_runs.extend(run_medians)
            pooled_work.extend(work)
            levels[str(concurrency)] = _interval_block(run_medians, work)
        levels["all"] = _interval_block(pooled_runs, pooled_work)
        out["by_topology"][topology] = levels
    return out


def _interval_block(run_medians: list[float], work: list[float]) -> dict[str, Any]:
    interval = e3_analysis.median(run_medians)
    return {
        "runs": len(run_medians),
        "service_interval_ms": _round(interval),
        "implied_service_rate_per_second": (
            None if not interval else round(1000.0 / interval, 4)
        ),
        "executor_p50_ms": _round(
            e3_analysis.percentile(sorted(work), 0.50) if work else None
        ),
        "observations": len(work),
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _difference(local: float | None, federated: float | None) -> float | None:
    if local is None or federated is None:
        return None
    return round(federated - local, 4)


def rows(latency: dict[str, Any], interval: dict[str, Any]) -> list[dict[str, Any]]:
    """The tidy table, one row per comparable quantity."""
    out: list[dict[str, Any]] = []
    for statistic in ("p50", "p95"):
        for key, _label in COMPONENTS:
            local = latency["by_topology"]["local"][key]
            federated = latency["by_topology"]["federated"][key]
            out.append(
                {
                    "workload": "latency",
                    "concurrency": "1",
                    "component": key,
                    "statistic": statistic,
                    "unit": "ms",
                    "local": local[f"{statistic}_ms"],
                    "federated": federated[f"{statistic}_ms"],
                    "difference": _difference(
                        local[f"{statistic}_ms"], federated[f"{statistic}_ms"]
                    ),
                    "observations_local": local["observations"],
                    "observations_federated": federated["observations"],
                }
            )

    levels = sorted(
        set(interval["by_topology"]["local"]) | set(interval["by_topology"]["federated"]),
        key=lambda name: (name == "all", name),
    )
    for level in levels:
        local = interval["by_topology"]["local"].get(level)
        federated = interval["by_topology"]["federated"].get(level)
        if not local or not federated:
            continue
        for component, statistic, unit, field in (
            ("agent_service_interval", "median", "ms", "service_interval_ms"),
            (
                "implied_service_rate",
                "reciprocal_of_interval",
                "interactions/second",
                "implied_service_rate_per_second",
            ),
            ("executor_t1_t2", "p50", "ms", "executor_p50_ms"),
        ):
            out.append(
                {
                    "workload": "throughput",
                    "concurrency": level,
                    "component": component,
                    "statistic": statistic,
                    "unit": unit,
                    "local": local[field],
                    "federated": federated[field],
                    "difference": _difference(local[field], federated[field]),
                    "observations_local": local["observations"],
                    "observations_federated": federated["observations"],
                }
            )
    return out


def write_table(table: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # See fam.analysis.e3.write_tables: the csv dialect terminates lines with
    # CRLF on every platform unless pinned, and this table is compared byte
    # for byte against its committed copy.
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(table[0].keys()), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(table)
    return path


def _protocol_commit(runs: list[e3_analysis.RunRecords]) -> str:
    commits = {
        run.manifest.get("protocol_git_commit")
        for run in runs
        if run.manifest.get("protocol_git_commit")
    }
    if len(commits) == 1:
        return commits.pop()
    if not commits:
        return "unknown"
    return "mixed"


def _source_digests(runs: list[e3_analysis.RunRecords]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for run in runs:
        roles: dict[str, str] = {}
        for artifact in run.manifest.get("raw_artifacts", []):
            role, digest = artifact.get("role"), artifact.get("sha256")
            if role and digest:
                roles[role] = digest
        if roles:
            out[run.run_id] = roles
    return dict(sorted(out.items()))


def report(latency: dict[str, Any], interval: dict[str, Any]) -> None:
    check = latency["clock_consistency"]
    print("1. one timeline across two processes")
    print(f"   interactions checked      {check['interactions_checked']:,}")
    print(f"   ordering violations       {check['ordering_violations']}")
    if check["ordering_violations"]:
        print("   -> the stamps are NOT comparable; the decomposition is void")
        return
    print("   -> T0 <= T1 <= T2 <= T3 holds throughout")

    print("\n2. median round trip, decomposed")
    print(f"   {'component':24} {'local':>10} {'federated':>10} {'difference':>11}")
    for key, label in COMPONENTS:
        local = latency["by_topology"]["local"][key]["p50_ms"]
        federated = latency["by_topology"]["federated"][key]["p50_ms"]
        print(
            f"   {label:24} {local:>10.2f} {federated:>10.2f} "
            f"{_difference(local, federated):>+11.2f}"
        )

    print("\n3. agent service interval, inside the measurement window")
    for topology in TOPOLOGIES:
        block = interval["by_topology"][topology]["all"]
        print(
            f"   {topology:12} {block['service_interval_ms']:>8.2f} ms  -> "
            f"{block['implied_service_rate_per_second']:>6.2f}/s   "
            f"executor p50 {block['executor_p50_ms']:.3f} ms "
            f"({block['runs']} runs)"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign-id",
        default=None,
        help="campaign to decompose; the most recently completed one by default",
    )
    args = parser.parse_args()

    root = resolve_results_dir(create=False)
    runs = e3_analysis.load_campaign(root, args.campaign_id)
    if not runs:
        print("no E3 campaign runs found")
        return 1
    campaign_id = runs[0].campaign_id
    print(f"decomposition over {root}")
    print(f"campaign {campaign_id}, {len(runs)} runs\n")

    latency = decompose_latency(root, runs)
    interval = agent_service_interval(root, runs)
    report(latency, interval)

    table = rows(latency, interval)
    processed = root / "processed"
    table_path = write_table(table, processed / "e3-tables" / "rtt_decomposition.csv")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "artifact": "e3_rtt_decomposition",
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": analysis_code_commit(),
        "protocol_git_commit": _protocol_commit(runs),
        "campaign_id": campaign_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publication_data": publication_data(),
        "scope_note": (
            "Post-collection explanatory analysis over already archived "
            "telemetry. It derives no primary metric and changes no acceptance "
            "criterion; the predefined primary latency measure remains T0 -> T3."
        ),
        "latency_decomposition": latency,
        "agent_service_interval": interval,
        "source_run_ids": sorted(run.run_id for run in runs),
        "source_digests": _source_digests(runs),
    }
    json_path = processed / f"rtt-decomposition-{stamp}.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )

    print(f"\n   table:     {table_path}")
    print(f"   provenance: {json_path}")
    return 0 if not latency["clock_consistency"]["ordering_violations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
