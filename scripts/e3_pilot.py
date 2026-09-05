#!/usr/bin/env python3
"""E3 pilot — validate benchmark mechanics before the development campaign.

experimental-protocol.md §3 Phase 2 and Task 05 §10/§43. The pilot exists to
find out whether the benchmark measures what it claims to, *before* 120 runs
are spent finding out otherwise.

It confirms:

    the 256-byte body assertion fires on every send
    T0/T3 produce plausible, ordered, single-clock RTTs
    local and federated runs execute through the same code path
    C = 8 and C = 32 hold their concurrency closed-loop
    the selected sync timeline limit does not truncate under ordinary load
    the frozen rate limits stay non-binding
    10 s of warm-up reaches an operationally stable regime
    manifests and raw records validate against the frozen schemas
    schedule generation is deterministic

Pilot runs are ``publication_data = false`` and are not E3 repetitions. They
are written under ``raw/e3/pilot/`` so they cannot be mistaken for campaign
data by anything that walks the frozen E3 tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from fam.benchmark.runner import BenchmarkRun, execute_benchmark_run  # noqa: E402
from fam.benchmark.schedule import (  # noqa: E402
    DEFAULT_SCHEDULE_SEED,
    ScheduledRun,
    generate_campaign_schedule,
)
from fam.common.digests import file_sha256  # noqa: E402
from fam.common.env import protocol_git_commit, publication_data  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    E3_BODY_BYTES,
    E3_CONCURRENCY_LEVELS,
    E3_INTER_RUN_IDLE_SECONDS,
    E3_MEASUREMENT_SECONDS,
    E3_TOPOLOGY_FEDERATED,
    E3_TOPOLOGY_LOCAL,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
    EXECUTION_ANALYSIS_SPEC_VERSION,
)
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)
from fam.common.validity import InvalidRun, InvalidRunClass  # noqa: E402

ANALYSIS_CODE_COMMIT = "task-05-working-tree"

#: Candidate development sync timeline limit. Chosen far above the
#: bounded-concurrency envelope rather than just above it: at C = 32 both
#: directions of every interaction land in the same room, so timeline
#: occupancy tracks roughly 2C plus whatever accumulates while a sync is in
#: flight. The pilot measures actual occupancy and confirms the headroom.
CANDIDATE_TIMELINE_LIMIT = int(os.environ.get("FAM_E3_TIMELINE_LIMIT", "500"))
SYNC_TIMEOUT_MS = int(os.environ.get("FAM_E3_SYNC_TIMEOUT_MS", "30000"))

#: Latency pilot is shortened. It validates instrumentation, not statistics —
#: the campaign supplies the 500-interaction runs.
PILOT_LATENCY_WARMUP = int(os.environ.get("FAM_E3_PILOT_LATENCY_WARMUP", "10"))
PILOT_LATENCY_MEASURED = int(os.environ.get("FAM_E3_PILOT_LATENCY_MEASURED", "50"))

#: Throughput pilot uses the frozen timings. Shortening the window would
#: destroy the one thing only the pilot can answer: whether 10 s of warm-up
#: reaches steady state before the window opens.
PILOT_WARMUP_SECONDS = float(os.environ.get("FAM_E3_PILOT_WARMUP_S", "10"))
PILOT_MEASUREMENT_SECONDS = float(
    os.environ.get("FAM_E3_PILOT_MEASUREMENT_S", str(E3_MEASUREMENT_SECONDS))
)
PILOT_DRAIN_SECONDS = float(os.environ.get("FAM_E3_PILOT_DRAIN_S", "10"))


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _environment(root: Path) -> dict[str, Any]:
    path = environment_dir(root) / "environment-latest.json"
    if not path.exists():
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"{path} not found; run `make setup` and `make verify` first",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _rate_limits(environment: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for domain, config in (environment.get("sanitized_config") or {}).items():
        out[domain] = {k: v for k, v in config.items() if k.startswith("rc_")}
    return out


# ------------------------------------------------------------------- checks


class Findings:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.entries.append({"check": name, "ok": bool(ok), "detail": detail})
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return all(entry["ok"] for entry in self.entries)

    @property
    def failures(self) -> list[str]:
        return [e["check"] for e in self.entries if not e["ok"]]


def check_schedule_determinism(findings: Findings) -> dict[str, Any]:
    print("\n1. Schedule generation")
    first = generate_campaign_schedule(seed=DEFAULT_SCHEDULE_SEED)
    second = generate_campaign_schedule(seed=DEFAULT_SCHEDULE_SEED)
    other = generate_campaign_schedule(seed=DEFAULT_SCHEDULE_SEED + 1)

    findings.record(
        "schedule is deterministic for a fixed seed",
        first == second,
        f"{len(first)} runs",
    )
    findings.record(
        "a different seed produces a different order",
        [r.topology for r in first] != [r.topology for r in other],
    )

    latency = [r for r in first if r.workload == E3_WORKLOAD_LATENCY]
    leaders = [
        r.topology for r in latency if r.within_block_order == 1
    ]
    local_first = leaders.count(E3_TOPOLOGY_LOCAL)
    findings.record(
        "latency blocks are counterbalanced",
        local_first == len(leaders) - local_first,
        f"{local_first} local-first, {len(leaders) - local_first} federated-first",
    )

    blocks: dict[str, set[str]] = {}
    for run in first:
        blocks.setdefault(run.block_id, set()).add(run.topology)
    findings.record(
        "every block contains exactly one local and one federated run",
        all(
            names == {E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED}
            for names in blocks.values()
        ),
        f"{len(blocks)} blocks",
    )
    return {
        "seed": DEFAULT_SCHEDULE_SEED,
        "total_runs": len(first),
        "blocks": len(blocks),
        "latency_local_first": local_first,
    }


def _rtts(records: list[dict], phases: tuple[str, ...]) -> list[int]:
    return [
        r["completed_monotonic_ns"] - r["initiated_monotonic_ns"]
        for r in records
        if r.get("phase") in phases
        and r.get("outcome") == "success"
        and r.get("completed_monotonic_ns") is not None
    ]


def check_latency_instrumentation(
    findings: Findings, runs: dict[str, BenchmarkRun]
) -> dict[str, Any]:
    print("\n3. Latency instrumentation (T0/T3)")
    summary: dict[str, Any] = {}
    for name, run in runs.items():
        workload = run.workload_result
        records = workload.records if workload else []
        measured = [r for r in records if r.get("phase") == "measured"]
        rtts = _rtts(records, ("measured",))

        findings.record(
            f"{name}: measured interaction count",
            len(measured) == PILOT_LATENCY_MEASURED,
            f"{len(measured)}/{PILOT_LATENCY_MEASURED}",
        )
        findings.record(
            f"{name}: every measured body is exactly {E3_BODY_BYTES} bytes",
            all(r.get("message_body_bytes") == E3_BODY_BYTES for r in measured),
        )
        findings.record(
            f"{name}: every RTT is strictly positive and below the timeout",
            bool(rtts) and all(0 < v < 10_000_000_000 for v in rtts),
            f"n={len(rtts)}",
        )
        findings.record(
            f"{name}: warm-up is excluded from the measured phase",
            all(r.get("phase") != "warmup" for r in measured),
        )
        findings.record(
            f"{name}: max_in_flight = 1 held (no overlapping interactions)",
            _no_overlap(measured),
        )
        summary[name] = {
            "measured": len(measured),
            "successful": len(rtts),
            "p50_ms": round(statistics.median(rtts) / 1e6, 3) if rtts else None,
            "min_ms": round(min(rtts) / 1e6, 3) if rtts else None,
            "max_ms": round(max(rtts) / 1e6, 3) if rtts else None,
        }
    return summary


def _no_overlap(records: list[dict]) -> bool:
    """With max_in_flight = 1 no interaction may start before the last ended."""
    ordered = sorted(records, key=lambda r: r["initiated_monotonic_ns"])
    previous_end = 0
    for record in ordered:
        if record["initiated_monotonic_ns"] < previous_end:
            return False
        end = record.get("completed_monotonic_ns")
        previous_end = end if end is not None else record["initiated_monotonic_ns"]
    return True


def _completion_series(
    records: list[dict], loop_start: int, bucket_seconds: float = 5.0
) -> list[dict[str, Any]]:
    """Successful completions per fixed bucket across the whole run.

    Recorded so that the stationarity question can be re-examined from the
    shape rather than from a single summary ratio, which at C = 32 is mostly
    noise on this host.
    """
    completions = sorted(
        r["completed_monotonic_ns"]
        for r in records
        if r.get("outcome") == "success" and r.get("completed_monotonic_ns")
    )
    if not completions:
        return []
    width = int(bucket_seconds * 1e9)
    series: list[dict[str, Any]] = []
    index = 0
    while loop_start + index * width <= completions[-1]:
        low = loop_start + index * width
        high = low + width
        series.append(
            {
                "from_seconds": round(index * bucket_seconds, 1),
                "completions": sum(1 for t in completions if low <= t < high),
            }
        )
        index += 1
    return series


def _stationarity(records: list[dict], start: int, end: int) -> dict[str, Any]:
    """First-half against second-half completion rate, §22 and §25."""
    midpoint = start + (end - start) // 2
    first = sum(
        1
        for r in records
        if r.get("outcome") == "success"
        and r.get("completed_monotonic_ns") is not None
        and start <= r["completed_monotonic_ns"] < midpoint
    )
    second = sum(
        1
        for r in records
        if r.get("outcome") == "success"
        and r.get("completed_monotonic_ns") is not None
        and midpoint <= r["completed_monotonic_ns"] < end
    )
    half = (end - start) / 2e9
    first_rate = first / half if half else 0.0
    second_rate = second / half if half else 0.0
    ratio = (second_rate / first_rate) if first_rate else None
    return {
        "first_half_completions": first,
        "second_half_completions": second,
        "first_half_rate_per_second": round(first_rate, 3),
        "second_half_rate_per_second": round(second_rate, 3),
        "second_over_first": round(ratio, 4) if ratio is not None else None,
    }


def check_throughput(
    findings: Findings, runs: dict[str, BenchmarkRun]
) -> dict[str, Any]:
    print("\n4. Throughput mechanics")
    summary: dict[str, Any] = {}
    for name, run in runs.items():
        workload = run.workload_result
        records = workload.records if workload else []
        start = workload.window_start_ns if workload else 0
        end = workload.window_end_ns if workload else 0

        counted = [
            r
            for r in records
            if r.get("outcome") == "success"
            and r.get("completed_monotonic_ns") is not None
            and start <= r["completed_monotonic_ns"] < end
        ]
        observed = len(counted) / ((end - start) / 1e9) if end > start else 0.0
        stationarity = _stationarity(records, start, end)

        findings.record(
            f"{name}: closed loop produced traffic in every phase",
            all(
                any(r.get("phase") == phase for r in records)
                for phase in ("warmup", "window")
            ),
            f"{len(records)} interactions",
        )
        findings.record(
            f"{name}: concurrency was held at C",
            _peak_concurrency(records) >= run.scheduled.concurrency * 0.75,
            f"observed peak in-flight ~{_peak_concurrency(records)} "
            f"of C={run.scheduled.concurrency}",
        )
        findings.record(
            f"{name}: the window is exactly the frozen duration",
            abs((end - start) / 1e9 - PILOT_MEASUREMENT_SECONDS) < 0.05,
            f"{(end - start) / 1e9:.3f}s",
        )
        # Mandatory reported diagnostic (§22, §25). Deliberately not a
        # pass/fail gate on its own: the protocol defines no threshold, and
        # inventing one would fail runs for ordinary variance.
        ratio = stationarity["second_over_first"]
        print(
            f"        stationarity second/first = {ratio} "
            f"({stationarity['first_half_completions']} then "
            f"{stationarity['second_half_completions']} completions)"
        )

        warmup_rate = _warmup_rate(records, workload)
        window_rate = len(counted) / ((end - start) / 1e9) if end > start else 0.0
        findings.record(
            f"{name}: warm-up reached the window completion rate",
            warmup_rate >= window_rate * 0.75,
            f"warm-up {warmup_rate:.2f}/s vs window {window_rate:.2f}/s "
            "— a warm-up that was too short completes more slowly than the "
            "window, not faster",
        )

        summary[name] = {
            "completion_series_5s": _completion_series(
                records, workload.loop_start_ns if workload else start
            ),
            "topology": run.scheduled.topology,
            "concurrency": run.scheduled.concurrency,
            "interactions": len(records),
            "counted_in_window": len(counted),
            "observed_throughput_per_second": round(observed, 3),
            "outstanding_at_window_start": (
                workload.outstanding_at_window_start if workload else 0
            ),
            "outstanding_at_window_end": (
                workload.outstanding_at_window_end if workload else 0
            ),
            "drain_completions": sum(1 for r in records if r.get("phase") == "drain"),
            "timeouts": sum(1 for r in records if r.get("outcome") == "timeout"),
            "send_errors": workload.send_errors if workload else 0,
            "rate_limited_sends": workload.rate_limited_sends if workload else 0,
            **stationarity,
        }
    ratios = [
        item["second_over_first"]
        for item in summary.values()
        if item.get("second_over_first") is not None
    ]
    if ratios:
        rising = sum(1 for r in ratios if r > 1)
        falling = len(ratios) - rising
        findings.record(
            "no systematic ramp or collapse across the pilot runs",
            not (rising == len(ratios) or falling == len(ratios)) or len(ratios) < 2,
            f"{rising} runs rising, {falling} falling "
            f"(ratios {[round(r, 3) for r in ratios]}); a systematic effect "
            "points the same way in every run, noise does not",
        )
    return summary


def _warmup_rate(records: list[dict], workload) -> float:
    """Successful completions per second before the window opened."""
    if workload is None or workload.window_start_ns is None:
        return 0.0
    start = workload.loop_start_ns or 0
    end = workload.window_start_ns
    if end <= start:
        return 0.0
    completed = sum(
        1
        for r in records
        if r.get("outcome") == "success"
        and r.get("completed_monotonic_ns") is not None
        and start <= r["completed_monotonic_ns"] < end
    )
    return completed / ((end - start) / 1e9)


def _peak_concurrency(records: list[dict]) -> int:
    """Maximum simultaneous in-flight interactions, from raw timestamps."""
    events: list[tuple[int, int]] = []
    for record in records:
        start = record.get("initiated_monotonic_ns")
        end = record.get("completed_monotonic_ns")
        if start is None:
            continue
        events.append((start, 1))
        events.append((end if end is not None else start, -1))
    events.sort()
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def check_sync_configuration(
    findings: Findings, runs: dict[str, BenchmarkRun]
) -> dict[str, Any]:
    """§11: choose the limit from observed occupancy, with margin.

    Only the workload period counts. Room creation, the agent join and the
    initial sync all produce ordinary transport activity — an initial sync is
    limited by construction, because everything before it is a gap — and
    folding that into the workload figures would condemn a sync configuration
    that never truncates a single measured interaction.
    """
    print("\n5. Sync timeline limit")
    occupancy: dict[str, Any] = {}
    worst = 0
    truncated = 0
    workload_episodes = 0
    setup_episodes = 0
    recovered_events = 0
    overlapping = 0

    for name, run in runs.items():
        sender = run.sender_transport
        setup = run.setup_transport
        agent = run.agent_transport
        workload = run.workload_result

        worst = max(worst, sender.get("max_timeline_events_observed", 0))
        truncated += sender.get("limited_syncs_observed", 0)
        workload_episodes += sender.get("live_recovery_episodes", 0)
        workload_episodes += agent.get("productive_recovery_episodes", 0)
        setup_episodes += setup.get("live_recovery_episodes", 0)
        setup_episodes += agent.get("setup_recovery_episodes", 0)
        recovered_events += agent.get("events_recovered_from_history", 0)
        affected = sum(
            1
            for record in (workload.records if workload else [])
            if record.get("live_recovery_episode")
        )
        overlapping += affected

        occupancy[name] = {
            "workload_max_timeline_events": sender.get("max_timeline_events_observed"),
            "workload_max_message_events": sender.get("max_message_events_observed"),
            "workload_sync_slices": sender.get("sync_slices_observed"),
            "workload_limited_syncs": sender.get("limited_syncs_observed"),
            "workload_sender_recovery_episodes": sender.get("live_recovery_episodes"),
            "setup_sender_recovery_episodes": setup.get("live_recovery_episodes"),
            "setup_max_timeline_events": setup.get("max_timeline_events_observed"),
            "agent_recovery_episodes_total": agent.get("live_recovery_episodes"),
            "agent_setup_recovery_episodes": agent.get("setup_recovery_episodes"),
            "agent_productive_recovery_episodes": agent.get(
                "productive_recovery_episodes"
            ),
            "agent_events_recovered_from_history": agent.get(
                "events_recovered_from_history"
            ),
            "interactions_overlapping_recovery": affected,
        }

    findings.record(
        f"selected timeline limit {CANDIDATE_TIMELINE_LIMIT} was never reached",
        worst < CANDIDATE_TIMELINE_LIMIT,
        f"maximum workload timeline occupancy {worst} events",
    )
    findings.record(
        "no sync timeline was truncated during the workload",
        truncated == 0,
        f"{truncated} limited syncs in the workload period "
        f"({setup_episodes} during setup, which is expected)",
    )
    findings.record(
        "live gap recovery stayed off the measurement path",
        workload_episodes == 0 and recovered_events == 0,
        f"{workload_episodes} workload episodes, "
        f"{recovered_events} events recovered through history",
    )
    findings.record(
        "no measured interaction was delivered through gap recovery",
        overlapping == 0,
        f"{overlapping} interactions overlap a recovery episode",
    )
    headroom = round(CANDIDATE_TIMELINE_LIMIT / worst, 1) if worst else None
    findings.record(
        "the limit retains clear operational headroom",
        headroom is None or headroom >= 4,
        f"{headroom}x the observed workload maximum"
        if headroom
        else "no workload occupancy observed",
    )
    return {
        "selected_timeline_limit": CANDIDATE_TIMELINE_LIMIT,
        "sync_timeout_ms": SYNC_TIMEOUT_MS,
        "max_workload_timeline_events_observed": worst,
        "headroom_factor": headroom,
        "workload_limited_syncs": truncated,
        "workload_recovery_episodes": workload_episodes,
        "setup_recovery_episodes": setup_episodes,
        "events_recovered_from_history": recovered_events,
        "interactions_overlapping_recovery": overlapping,
        "per_run": occupancy,
        "note": (
            "Setup episodes are expected: an initial sync is limited by "
            "construction. Only the workload figures bear on whether the sync "
            "configuration puts gap recovery on the measurement path."
        ),
    }


def check_rate_limits(
    findings: Findings, rate_limits: dict[str, Any], runs: dict[str, BenchmarkRun]
) -> dict[str, Any]:
    print("\n6. Rate-limit behaviour")
    peak_observed = 0.0
    limited = 0
    codes: set[str] = set()
    for run in runs.values():
        workload = run.workload_result
        if workload is None:
            continue
        limited += workload.rate_limited_sends
        codes.update(workload.rate_limit_errcodes)
        start, end = workload.window_start_ns, workload.window_end_ns
        if start and end and end > start:
            counted = sum(
                1
                for r in workload.records
                if r.get("completed_monotonic_ns") is not None
                and start <= r["completed_monotonic_ns"] < end
            )
            peak_observed = max(peak_observed, counted / ((end - start) / 1e9))

    configured = min(
        (limits.get("rc_message") or {}).get("per_second", 0)
        for limits in rate_limits.values()
    ) if rate_limits else 0

    findings.record(
        "no M_LIMIT_EXCEEDED occurred",
        limited == 0,
        f"{limited} rate-limited sends; errcodes={sorted(codes) or 'none'}",
    )
    findings.record(
        "the frozen rc_message limit exceeds the observed offered load",
        configured > peak_observed,
        f"configured {configured}/s vs observed ~{peak_observed:.1f} "
        "completions/s per direction",
    )
    return {
        "configured_rc_message_per_second": configured,
        "peak_observed_completions_per_second": round(peak_observed, 2),
        "rate_limited_sends": limited,
        "errcodes": sorted(codes),
    }


def check_schemas(findings: Findings, runs: dict[str, BenchmarkRun]) -> dict[str, Any]:
    """§43: raw records and manifests validate against the frozen schemas.

    Checked here rather than left to `make analyse` so the pilot can be run
    on its own and still answer whether the campaign will produce loadable
    evidence. A schema defect found after 120 runs is 120 runs wasted.
    """
    print("\n8. Schemas and manifests")
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - jsonschema is a pinned dependency
        findings.record("jsonschema available", False, "not installed")
        return {}

    schema_dir = Path("/app/results/schemas")
    validators = {}
    for key, name in (
        ("runner", "raw-runner-record.schema.json"),
        ("manifest", "run-manifest.schema.json"),
    ):
        path = schema_dir / name
        if not path.exists():
            findings.record(f"schema present: {name}", False, str(path))
            continue
        validators[key] = Draft202012Validator(
            json.loads(path.read_text(encoding="utf-8"))
        )

    checked = {"records": 0, "manifests": 0}
    problems: list[str] = []

    for run in runs.values():
        if run.runner_stream and run.runner_stream.exists() and "runner" in validators:
            for number, line in enumerate(
                run.runner_stream.read_text(encoding="utf-8").splitlines(), 1
            ):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                checked["records"] += 1
                errors = sorted(
                    validators["runner"].iter_errors(record), key=lambda e: e.path
                )
                if errors:
                    problems.append(
                        f"{run.runner_stream.name}:{number} {errors[0].message}"
                    )
                    break
        if run.manifest_path and "manifest" in validators:
            manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
            checked["manifests"] += 1
            errors = sorted(
                validators["manifest"].iter_errors(manifest), key=lambda e: e.path
            )
            if errors:
                problems.append(f"{run.manifest_path.name} {errors[0].message}")

    findings.record(
        "every raw record and manifest validates",
        not problems,
        f"{checked['records']} records, {checked['manifests']} manifests"
        + (f"; first problem: {problems[0]}" if problems else ""),
    )

    # The estimator must stay derived: a raw record that carries the answer
    # would bind immutable evidence to one revision of the analysis spec.
    leaked = 0
    for run in runs.values():
        if not (run.runner_stream and run.runner_stream.exists()):
            continue
        for line in run.runner_stream.read_text(encoding="utf-8").splitlines():
            if line.strip() and "counted_in_window" in line:
                leaked += 1
    findings.record(
        "no raw record persists a derived analytical field",
        leaked == 0,
        f"{leaked} records carry counted_in_window",
    )

    required = (
        "window_start_ns",
        "window_end_ns",
        "phase",
        "block_id",
        "workload",
        "concurrency",
        "message_body_bytes",
    )
    missing: set[str] = set()
    for name, run in runs.items():
        if run.scheduled.workload != E3_WORKLOAD_THROUGHPUT:
            continue
        for record in (run.workload_result.records if run.workload_result else [])[:1]:
            missing |= {field for field in required if field not in record}
    findings.record(
        "throughput records carry every field the estimator needs",
        not missing,
        f"missing {sorted(missing)}" if missing else "all present",
    )
    return {"validated": checked, "problems": problems, "leaked_derived_fields": leaked}


def check_symmetry(findings: Findings, runs: dict[str, BenchmarkRun]) -> None:
    print("\n7. Local/federated symmetry")
    manifests = {}
    for name, run in runs.items():
        if run.manifest_path is None:
            continue
        manifests[name] = json.loads(run.manifest_path.read_text(encoding="utf-8"))

    comparable_keys = (
        "message_body_bytes",
        "interaction_timeout_seconds",
        "sync_configuration",
        "raw_schema_version",
        "room_version",
        "execution_protocol_version",
    )
    groups: dict[str, list[tuple[str, Any]]] = {key: [] for key in comparable_keys}
    for name, manifest in manifests.items():
        for key in comparable_keys:
            groups[key].append((name, manifest.get(key)))

    for key, values in groups.items():
        distinct = {json.dumps(value, sort_keys=True) for _, value in values}
        findings.record(
            f"identical across topologies: {key}",
            len(distinct) == 1,
            next(iter(distinct)) if len(distinct) == 1 else str(sorted(distinct)),
        )

    topologies = {m.get("topology") for m in manifests.values()}
    findings.record(
        "both topologies executed",
        topologies == {E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED},
        str(sorted(topologies)),
    )

    agents = {m.get("topology"): m["participants"]["benchmark_agent"] for m in manifests.values()}
    senders = {m["participants"]["benchmark_sender"] for m in manifests.values()}
    findings.record(
        "only the agent identity differs between topologies",
        len(senders) == 1 and len(set(agents.values())) == 2,
        f"sender={senders}, agents={agents}",
    )


# --------------------------------------------------------------------- main


async def _pilot_run(
    *,
    root: Path,
    workload: str,
    topology: str,
    concurrency: int,
    stamp: str,
    rate_limits: dict[str, Any],
) -> BenchmarkRun:
    scheduled = ScheduledRun(
        workload=workload,
        block_id=f"pilot-{workload[:3]}-c{concurrency:02d}",
        block_index=0,
        within_block_order=1,
        topology=topology,
        concurrency=concurrency,
    )
    run_id = f"e3pilot-{workload[:3]}-c{concurrency:02d}-{topology}-{stamp}"
    print(f"\n  running {run_id}")
    return await execute_benchmark_run(
        scheduled=scheduled,
        run_id=run_id,
        root=root,
        sync_timeline_limit=CANDIDATE_TIMELINE_LIMIT,
        sync_timeout_ms=SYNC_TIMEOUT_MS,
        campaign_id="e3-pilot",
        campaign_fingerprint="pilot",
        schedule_seed=DEFAULT_SCHEDULE_SEED,
        rate_limit_reference=rate_limits,
        environment_manifest="environment/environment-latest.json",
        warmup_interactions=PILOT_LATENCY_WARMUP,
        measured_interactions=PILOT_LATENCY_MEASURED,
        warmup_seconds=PILOT_WARMUP_SECONDS,
        measurement_seconds=PILOT_MEASUREMENT_SECONDS,
        drain_seconds=PILOT_DRAIN_SECONDS,
        raw_subdir="pilot",
    )


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    environment = _environment(root)
    rate_limits = _rate_limits(environment)
    stamp = _stamp()
    findings = Findings()

    print("E3 pilot — benchmark mechanics validation")
    print(f"results:          {root}")
    print(f"publication_data: {publication_data()}")
    print("pilot runs are not E3 repetitions and are not publication evidence")

    schedule_summary = check_schedule_determinism(findings)

    print("\n2. Pilot runs")
    latency_runs: dict[str, BenchmarkRun] = {}
    throughput_runs: dict[str, BenchmarkRun] = {}

    for topology in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
        run = await _pilot_run(
            root=root,
            workload=E3_WORKLOAD_LATENCY,
            topology=topology,
            concurrency=1,
            stamp=stamp,
            rate_limits=rate_limits,
        )
        latency_runs[f"latency/{topology}"] = run
        await asyncio.sleep(E3_INTER_RUN_IDLE_SECONDS)

    for concurrency in E3_CONCURRENCY_LEVELS:
        for topology in (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED):
            run = await _pilot_run(
                root=root,
                workload=E3_WORKLOAD_THROUGHPUT,
                topology=topology,
                concurrency=concurrency,
                stamp=stamp,
                rate_limits=rate_limits,
            )
            throughput_runs[f"throughput-c{concurrency}/{topology}"] = run
            await asyncio.sleep(E3_INTER_RUN_IDLE_SECONDS)

    all_runs = {**latency_runs, **throughput_runs}
    for name, run in all_runs.items():
        findings.record(
            f"{name}: run is valid",
            run.validity.valid,
            "" if run.validity.valid else str(run.problems),
        )
        findings.record(
            f"{name}: fresh room, version 12, encryption disabled",
            run.room_version == "12" and not run.encryption_enabled,
            f"{run.room_id} v{run.room_version}",
        )

    latency_summary = check_latency_instrumentation(findings, latency_runs)
    throughput_summary = check_throughput(findings, throughput_runs)
    sync_summary = check_sync_configuration(findings, all_runs)
    rate_summary = check_rate_limits(findings, rate_limits, throughput_runs)
    check_symmetry(findings, all_runs)
    schema_summary = check_schemas(findings, all_runs)

    artifact = {
        "artifact": "e3_pilot_summary",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": protocol_git_commit(),
        "publication_data": publication_data(),
        "scope_note": (
            "Development pilot. Not an E3 repetition, not publication "
            "evidence, and not a performance result."
        ),
        "schedule": schedule_summary,
        "latency": latency_summary,
        "throughput": throughput_summary,
        "sync_configuration": sync_summary,
        "rate_limits": rate_summary,
        "schema_validation": schema_summary,
        "source_run_ids": sorted(run.run_id for run in all_runs.values()),
        "source_raw_digests": {
            run.run_id: {
                role: file_sha256(path)
                for role, path in (
                    ("runner_interaction_stream", run.runner_stream),
                    ("agent_telemetry_stream", run.agent_stream),
                )
                if path is not None and path.exists()
            }
            for run in all_runs.values()
        },
        "checks": findings.entries,
        "verdict": "PASS" if findings.ok else "FAIL",
    }

    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    path = processed / f"e3-pilot-{stamp}.json"
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (processed / "e3-pilot-latest.json").write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"\npilot artifact: {path}")
    print(f"\nE3 pilot: {artifact['verdict']}")
    if not findings.ok:
        for name in findings.failures:
            print(f"  ! {name}")
        print("\nfix the pilot findings before running `make e3`")
        return 1

    print(
        "\nrecommended development sync timeline limit: "
        f"{CANDIDATE_TIMELINE_LIMIT} (maximum workload occupancy "
        f"{sync_summary['max_workload_timeline_events_observed']} events, "
        f"{sync_summary['headroom_factor']}x headroom)"
    )
    print("next: make e3")
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
