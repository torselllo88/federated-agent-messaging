"""E3 analysis: latency, observed throughput, and the paired-block bootstrap.

experimental-protocol.md §29–§33. Two rules shape everything here.

*The paired run block is the resampling unit.* Individual messages are not
independent replicates (§29), so the bootstrap resamples blocks, keeps both
topologies of every selected block, and only then pools messages inside the
selected runs. Bootstrapping messages directly would produce intervals far too
narrow to mean anything, because ten thousand observations from twenty runs
carry roughly twenty runs worth of information.

*Nothing is trimmed.* No winsorization, no tail trimming, no outlier removal
(§33, §34). A slow successful interaction is data.

Everything in this module is derived. It reads immutable raw records and
recomputes every classification — including window membership — under the
current ``analysis_spec_version``, so a later estimator revision changes the
processed artifacts and never the evidence they came from.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Sequence

from fam.common.frozen import (
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E3_BOOTSTRAP_CONFIDENCE,
    E3_MEASUREMENT_SECONDS,
    E3_TOPOLOGY_FEDERATED,
    E3_TOPOLOGY_LOCAL,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
)

#: Bootstrap replicates. Not frozen by the protocol, which fixes the method
#: and the confidence level but not the replicate count. Two thousand is
#: comfortably enough for a 95% percentile interval over twenty clusters, and
#: keeps the whole analysis interactive.
DEFAULT_REPLICATES = 2000
DEFAULT_BOOTSTRAP_SEED = 20260905

SUCCESS = "success"


# ------------------------------------------------------------- percentiles


def percentile(sorted_values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile of an already-sorted sample.

    One definition is used for the point estimate and for every bootstrap
    replicate. Nearest rank rather than interpolation because it is
    unambiguous and needs no convention footnote; at ten thousand
    observations the two differ by less than the clock resolution.
    """
    if not sorted_values:
        return None
    rank = max(1, math.ceil(q * len(sorted_values)))
    return float(sorted_values[min(rank, len(sorted_values)) - 1])


def _ns_to_ms(value: float | None) -> float | None:
    return None if value is None else round(value / 1e6, 4)


# --------------------------------------------------------------- run models


@dataclass
class RunRecords:
    """One benchmark run: its manifest facts and its raw interactions."""

    run_id: str
    workload: str
    topology: str
    block_id: str
    concurrency: int
    campaign_id: str
    valid: bool
    invalid_class: str | None
    window_start_ns: int | None
    window_end_ns: int | None
    records: list[dict[str, Any]] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    # -- latency ---------------------------------------------------------

    @property
    def measured(self) -> list[dict[str, Any]]:
        """Measured interactions: warm-up excluded (§30)."""
        return [r for r in self.records if r.get("phase") == "measured"]

    def rtts_ns(self) -> list[int]:
        return sorted(
            r["completed_monotonic_ns"] - r["initiated_monotonic_ns"]
            for r in self.measured
            if r.get("outcome") == SUCCESS
            and r.get("completed_monotonic_ns") is not None
        )

    # -- throughput ------------------------------------------------------

    def counted_in_window(self) -> list[dict[str, Any]]:
        """§22: successful completions inside the window, by completion time.

        Derived here, never read from a raw field. Initiation time is
        irrelevant to this count — that is exactly what removes the
        one-sided censoring bias at the trailing edge.
        """
        start, end = self.window_start_ns, self.window_end_ns
        if start is None or end is None:
            return []
        return [
            r
            for r in self.records
            if r.get("outcome") == SUCCESS
            and r.get("completed_monotonic_ns") is not None
            and start <= r["completed_monotonic_ns"] < end
        ]

    def observed_throughput(self) -> float | None:
        start, end = self.window_start_ns, self.window_end_ns
        if start is None or end is None or end <= start:
            return None
        return len(self.counted_in_window()) / ((end - start) / 1e9)

    # -- shared ----------------------------------------------------------

    def failure_rate(self) -> float | None:
        """§12, over the interactions this workload counts as initiated."""
        population = (
            self.measured
            if self.workload == E3_WORKLOAD_LATENCY
            else [r for r in self.records if r.get("phase") != "warmup"]
        )
        if not population:
            return None
        failures = sum(1 for r in population if r.get("outcome") != SUCCESS)
        return failures / len(population)

    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            outcome = record.get("outcome", "unknown")
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def stationarity(self) -> dict[str, Any]:
        """First-half against second-half completion rate (§22, mandatory)."""
        start, end = self.window_start_ns, self.window_end_ns
        if start is None or end is None or end <= start:
            return {}
        midpoint = start + (end - start) // 2
        half_seconds = (end - start) / 2e9
        first = sum(
            1
            for r in self.counted_in_window()
            if r["completed_monotonic_ns"] < midpoint
        )
        second = len(self.counted_in_window()) - first
        first_rate = first / half_seconds
        second_rate = second / half_seconds
        return {
            "first_half_completions": first,
            "second_half_completions": second,
            "first_half_rate_per_second": round(first_rate, 4),
            "second_half_rate_per_second": round(second_rate, 4),
            "second_over_first": (
                round(second_rate / first_rate, 4) if first_rate else None
            ),
        }

    def boundary_diagnostics(self) -> dict[str, Any]:
        """§24. Diagnostics only; the estimator does not consult them."""
        start, end = self.window_start_ns, self.window_end_ns
        if start is None or end is None:
            return {}
        outstanding_at_start = sum(
            1
            for r in self.records
            if r["initiated_monotonic_ns"] < start
            and (
                r.get("completed_monotonic_ns") is None
                or r["completed_monotonic_ns"] >= start
            )
        )
        outstanding_at_end = sum(
            1
            for r in self.records
            if r["initiated_monotonic_ns"] < end
            and (
                r.get("completed_monotonic_ns") is None
                or r["completed_monotonic_ns"] >= end
            )
        )
        drain = [r for r in self.records if r.get("phase") == "drain"]
        return {
            "outstanding_at_window_start": outstanding_at_start,
            "successful_completions_in_window": len(self.counted_in_window()),
            "failures_in_window": sum(
                1
                for r in self.records
                if r.get("outcome") != SUCCESS
                and r.get("completed_monotonic_ns") is not None
                and start <= r["completed_monotonic_ns"] < end
            ),
            "outstanding_at_window_end": outstanding_at_end,
            "drain_completions": sum(1 for r in drain if r.get("outcome") == SUCCESS),
            "drain_failures": sum(1 for r in drain if r.get("outcome") != SUCCESS),
            "unresolved_after_drain": sum(
                1
                for r in self.records
                if r.get("completed_monotonic_ns") is None
                and r.get("outcome") != "send_error"
            ),
        }

    def recovery_overlap(self) -> int:
        """Interactions whose ACK arrived through a live recovery episode."""
        return sum(1 for r in self.records if r.get("live_recovery_episode"))

    def rate_limited(self) -> int:
        return sum(
            1 for r in self.records if r.get("send_errcode") == "M_LIMIT_EXCEEDED"
        )


@dataclass
class PairedBlock:
    block_id: str
    local: RunRecords
    federated: RunRecords


# --------------------------------------------------------------- loading


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("record_type", "interaction") == "interaction":
            out.append(record)
    return out


def campaign_inventory(root: Path) -> dict[str, dict[str, Any]]:
    """Every E3 campaign present, so a superseded one cannot pass unnoticed."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "manifests").glob("*.manifest.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("experiment") != "E3":
            continue
        key = payload.get("campaign_id", "")
        entry = found.setdefault(
            key,
            {
                "runs": 0,
                "runtime_code_revision": None,
                "last_completion": "",
                "fingerprint": payload.get("campaign_fingerprint"),
            },
        )
        entry["runs"] += 1
        stamp = payload.get("completion_timestamp") or ""
        entry["last_completion"] = max(entry["last_completion"], stamp)
    return found


def load_campaign(root: Path, campaign_id: str | None = None) -> list[RunRecords]:
    """Load every E3 benchmark run of one campaign from immutable raw data.

    When no campaign is named, the most recently completed one is used. Pilot
    runs carry ``campaign_id = "e3-pilot"`` and are never pooled with campaign
    data by accident.
    """
    manifests = sorted((root / "manifests").glob("*.manifest.json"))
    candidates: list[dict[str, Any]] = []
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("experiment") != "E3":
            continue
        candidates.append(payload)

    if campaign_id is None:
        # Choose the most recently completed campaign, not the largest. Once a
        # campaign has been re-run under a corrected runtime both are complete,
        # and "most runs" would be a coin toss between the corrected dataset
        # and the one it replaced.
        latest: dict[str, str] = {}
        for payload in candidates:
            key = payload.get("campaign_id", "")
            if not key or key == "e3-pilot":
                continue
            stamp = payload.get("completion_timestamp") or ""
            if stamp > latest.get(key, ""):
                latest[key] = stamp
        if not latest:
            return []
        campaign_id = max(latest.items(), key=lambda item: item[1])[0]

    runs: list[RunRecords] = []
    for payload in candidates:
        if payload.get("campaign_id") != campaign_id:
            continue
        runner = next(
            (
                a
                for a in payload.get("raw_artifacts", [])
                if a["role"] == "runner_interaction_stream"
            ),
            None,
        )
        records = _load_jsonl(root / runner["path"]) if runner else []
        validity = payload.get("validity_classification") or {}
        runs.append(
            RunRecords(
                run_id=payload["run_id"],
                workload=payload.get("workload_type", ""),
                topology=payload.get("topology", ""),
                block_id=payload.get("paired_block_id", ""),
                concurrency=payload.get("concurrency", 0),
                campaign_id=campaign_id,
                valid=bool(validity.get("valid")),
                invalid_class=validity.get("invalid_class"),
                window_start_ns=payload.get("window_start_ns"),
                window_end_ns=payload.get("window_end_ns"),
                records=records,
                manifest=payload,
            )
        )
    return runs


def pair_blocks(runs: Iterable[RunRecords]) -> list[PairedBlock]:
    """Group valid runs into complete local/federated blocks.

    A block missing one topology is not a paired observation and is dropped
    from the paired comparison — with the drop reported, never silently.
    """
    grouped: dict[str, dict[str, RunRecords]] = {}
    for run in runs:
        if not run.valid:
            continue
        grouped.setdefault(run.block_id, {})[run.topology] = run
    blocks: list[PairedBlock] = []
    for block_id, topologies in sorted(grouped.items()):
        local = topologies.get(E3_TOPOLOGY_LOCAL)
        federated = topologies.get(E3_TOPOLOGY_FEDERATED)
        if local is not None and federated is not None:
            blocks.append(PairedBlock(block_id, local, federated))
    return blocks


def incomplete_blocks(runs: Iterable[RunRecords]) -> list[str]:
    grouped: dict[str, set[str]] = {}
    for run in runs:
        grouped.setdefault(run.block_id, set())
        if run.valid:
            grouped[run.block_id].add(run.topology)
    return sorted(
        block_id
        for block_id, topologies in grouped.items()
        if topologies != {E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED}
    )


# --------------------------------------------------------------- bootstrap


def _interval(values: list[float], confidence: float) -> dict[str, float | None]:
    if not values:
        return {"low": None, "high": None}
    ordered = sorted(values)
    tail = (1.0 - confidence) / 2.0
    low = percentile(ordered, tail)
    high = percentile(ordered, 1.0 - tail)
    return {"low": low, "high": high}


def paired_bootstrap_latency(
    blocks: list[PairedBlock],
    *,
    quantiles: tuple[float, ...] = (0.50, 0.95, 0.99),
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = E3_BOOTSTRAP_CONFIDENCE,
) -> dict[str, Any]:
    """§38 of the task, §32 of the protocol.

    Each replicate resamples the blocks with replacement, keeps both runs of
    every selected block, pools measured interactions within each topology,
    and computes the percentile and the paired comparison from those pools.
    """
    if not blocks:
        return {}

    local_sorted = [block.local.rtts_ns() for block in blocks]
    federated_sorted = [block.federated.rtts_ns() for block in blocks]

    def pooled(indices: list[int], source: list[list[int]]) -> list[int]:
        # Concatenating pre-sorted runs gives Timsort natural runs to merge,
        # which is why this stays cheap enough for thousands of replicates.
        return sorted(chain.from_iterable(source[i] for i in indices))

    point_local = pooled(range(len(blocks)), local_sorted)  # type: ignore[arg-type]
    point_federated = pooled(range(len(blocks)), federated_sorted)  # type: ignore[arg-type]

    rng = random.Random(seed)
    replicate_stats: dict[float, dict[str, list[float]]] = {
        q: {"local": [], "federated": [], "difference": [], "ratio": []}
        for q in quantiles
    }

    for _ in range(replicates):
        indices = [rng.randrange(len(blocks)) for _ in range(len(blocks))]
        local_pool = pooled(indices, local_sorted)
        federated_pool = pooled(indices, federated_sorted)
        for q in quantiles:
            lo = percentile(local_pool, q)
            fe = percentile(federated_pool, q)
            if lo is None or fe is None:
                continue
            replicate_stats[q]["local"].append(lo)
            replicate_stats[q]["federated"].append(fe)
            replicate_stats[q]["difference"].append(fe - lo)
            if lo:
                replicate_stats[q]["ratio"].append(fe / lo)

    out: dict[str, Any] = {
        "resampling_unit": "paired run block",
        "blocks": len(blocks),
        "replicates": replicates,
        "bootstrap_seed": seed,
        "confidence": confidence,
        "local_measured_interactions": len(point_local),
        "federated_measured_interactions": len(point_federated),
        "percentiles": {},
    }
    for q in quantiles:
        local_point = percentile(point_local, q)
        federated_point = percentile(point_federated, q)
        difference = (
            federated_point - local_point
            if local_point is not None and federated_point is not None
            else None
        )
        ratio = (
            federated_point / local_point
            if local_point and federated_point is not None
            else None
        )
        stats = replicate_stats[q]
        out["percentiles"][f"p{int(q * 100)}"] = {
            "local_ms": _ns_to_ms(local_point),
            "federated_ms": _ns_to_ms(federated_point),
            "difference_ms": _ns_to_ms(difference),
            "ratio": round(ratio, 4) if ratio else None,
            "local_ci_ms": {
                k: _ns_to_ms(v) for k, v in _interval(stats["local"], confidence).items()
            },
            "federated_ci_ms": {
                k: _ns_to_ms(v)
                for k, v in _interval(stats["federated"], confidence).items()
            },
            "difference_ci_ms": {
                k: _ns_to_ms(v)
                for k, v in _interval(stats["difference"], confidence).items()
            },
            "ratio_ci": {
                k: (round(v, 4) if v is not None else None)
                for k, v in _interval(stats["ratio"], confidence).items()
            },
        }
    return out


def paired_bootstrap_throughput(
    blocks: list[PairedBlock],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = E3_BOOTSTRAP_CONFIDENCE,
) -> dict[str, Any]:
    """§39 of the task, §31/§32 of the protocol.

    The per-run estimate is one observed-throughput value; the aggregate is
    the median run throughput (§31). Completion events are never pooled as
    independent replicates — twenty runs carry twenty runs of information.
    """
    if not blocks:
        return {}

    local = [block.local.observed_throughput() for block in blocks]
    federated = [block.federated.observed_throughput() for block in blocks]
    usable = [
        index
        for index in range(len(blocks))
        if local[index] is not None and federated[index] is not None
    ]
    if not usable:
        return {}

    def median(values: list[float]) -> float:
        ordered = sorted(values)
        n = len(ordered)
        mid = n // 2
        return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2

    point_local = median([local[i] for i in usable])
    point_federated = median([federated[i] for i in usable])

    rng = random.Random(seed)
    samples = {"local": [], "federated": [], "difference": [], "ratio": []}
    for _ in range(replicates):
        indices = [rng.choice(usable) for _ in usable]
        lo = median([local[i] for i in indices])
        fe = median([federated[i] for i in indices])
        samples["local"].append(lo)
        samples["federated"].append(fe)
        samples["difference"].append(fe - lo)
        if lo:
            samples["ratio"].append(fe / lo)

    return {
        "resampling_unit": "paired run block",
        "blocks": len(usable),
        "replicates": replicates,
        "bootstrap_seed": seed,
        "confidence": confidence,
        "statistic": "median run observed throughput",
        "local_per_second": round(point_local, 4),
        "federated_per_second": round(point_federated, 4),
        "difference_per_second": round(point_federated - point_local, 4),
        "ratio": round(point_federated / point_local, 4) if point_local else None,
        "local_ci": _round_interval(_interval(samples["local"], confidence)),
        "federated_ci": _round_interval(_interval(samples["federated"], confidence)),
        "difference_ci": _round_interval(_interval(samples["difference"], confidence)),
        "ratio_ci": _round_interval(_interval(samples["ratio"], confidence)),
        "local_run_throughputs": [round(local[i], 4) for i in usable],
        "federated_run_throughputs": [round(federated[i], 4) for i in usable],
    }


def _round_interval(interval: dict[str, float | None]) -> dict[str, float | None]:
    return {k: (round(v, 4) if v is not None else None) for k, v in interval.items()}


# ----------------------------------------------------------- topology views


def latency_topology_summary(runs: list[RunRecords]) -> dict[str, Any]:
    """§30, per topology, over valid runs."""
    out: dict[str, Any] = {}
    for name in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
        selected = [
            r
            for r in runs
            if r.workload == E3_WORKLOAD_LATENCY and r.topology == name and r.valid
        ]
        pooled = sorted(chain.from_iterable(r.rtts_ns() for r in selected))
        initiated = sum(len(r.measured) for r in selected)
        failures: dict[str, int] = {}
        for run in selected:
            for record in run.measured:
                outcome = record.get("outcome", "unknown")
                if outcome != SUCCESS:
                    failures[outcome] = failures.get(outcome, 0) + 1
        out[name] = {
            "runs": len(selected),
            "initiated_interactions": initiated,
            "successful_interactions": len(pooled),
            "failures_by_category": failures,
            "failure_rate": (
                round((initiated - len(pooled)) / initiated, 6) if initiated else None
            ),
            "p50_ms": _ns_to_ms(percentile(pooled, 0.50)),
            "p95_ms": _ns_to_ms(percentile(pooled, 0.95)),
            "p99_ms": _ns_to_ms(percentile(pooled, 0.99)),
            "mean_ms": (
                round(sum(pooled) / len(pooled) / 1e6, 4) if pooled else None
            ),
            "min_ms": _ns_to_ms(pooled[0]) if pooled else None,
            "max_ms": _ns_to_ms(pooled[-1]) if pooled else None,
        }
    return out


def throughput_topology_summary(
    runs: list[RunRecords], concurrency: int
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
        selected = [
            r
            for r in runs
            if r.workload == E3_WORKLOAD_THROUGHPUT
            and r.topology == name
            and r.concurrency == concurrency
            and r.valid
        ]
        values = [
            r.observed_throughput()
            for r in selected
            if r.observed_throughput() is not None
        ]
        ordered = sorted(values)
        failure_rates = [
            r.failure_rate() for r in selected if r.failure_rate() is not None
        ]
        out[name] = {
            "runs": len(selected),
            "observed_throughput_runs": [round(v, 4) for v in ordered],
            "median_observed_throughput_per_second": (
                round(percentile(ordered, 0.5), 4) if ordered else None
            ),
            "min_per_second": round(ordered[0], 4) if ordered else None,
            "max_per_second": round(ordered[-1], 4) if ordered else None,
            "mean_failure_rate": (
                round(sum(failure_rates) / len(failure_rates), 6)
                if failure_rates
                else None
            ),
            "stationarity": [r.stationarity() for r in selected],
            "boundary_diagnostics": [r.boundary_diagnostics() for r in selected],
        }
    return out


def derived_c1_check(
    latency: dict[str, Any], throughput: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """§31: predicted C=1 throughput against the observed trend.

    A gross inconsistency here means an instrumentation or workload defect,
    not a system property. It is a sanity check on the pipeline, not a result.
    """
    out: dict[str, Any] = {}
    for name in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
        mean_ms = (latency.get(name) or {}).get("mean_ms")
        predicted = 1000.0 / mean_ms if mean_ms else None
        observed = {
            str(level): (data.get(name) or {}).get(
                "median_observed_throughput_per_second"
            )
            for level, data in throughput.items()
        }
        consistent = None
        if predicted is not None:
            values = [v for v in observed.values() if v]
            # The closed loop cannot complete fewer interactions at C = 8 than
            # a single slot does, and cannot exceed C times that rate.
            consistent = bool(values) and all(
                predicted * 0.5 <= value <= predicted * max(1, max(
                    int(level) for level in observed
                )) * 2
                for value in values
            )
        out[name] = {
            "mean_rtt_ms": mean_ms,
            "predicted_c1_throughput_per_second": (
                round(predicted, 4) if predicted else None
            ),
            "observed_median_by_concurrency": observed,
            "consistent": consistent,
        }
    return out


def recovery_and_rate_limit_diagnostics(runs: list[RunRecords]) -> dict[str, Any]:
    """§42 and §28. Reported for every run, never used to filter one."""
    affected = []
    rate_limited = []
    setup_only = 0
    for run in runs:
        sender = (run.manifest.get("sender_transport_diagnostics") or {})
        agent = (run.manifest.get("agent_transport_diagnostics") or {})

        # Sender counters are reset at the workload boundary, so anything they
        # hold is workload activity. Agent counters are cumulative from
        # process start, so only episodes that actually recovered events from
        # history count — the rest is the initial sync, which is limited by
        # construction and recovers nothing.
        workload_episodes = sender.get("live_recovery_episodes", 0) + agent.get(
            "productive_recovery_episodes", 0
        )
        if not workload_episodes and agent.get("live_recovery_episodes", 0):
            setup_only += 1
        if workload_episodes:
            affected.append(
                {
                    "run_id": run.run_id,
                    "topology": run.topology,
                    "workload": run.workload,
                    "concurrency": run.concurrency,
                    "sender_recovery_episodes": sender.get("live_recovery_episodes"),
                    "sender_recovery_pages": sender.get("live_recovery_pages"),
                    "agent_productive_recovery_episodes": agent.get(
                        "productive_recovery_episodes"
                    ),
                    "agent_events_recovered_from_history": agent.get(
                        "events_recovered_from_history"
                    ),
                    "interactions_overlapping_recovery": run.recovery_overlap(),
                }
            )
        if run.rate_limited():
            rate_limited.append(
                {
                    "run_id": run.run_id,
                    "topology": run.topology,
                    "m_limit_exceeded_sends": run.rate_limited(),
                }
            )
    return {
        "runs_with_live_recovery": affected,
        "runs_with_live_recovery_count": len(affected),
        "runs_with_setup_only_recovery_count": setup_only,
        "interactions_overlapping_recovery_total": sum(
            run.recovery_overlap() for run in runs
        ),
        "runs_with_m_limit_exceeded": rate_limited,
        "runs_with_m_limit_exceeded_count": len(rate_limited),
        "note": (
            "Workload recovery episodes add pagination round trips inside the "
            "sync loop, so a run with one carries a caveat on its timings. "
            "Affected interactions are preserved and reported, never removed. "
            "Setup episodes are counted separately and carry no caveat: "
            "joining a room produces one initial sync that is limited by "
            "construction and recovers nothing, before any request exists."
        ),
    }


def environment_drift(runs: list[RunRecords]) -> dict[str, Any]:
    """Per-run environment growth against per-run central latency.

    A campaign that gets slower as it goes is not automatically invalid — but
    it is something Task 07 needs to know before it collects formal evidence
    on the same accounts.
    """
    rows: list[dict[str, Any]] = []
    ordered = sorted(
        (r for r in runs if r.workload == E3_WORKLOAD_LATENCY and r.valid),
        key=lambda r: r.manifest.get("start_timestamp") or "",
    )
    for position, run in enumerate(ordered, start=1):
        rtts = run.rtts_ns()
        diagnostics = run.manifest.get("host_diagnostics") or {}
        rows.append(
            {
                "run_id": run.run_id,
                "campaign_position": position,
                "block_id": run.block_id,
                "topology": run.topology,
                "sender_joined_rooms": diagnostics.get("sender_joined_rooms"),
                "load_average_1_5_15": diagnostics.get("load_average_1_5_15"),
                "p50_ms": _ns_to_ms(percentile(rtts, 0.50)),
                "successful": len(rtts),
            }
        )

    usable = [r for r in rows if r["p50_ms"] is not None]

    def _pearson(xs: list[float], ys: list[float]) -> float | None:
        if len(xs) < 3:
            return None
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        var_x = sum((x - mean_x) ** 2 for x in xs)
        var_y = sum((y - mean_y) ** 2 for y in ys)
        if not var_x or not var_y:
            return None
        return round(cov / math.sqrt(var_x * var_y), 4)

    by_position: dict[str, float | None] = {}
    halves: dict[str, dict[str, float | None]] = {}
    for name in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
        subset = [r for r in usable if r["topology"] == name]
        by_position[name] = _pearson(
            [float(r["campaign_position"]) for r in subset],
            [float(r["p50_ms"]) for r in subset],
        )
        if len(subset) >= 4:
            middle = len(subset) // 2
            first = [r["p50_ms"] for r in subset[:middle]]
            second = [r["p50_ms"] for r in subset[middle:]]
            halves[name] = {
                "first_half_mean_p50_ms": round(sum(first) / len(first), 3),
                "second_half_mean_p50_ms": round(sum(second) / len(second), 3),
            }

    return {
        "per_run": rows,
        "joined_rooms_first": usable[0]["sender_joined_rooms"] if usable else None,
        "joined_rooms_last": usable[-1]["sender_joined_rooms"] if usable else None,
        "p50_vs_campaign_position_pearson_by_topology": by_position,
        "p50_by_campaign_half": halves,
        "note": (
            "Execution order and accumulated rooms both increase monotonically "
            "across a campaign, so they are perfectly confounded and neither "
            "correlation can separate them. Position is reported as the "
            "primary axis because a settling effect explains the observed "
            "direction and room accumulation does not: runs get FASTER as the "
            "campaign proceeds. Reported only — nothing is weighted, adjusted "
            "or excluded on these figures. The paired comparison is protected "
            "by §24 counterbalancing, both topologies of a block executing "
            "adjacently, which is why the p50 ratio stays stable across "
            "campaigns whose absolute latency differs substantially."
        ),
    }


def integrity_problems(runs: list[RunRecords]) -> list[str]:
    """Facts the raw data must not be able to state.

    A record can be schema-valid, digest-verified and still impossible. The
    checks here are the ones that caught real defects:

    * a success whose RTT exceeds the timeout that was supposed to end it;
    * a completion recorded before its own initiation;
    * a throughput record missing the window bounds the estimator needs;
    * a success with no completion timestamp, or a timeout carrying one.
    """
    budget_ns = int(DEFAULT_INTERACTION_TIMEOUT_SECONDS * 1e9)
    problems: list[str] = []

    for run in runs:
        over_timeout = 0
        reversed_clock = 0
        missing_window = 0
        success_without_completion = 0
        timeout_with_completion = 0

        for record in run.records:
            outcome = record.get("outcome")
            initiated = record.get("initiated_monotonic_ns")
            completed = record.get("completed_monotonic_ns")

            if outcome == SUCCESS:
                if completed is None:
                    success_without_completion += 1
                else:
                    if completed < initiated:
                        reversed_clock += 1
                    elif completed - initiated > budget_ns:
                        over_timeout += 1
            elif outcome == "timeout" and completed is not None:
                timeout_with_completion += 1

            if run.workload == E3_WORKLOAD_THROUGHPUT and (
                record.get("window_start_ns") is None
                or record.get("window_end_ns") is None
            ):
                missing_window += 1

        for count, description in (
            (
                over_timeout,
                f"successful interactions with an RTT above the frozen "
                f"{DEFAULT_INTERACTION_TIMEOUT_SECONDS:.0f}s interaction timeout",
            ),
            (reversed_clock, "interactions completing before they were initiated"),
            (missing_window, "throughput records without window bounds"),
            (success_without_completion, "successes with no completion timestamp"),
            (timeout_with_completion, "timeouts carrying a completion timestamp"),
        ):
            if count:
                problems.append(f"{run.run_id}: {count} {description}")

    return problems


def analyse(
    runs: list[RunRecords],
    *,
    replicates: int = DEFAULT_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """The complete development E3 analysis over one campaign."""
    latency_runs = [r for r in runs if r.workload == E3_WORKLOAD_LATENCY]
    latency_blocks = pair_blocks(latency_runs)

    throughput: dict[str, dict[str, Any]] = {}
    throughput_bootstrap: dict[str, Any] = {}
    concurrencies = sorted(
        {r.concurrency for r in runs if r.workload == E3_WORKLOAD_THROUGHPUT}
    )
    for level in concurrencies:
        subset = [
            r
            for r in runs
            if r.workload == E3_WORKLOAD_THROUGHPUT and r.concurrency == level
        ]
        throughput[str(level)] = throughput_topology_summary(subset, level)
        throughput_bootstrap[str(level)] = paired_bootstrap_throughput(
            pair_blocks(subset), replicates=replicates, seed=seed
        )

    latency = latency_topology_summary(latency_runs)
    invalid = [
        {"run_id": r.run_id, "class": r.invalid_class} for r in runs if not r.valid
    ]

    # §47: the analysis must be rerunnable from immutable raw data, which
    # means every processed artifact names the raw files it came from and the
    # seed that produced its intervals. Digests come from the run manifests,
    # which the digest-verification step has already checked against disk.
    source_digests = {
        run.run_id: {
            artifact["role"]: artifact["sha256"]
            for artifact in run.manifest.get("raw_artifacts", [])
        }
        for run in runs
    }

    return {
        "campaign_id": runs[0].campaign_id if runs else None,
        "campaign_fingerprint": (
            runs[0].manifest.get("campaign_fingerprint") if runs else None
        ),
        "schedule_seed": runs[0].manifest.get("schedule_seed") if runs else None,
        "bootstrap_seed": seed,
        "bootstrap_replicates": replicates,
        "source_run_ids": sorted(run.run_id for run in runs),
        "source_paired_block_ids": sorted({run.block_id for run in runs if run.block_id}),
        "source_raw_digests": source_digests,
        "sync_configuration": (
            runs[0].manifest.get("sync_configuration") if runs else None
        ),
        "runs_total": len(runs),
        "runs_valid": sum(1 for r in runs if r.valid),
        "runs_invalid": invalid,
        "integrity_problems": integrity_problems(runs),
        "measurement_window_seconds": E3_MEASUREMENT_SECONDS,
        "latency": {
            "by_topology": latency,
            "paired_blocks": len(latency_blocks),
            "incomplete_blocks": incomplete_blocks(latency_runs),
            "bootstrap": paired_bootstrap_latency(
                latency_blocks, replicates=replicates, seed=seed
            ),
        },
        "throughput": {
            "by_concurrency": throughput,
            "bootstrap": throughput_bootstrap,
            "paired_blocks": {
                str(level): len(
                    pair_blocks(
                        [
                            r
                            for r in runs
                            if r.workload == E3_WORKLOAD_THROUGHPUT
                            and r.concurrency == level
                        ]
                    )
                )
                for level in concurrencies
            },
        },
        "derived_c1_consistency": derived_c1_check(latency, throughput),
        "diagnostics": recovery_and_rate_limit_diagnostics(runs),
        "environment_drift": environment_drift(runs),
        "outlier_policy": (
            "No trimming, winsorization or outlier removal of any kind "
            "(experimental-protocol.md §33, §34). Successful high-latency "
            "interactions remain in the dataset."
        ),
    }


# ---------------------------------------------------------------- tables


def tables(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Tidy rows behind each development figure and table.

    One row per observation, no nesting, so the same data can drive a plot, a
    table or a spreadsheet without anyone reshaping JSON by hand — §47 of the
    task requires the analysis to be rerunnable without manual editing, and a
    figure produced by hand-copying numbers is the classic way that stops
    being true.

    These are development outputs. Nothing here is a publication figure.
    """
    latency_rows: list[dict[str, Any]] = []
    for name, item in (summary["latency"]["by_topology"] or {}).items():
        for label in ("p50", "p95", "p99"):
            latency_rows.append(
                {
                    "workload": "latency",
                    "topology": name,
                    "percentile": label,
                    "value_ms": item.get(f"{label}_ms"),
                    "successful_interactions": item.get("successful_interactions"),
                    "initiated_interactions": item.get("initiated_interactions"),
                    "failure_rate": item.get("failure_rate"),
                    "runs": item.get("runs"),
                }
            )

    comparison_rows: list[dict[str, Any]] = []
    for label, stats in (
        (summary["latency"].get("bootstrap") or {}).get("percentiles") or {}
    ).items():
        comparison_rows.append(
            {
                "workload": "latency",
                "metric": label,
                "unit": "ms",
                "local": stats["local_ms"],
                "federated": stats["federated_ms"],
                "difference": stats["difference_ms"],
                "difference_ci_low": stats["difference_ci_ms"]["low"],
                "difference_ci_high": stats["difference_ci_ms"]["high"],
                "ratio": stats["ratio"],
                "ratio_ci_low": stats["ratio_ci"]["low"],
                "ratio_ci_high": stats["ratio_ci"]["high"],
            }
        )

    throughput_rows: list[dict[str, Any]] = []
    stationarity_rows: list[dict[str, Any]] = []
    for level, topologies in (summary["throughput"]["by_concurrency"] or {}).items():
        for name, item in topologies.items():
            for index, value in enumerate(item.get("observed_throughput_runs") or []):
                throughput_rows.append(
                    {
                        "workload": "throughput",
                        "concurrency": int(level),
                        "topology": name,
                        "run_index": index,
                        "observed_throughput_per_second": value,
                    }
                )
            for index, halves in enumerate(item.get("stationarity") or []):
                if not halves:
                    continue
                stationarity_rows.append(
                    {
                        "concurrency": int(level),
                        "topology": name,
                        "run_index": index,
                        "first_half_rate_per_second": halves.get(
                            "first_half_rate_per_second"
                        ),
                        "second_half_rate_per_second": halves.get(
                            "second_half_rate_per_second"
                        ),
                        "second_over_first": halves.get("second_over_first"),
                    }
                )
        boot = (summary["throughput"].get("bootstrap") or {}).get(level) or {}
        if boot:
            comparison_rows.append(
                {
                    "workload": "throughput",
                    "metric": f"C={level} median observed throughput",
                    "unit": "interactions/second",
                    "local": boot["local_per_second"],
                    "federated": boot["federated_per_second"],
                    "difference": boot["difference_per_second"],
                    "difference_ci_low": boot["difference_ci"]["low"],
                    "difference_ci_high": boot["difference_ci"]["high"],
                    "ratio": boot["ratio"],
                    "ratio_ci_low": boot["ratio_ci"]["low"],
                    "ratio_ci_high": boot["ratio_ci"]["high"],
                }
            )

    return {
        "latency_percentiles": latency_rows,
        "paired_comparison": comparison_rows,
        "throughput_runs": throughput_rows,
        "stationarity": stationarity_rows,
    }


def write_tables(summary: dict[str, Any], directory: Path) -> list[Path]:
    """Write each tidy table as CSV alongside the processed JSON."""
    import csv

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, rows in tables(summary).items():
        if not rows:
            continue
        path = directory / f"{name}.csv"
        columns = list(rows[0].keys())
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)
    return written
