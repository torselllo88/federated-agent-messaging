#!/usr/bin/env python3
"""Analysis and validation over result artifacts.

Pipeline order is fixed by experimental-protocol.md §40: digest verification
first, then schema validation, then derived output. Analysing a dataset whose
per-file SHA-256 does not match its manifest is a provenance failure, not a
data question.

Derived output covers E0-E2 functional summaries, the Task 04 transport
readiness summary, and the development E3 metrics: latency percentiles,
observed throughput at tested concurrency, stationarity, failure rates and
paired-block bootstrap intervals. Every processed artifact carries the frozen
provenance triple plus source digests and run ids.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/src")

from fam.common.digests import file_sha256  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    EXECUTION_ANALYSIS_SPEC_VERSION,
    RAW_SCHEMA_VERSION,
)
from fam.common.results import manifests_dir, resolve_results_dir  # noqa: E402
from fam.analysis import e3 as e3_analysis  # noqa: E402

sys.path.insert(0, "/app/scripts")
from verify_digests import verify as verify_digests  # noqa: E402

#: The analysis implementation may be written or corrected after collection;
#: the specification it implements may not change without a disclosed
#: methodological revision (experimental-protocol.md §3 Phase 4, §40).
ANALYSIS_CODE_COMMIT = "task-05-working-tree"

REQUIRED_RUNNER_FIELDS = {
    "schema_version",
    "experiment",
    "run_id",
    "sequence_id",
    "room_id",
    "sender",
    "request_txn_id",
    "initiated_monotonic_ns",
    "outcome",
}
REQUIRED_AGENT_FIELDS = {"schema_version", "experiment", "run_id", "agent_mxid", "action"}

#: Never persisted as authoritative raw evidence; derived during analysis.
FORBIDDEN_RAW_FIELDS = {"counted_in_window"}


def load_jsonl(path: Path) -> list[dict]:
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{number} is not valid JSON: {exc}") from exc
    return records


SCHEMA_DIR = Path("/app/results/schemas")


def _load_schema(name: str) -> dict | None:
    path = SCHEMA_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(schema: dict | None):
    if schema is None:
        return None
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - jsonschema is a pinned dependency
        return None
    return Draft202012Validator(schema)


def validate_streams(root: Path) -> tuple[bool, list[str]]:
    """Validate raw streams against the tracked JSON Schemas.

    The field-level fallback runs when a schema is unavailable, so validation
    degrades to something useful rather than to nothing.
    """
    problems: list[str] = []
    validators = {
        "runner": _validator(_load_schema("raw-runner-record.schema.json")),
        "agent": _validator(_load_schema("raw-agent-record.schema.json")),
    }
    manifest_validator = _validator(_load_schema("run-manifest.schema.json"))

    for path in sorted((root / "raw").rglob("*.jsonl")):
        kind = "runner" if path.name.endswith(".runner.jsonl") else "agent"
        try:
            records = load_jsonl(path)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not records:
            problems.append(f"{path.name}: empty stream")
            continue

        validator = validators[kind]
        required = REQUIRED_RUNNER_FIELDS if kind == "runner" else REQUIRED_AGENT_FIELDS

        for index, record in enumerate(records, 1):
            leaked = FORBIDDEN_RAW_FIELDS & record.keys()
            if leaked:
                problems.append(
                    f"{path.name}:{index} persists derived analytical field(s) "
                    f"{sorted(leaked)} as raw evidence"
                )
                break
            if record.get("schema_version") != RAW_SCHEMA_VERSION:
                problems.append(
                    f"{path.name}:{index} schema_version "
                    f"{record.get('schema_version')!r} != {RAW_SCHEMA_VERSION!r}"
                )
                break
            if validator is not None:
                errors = sorted(validator.iter_errors(record), key=lambda e: e.path)
                if errors:
                    problems.append(
                        f"{path.name}:{index} schema violation: {errors[0].message}"
                    )
                    break
            else:
                missing = required - record.keys()
                if missing:
                    problems.append(
                        f"{path.name}:{index} missing fields {sorted(missing)}"
                    )
                    break

    if manifest_validator is not None:
        for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            errors = sorted(manifest_validator.iter_errors(manifest), key=lambda e: e.path)
            if errors:
                problems.append(
                    f"{manifest_path.name} schema violation: {errors[0].message}"
                )

    return not problems, problems


def summarize_e0(root: Path) -> dict:
    runs = []
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("experiment") != "E0":
            continue
        runs.append(
            {
                "run_id": manifest["run_id"],
                "room_id": manifest.get("room_id"),
                "publication_data": manifest.get("publication_data"),
                "requests_sent": manifest.get("requests_sent"),
                "requests_acked": manifest.get("requests_acked"),
                "duplicate_acks": manifest.get("duplicate_acks"),
                "agent_identity_before_restart": manifest.get(
                    "agent_identity_before_restart"
                ),
                "agent_identity_after_restart": manifest.get(
                    "agent_identity_after_restart"
                ),
                "identity_stable": manifest.get("agent_identity_before_restart")
                == manifest.get("agent_identity_after_restart"),
                "resumed_from_transport_checkpoint": manifest.get(
                    "resumed_from_transport_checkpoint"
                ),
                "completion_status": manifest.get("completion_status"),
                "validity": manifest.get("validity_classification"),
                "acceptance_failures": manifest.get("acceptance_failures", []),
                "c2_supporting_evidence_complete": (
                    manifest.get("c2_evidence", {}) or {}
                ).get("c2_supporting_evidence_complete"),
                "source_digests": {
                    artifact["role"]: artifact["sha256"]
                    for artifact in manifest.get("raw_artifacts", [])
                },
            }
        )

    passed = sum(1 for run in runs if run["completion_status"] == "pass")
    return {
        "runs": runs,
        "runs_total": len(runs),
        "runs_passed": passed,
        "verdict": (
            f"{passed}/{len(runs)} PASS" if runs else "no E0 runs found"
        ),
        "publication_data": any(run["publication_data"] for run in runs),
    }


def summarize_e1(root: Path) -> dict:
    runs = []
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("experiment") != "E1":
            continue
        classes = manifest.get("request_classes", {})
        cross = classes.get("cross_domain", {})
        same = classes.get("same_domain", {})
        runs.append(
            {
                "run_id": manifest["run_id"],
                "room_id": manifest.get("room_id"),
                "publication_data": manifest.get("publication_data"),
                "cross_domain": {
                    "sender": cross.get("sender"),
                    "requests": cross.get("sent"),
                    "acks": cross.get("acked"),
                    "duplicates": cross.get("duplicates"),
                },
                "same_domain": {
                    "sender": same.get("sender"),
                    "requests": same.get("sent"),
                    "acks": same.get("acked"),
                    "duplicates": same.get("duplicates"),
                },
                "a_requests_visible_on_b": manifest.get("a_requests_visible_on_b"),
                "b_requests_visible_on_a": manifest.get("b_requests_visible_on_a"),
                "expected_event_count": manifest.get("expected_event_count"),
                "domain_a_event_count": manifest.get("domain_a_event_count"),
                "domain_b_event_count": manifest.get("domain_b_event_count"),
                "missing_on_a": manifest.get("missing_on_a", []),
                "missing_on_b": manifest.get("missing_on_b", []),
                "unexpected_on_a": manifest.get("unexpected_on_a", []),
                "unexpected_on_b": manifest.get("unexpected_on_b", []),
                "event_set_equal": manifest.get("event_set_equal"),
                "membership_compatible": manifest.get("membership_compatible"),
                "membership_after_join": manifest.get("membership_after_join"),
                "validity": manifest.get("validity_classification"),
                "completion_status": manifest.get("completion_status"),
                "acceptance_failures": manifest.get("acceptance_failures", []),
                "comparison_artifact": manifest.get("federation_comparison_artifact"),
                "source_digests": {
                    artifact["role"]: artifact["sha256"]
                    for artifact in manifest.get("raw_artifacts", [])
                },
            }
        )

    valid = [r for r in runs if (r["validity"] or {}).get("valid")]
    passed = sum(1 for r in runs if r["completion_status"] == "pass")
    return {
        "runs": runs,
        "runs_total": len(runs),
        "runs_valid": len(valid),
        "runs_passed": passed,
        "verdict": f"{passed}/{len(runs)} PASS" if runs else "no E1 runs found",
    }


def print_e1(summary: dict) -> None:
    for run in summary["runs"]:
        cross, same = run["cross_domain"], run["same_domain"]
        print(f"   {run['run_id']}")
        print(
            f"     A->agent cross-domain  {cross['acks']}/{cross['requests']} ACK  "
            f"dup={cross['duplicates']}"
        )
        print(
            f"     B->agent same-domain   {same['acks']}/{same['requests']} ACK  "
            f"dup={same['duplicates']}"
        )
        print(
            f"     visibility             A->B={run['a_requests_visible_on_b']}  "
            f"B->A={run['b_requests_visible_on_a']}"
        )
        print(
            f"     event sets             expected={run['expected_event_count']} "
            f"A={run['domain_a_event_count']} B={run['domain_b_event_count']} "
            f"equal={run['event_set_equal']}"
        )
        for label in ("missing_on_a", "missing_on_b", "unexpected_on_a", "unexpected_on_b"):
            items = run[label]
            if items:
                print(f"     ! {label}: {len(items)} -> {items[:3]}")
        print(f"     membership compatible  {run['membership_compatible']}")
        validity = run["validity"] or {}
        suffix = "" if validity.get("valid") else f" ({validity.get('invalid_class')})"
        print(f"     validity               valid={validity.get('valid')}{suffix}")
        print(f"     {str(run['completion_status']).upper()}")
        for reason in run["acceptance_failures"]:
            print(f"       ! {reason}")
    print(
        f"   E1 valid runs {summary['runs_valid']}, "
        f"passed {summary['runs_passed']}, verdict: {summary['verdict']}"
    )


def summarize_e2(root: Path) -> dict:
    runs = []
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("experiment") != "E2":
            continue
        runs.append(
            {
                "run_id": manifest["run_id"],
                "room_id": manifest.get("room_id"),
                "publication_data": manifest.get("publication_data"),
                "timeline_limit": manifest.get("timeline_limit"),
                "offline_sends_accepted": manifest.get("sent_count"),
                "sync_limited": manifest.get("sync_limited"),
                "recovered_from_sync": manifest.get("recovered_from_sync"),
                "recovered_from_history": manifest.get("recovered_from_history"),
                "duplicate_observations": manifest.get("duplicate_observations"),
                "pagination_invoked": manifest.get("pagination_invoked"),
                "history_pages_fetched": manifest.get("history_pages_fetched"),
                "sent_count": manifest.get("sent_count"),
                "recovered_count": manifest.get("recovered_count"),
                "missing_from_recovery": manifest.get("missing_from_recovery", []),
                "unexpected_in_recovery": manifest.get("unexpected_in_recovery", []),
                "logically_processed": manifest.get("logically_processed"),
                "ack_count": manifest.get("ack_count"),
                "duplicate_acks": manifest.get("duplicate_acks"),
                "identity_resumed": manifest.get("agent_identity_before_restart")
                == manifest.get("agent_identity_after_restart"),
                "checkpoint_resumed": manifest.get("checkpoint_resumed"),
                "validity": manifest.get("validity_classification"),
                "completion_status": manifest.get("completion_status"),
                "acceptance_failures": manifest.get("acceptance_failures", []),
                "comparison_artifact": manifest.get("recovery_comparison_artifact"),
                "source_digests": {
                    artifact["role"]: artifact["sha256"]
                    for artifact in manifest.get("raw_artifacts", [])
                },
            }
        )

    valid = [r for r in runs if (r["validity"] or {}).get("valid")]
    passed = sum(1 for r in runs if r["completion_status"] == "pass")
    return {
        "runs": runs,
        "runs_total": len(runs),
        "runs_valid": len(valid),
        "runs_passed": passed,
        "verdict": f"{passed}/{len(runs)} PASS" if runs else "no E2 runs found",
    }


def print_e2(summary: dict) -> None:
    for run in summary["runs"]:
        print(f"   {run['run_id']}  (timeline_limit={run['timeline_limit']})")
        print(
            f"     offline sends accepted {run['offline_sends_accepted']}  "
            f"sync_limited={run['sync_limited']}"
        )
        print(
            f"     recovered              sync={run['recovered_from_sync']} "
            f"history={run['recovered_from_history']} "
            f"pages={run['history_pages_fetched']} "
            f"dup_obs={run['duplicate_observations']}"
        )
        print(
            f"     set equality           |S_sent|={run['sent_count']} "
            f"|S_recovered|={run['recovered_count']} "
            f"missing={len(run['missing_from_recovery'])} "
            f"unexpected={len(run['unexpected_in_recovery'])}"
        )
        for label in ("missing_from_recovery", "unexpected_in_recovery"):
            if run[label]:
                print(f"     ! {label}: {run[label][:3]}")
        print(
            f"     processing / ACKs      {run['logically_processed']} / "
            f"{run['ack_count']}  dup_ack={run['duplicate_acks']}"
        )
        print(
            f"     resume                 identity={run['identity_resumed']} "
            f"checkpoint={run['checkpoint_resumed']}"
        )
        validity = run["validity"] or {}
        suffix = "" if validity.get("valid") else f" ({validity.get('invalid_class')})"
        print(f"     validity               valid={validity.get('valid')}{suffix}")
        print(f"     {str(run['completion_status']).upper()}")
        for reason in run["acceptance_failures"]:
            print(f"       ! {reason}")
    print(
        f"   E2 valid runs {summary['runs_valid']}, "
        f"passed {summary['runs_passed']}, verdict: {summary['verdict']}"
    )


def summarize_readiness(root: Path) -> dict:
    """Transport readiness only. No performance metric is computed here."""
    runs = []
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("experiment") != "E3READINESS":
            continue
        runs.append(
            {
                "run_id": manifest["run_id"],
                "room_id": manifest.get("room_id"),
                "publication_data": manifest.get("publication_data"),
                "request_count": manifest.get("request_count"),
                "max_in_flight": manifest.get("max_in_flight"),
                "development_timeline_limit": manifest.get("development_timeline_limit"),
                "limited_sync_count": manifest.get("limited_sync_count"),
                "recovery_episode_count": manifest.get("recovery_episode_count"),
                "history_pages_fetched": manifest.get("history_pages_fetched"),
                "events_recovered_via_history": manifest.get(
                    "events_recovered_via_history"
                ),
                "sent_count": manifest.get("sent_count"),
                "processed_count": manifest.get("processed_count"),
                "ack_count": manifest.get("ack_count"),
                "missing_processed": manifest.get("missing_processed", []),
                "unexpected_processed": manifest.get("unexpected_processed", []),
                "duplicate_observations": manifest.get("duplicate_observations"),
                "duplicate_acks": manifest.get("duplicate_acks"),
                "recovery_failures": manifest.get("recovery_failures"),
                "checkpoint_commits": manifest.get("checkpoint_commits"),
                "validity": manifest.get("validity_classification"),
                "completion_status": manifest.get("completion_status"),
                "acceptance_failures": manifest.get("acceptance_failures", []),
                "source_digests": {
                    artifact["role"]: artifact["sha256"]
                    for artifact in manifest.get("raw_artifacts", [])
                },
            }
        )

    valid = [r for r in runs if (r["validity"] or {}).get("valid")]
    passed = sum(1 for r in runs if r["completion_status"] == "pass")
    return {
        "runs": runs,
        "runs_total": len(runs),
        "runs_valid": len(valid),
        "runs_passed": passed,
        "verdict": f"{passed}/{len(runs)} PASS" if runs else "no readiness runs found",
        "note": (
            "Transport readiness. Completion counts are correctness evidence, "
            "never throughput. No E3 performance metric is derived here."
        ),
    }


def print_readiness(summary: dict) -> None:
    for run in summary["runs"]:
        print(
            f"   {run['run_id']}  (requests={run['request_count']}, "
            f"max_in_flight={run['max_in_flight']}, "
            f"dev_timeline_limit={run['development_timeline_limit']})"
        )
        print(
            f"     limited syncs          {run['limited_sync_count']}  "
            f"episodes={run['recovery_episode_count']}  "
            f"pages={run['history_pages_fetched']}  "
            f"via history={run['events_recovered_via_history']}"
        )
        print(
            f"     processed / ACKs       {run['processed_count']} / {run['ack_count']}"
        )
        print(
            f"     missing / unexpected   {len(run['missing_processed'])} / "
            f"{len(run['unexpected_processed'])}"
        )
        print(
            f"     duplicates             observation="
            f"{run['duplicate_observations']}  processing="
            f"{max(0, (run['ack_count'] or 0) - (run['processed_count'] or 0))}  "
            f"ack={run['duplicate_acks']}"
        )
        print(f"     recovery failures      {run['recovery_failures']}")
        print(f"     {str(run['completion_status']).upper()}")
        for reason in run["acceptance_failures"]:
            print(f"       ! {reason}")
    print(
        f"   readiness valid runs {summary['runs_valid']}, "
        f"passed {summary['runs_passed']}, verdict: {summary['verdict']}"
    )


def summarize_e3(root: Path, campaign_id: str | None = None) -> dict:
    """Development E3 metrics, derived from immutable raw benchmark data."""
    campaign_id = campaign_id or os.environ.get("FAM_E3_CAMPAIGN", "").strip() or None
    inventory = e3_analysis.campaign_inventory(root)
    runs = e3_analysis.load_campaign(root, campaign_id)
    if not runs:
        return {"runs_total": 0, "campaign_inventory": inventory}
    replicates = int(os.environ.get("FAM_E3_BOOTSTRAP_REPLICATES", "0") or 0)
    seed = int(os.environ.get("FAM_E3_BOOTSTRAP_SEED", "0") or 0)
    summary = e3_analysis.analyse(
        runs,
        replicates=replicates or e3_analysis.DEFAULT_REPLICATES,
        seed=seed or e3_analysis.DEFAULT_BOOTSTRAP_SEED,
    )
    summary["campaign_inventory"] = inventory
    return summary


def _fmt(value, suffix=""):
    return "n/a" if value is None else f"{value}{suffix}"


def print_e3(summary: dict) -> None:
    print(f"   campaign {summary['campaign_id']}")
    others = {
        key: value
        for key, value in (summary.get("campaign_inventory") or {}).items()
        if key not in (summary["campaign_id"], "e3-pilot")
    }
    for key, value in sorted(others.items()):
        print(
            f"     (superseded campaign present: {key}, {value['runs']} runs, "
            f"last completion {value['last_completion']} — not analysed)"
        )
    print(
        f"   runs {summary['runs_valid']}/{summary['runs_total']} valid"
        + (f", invalid {len(summary['runs_invalid'])}" if summary["runs_invalid"] else "")
    )
    for entry in summary["runs_invalid"]:
        print(f"     ! {entry['run_id']} — {entry['class']}")
    for problem in summary.get("integrity_problems", []):
        print(f"     ! integrity: {problem}")

    latency = summary["latency"]
    print(f"\n   Workload A — latency ({latency['paired_blocks']} paired blocks)")
    for name in ("local", "federated"):
        item = latency["by_topology"].get(name) or {}
        print(
            f"     {name:<10} runs={item.get('runs')} "
            f"n={item.get('successful_interactions')}/{item.get('initiated_interactions')} "
            f"p50={_fmt(item.get('p50_ms'))}ms "
            f"p95={_fmt(item.get('p95_ms'))}ms "
            f"p99={_fmt(item.get('p99_ms'))}ms "
            f"fail={_fmt(item.get('failure_rate'))}"
        )
    boot = latency.get("bootstrap") or {}
    for label, stats in (boot.get("percentiles") or {}).items():
        diff = stats["difference_ci_ms"]
        ratio = stats["ratio_ci"]
        print(
            f"     {label}: diff={_fmt(stats['difference_ms'])}ms "
            f"[{_fmt(diff['low'])}, {_fmt(diff['high'])}]  "
            f"ratio={_fmt(stats['ratio'])} "
            f"[{_fmt(ratio['low'])}, {_fmt(ratio['high'])}]"
        )
    if latency.get("incomplete_blocks"):
        print(f"     incomplete blocks: {latency['incomplete_blocks']}")

    print("\n   Workload B — observed throughput at tested concurrency")
    for level, topologies in sorted(
        (summary["throughput"]["by_concurrency"] or {}).items(), key=lambda kv: int(kv[0])
    ):
        print(f"     C={level}  ({summary['throughput']['paired_blocks'].get(level)} paired blocks)")
        for name in ("local", "federated"):
            item = topologies.get(name) or {}
            print(
                f"       {name:<10} runs={item.get('runs')} "
                f"median={_fmt(item.get('median_observed_throughput_per_second'))}/s "
                f"range=[{_fmt(item.get('min_per_second'))}, {_fmt(item.get('max_per_second'))}] "
                f"fail={_fmt(item.get('mean_failure_rate'))}"
            )
        boot = (summary["throughput"]["bootstrap"] or {}).get(level) or {}
        if boot:
            diff, ratio = boot["difference_ci"], boot["ratio_ci"]
            print(
                f"       diff={_fmt(boot['difference_per_second'])}/s "
                f"[{_fmt(diff['low'])}, {_fmt(diff['high'])}]  "
                f"ratio={_fmt(boot['ratio'])} "
                f"[{_fmt(ratio['low'])}, {_fmt(ratio['high'])}]"
            )
        for name in ("local", "federated"):
            halves = [
                s.get("second_over_first")
                for s in (topologies.get(name) or {}).get("stationarity", [])
                if s.get("second_over_first") is not None
            ]
            if halves:
                print(
                    f"       {name:<10} stationarity second/first "
                    f"min={min(halves):.3f} max={max(halves):.3f}"
                )

    diagnostics = summary["diagnostics"]
    print(
        f"\n   transport diagnostics: "
        f"{diagnostics['runs_with_live_recovery_count']} runs with workload "
        f"gap recovery ({diagnostics['interactions_overlapping_recovery_total']} "
        f"interactions affected), "
        f"{diagnostics['runs_with_setup_only_recovery_count']} with a setup-only "
        f"episode, "
        f"{diagnostics['runs_with_m_limit_exceeded_count']} with M_LIMIT_EXCEEDED"
    )
    for entry in diagnostics["runs_with_live_recovery"]:
        print(
            f"     ~ {entry['run_id']}: sender={entry['sender_recovery_episodes']} "
            f"agent={entry['agent_recovery_episodes']} episodes, "
            f"{entry['interactions_overlapping_recovery']} interactions overlap"
        )

    drift = summary.get("environment_drift") or {}
    if drift.get("joined_rooms_first") is not None:
        print(
            f"   campaign drift: p50 vs execution order "
            f"{drift.get('p50_vs_campaign_position_pearson_by_topology')} "
            f"(sender joined rooms {drift['joined_rooms_first']} -> "
            f"{drift['joined_rooms_last']}; order and rooms are confounded)"
        )
        for name, item in (drift.get("p50_by_campaign_half") or {}).items():
            print(
                f"     {name:<10} mean run p50 "
                f"{item['first_half_mean_p50_ms']}ms -> "
                f"{item['second_half_mean_p50_ms']}ms across the campaign"
            )

    for name, item in (summary.get("derived_c1_consistency") or {}).items():
        print(
            f"   derived C=1 check ({name}): predicted "
            f"{_fmt(item['predicted_c1_throughput_per_second'])}/s from mean RTT "
            f"{_fmt(item['mean_rtt_ms'])}ms, consistent={item['consistent']}"
        )
    print(
        "\n   observed throughput at tested concurrency — never maximum, "
        "capacity, achievable or saturation throughput"
    )


def main() -> int:
    root = resolve_results_dir(create=False)
    print(f"analysis over {root}\n")

    print("1. digest verification")
    checked, failed, problems = verify_digests(root)
    print(f"   artifacts checked {checked}, failures {failed}")
    for problem in problems:
        print(f"   ! {problem}")
    if failed or checked == 0:
        print("\nANALYSE: FAIL (provenance)")
        return 1

    print("\n2. schema validation")
    schema_ok, schema_problems = validate_streams(root)
    for problem in schema_problems:
        print(f"   ! {problem}")
    print(f"   {'ok' if schema_ok else 'problems found'}")
    if not schema_ok:
        print("\nANALYSE: FAIL (schema)")
        return 1

    print("\n3. E0 summary")
    summary = summarize_e0(root)
    for run in summary["runs"]:
        print(
            f"   {run['run_id']}  {run['requests_acked']}/{run['requests_sent']} ACK  "
            f"dup={run['duplicate_acks']}  identity_stable={run['identity_stable']}  "
            f"{run['completion_status'].upper()}"
        )
    print(f"   verdict: {summary['verdict']}")

    print("\n4. E1 summary")
    e1 = summarize_e1(root)
    if e1["runs_total"]:
        print_e1(e1)
    else:
        print("   no E1 runs found")

    print("\n5. E2 summary")
    e2 = summarize_e2(root)
    if e2["runs_total"]:
        print_e2(e2)
    else:
        print("   no E2 runs found")

    print("\n6. E3 transport readiness (not a performance measurement)")
    readiness = summarize_readiness(root)
    if readiness["runs_total"]:
        print_readiness(readiness)
    else:
        print("   no readiness runs found")

    print("\n7. E3 development metrics")
    e3 = summarize_e3(root)
    if e3["runs_total"]:
        print_e3(e3)
    else:
        print("   no E3 benchmark runs found")

    processed_dir = root / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = {
        # Provenance model, experimental-protocol.md §40. The analysis
        # specification and its implementation are separate identifiers, and
        # neither is the protocol commit.
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": _protocol_commit(root),
        "protocol_git_commits_by_experiment": _protocol_commits(root),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_run_ids": [
            run["run_id"]
            for run in (summary["runs"] + e1["runs"] + e2["runs"] + readiness["runs"])
        ]
        + list(e3.get("source_run_ids", [])),
        "source_digests": {
            **{
                run["run_id"]: run["source_digests"]
                for run in (
                    summary["runs"] + e1["runs"] + e2["runs"] + readiness["runs"]
                )
            },
            **e3.get("source_raw_digests", {}),
        },
        "e0_summary": summary,
        "e1_summary": e1,
        "e2_summary": e2,
        "e3_readiness_summary": readiness,
        "e3_summary": e3,
        "note": (
            "Development validation. publication_data is false; this is not "
            "publication evidence and no formal evidence counter is updated."
        ),
    }
    if e3["runs_total"]:
        # Development figures and tables (§48). Not publication outputs.
        written = e3_analysis.write_tables(e3, processed_dir / "e3-tables")
        for table in written:
            print(f"   development table: {table}")

    path = processed_dir / f"experiment-summary-{stamp}.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n   processed artifact: {path}")
    print(f"   sha256 {file_sha256(path)}")

    ok = summary["runs_total"] > 0 and summary["runs_passed"] == summary["runs_total"]
    if e1["runs_total"]:
        ok = ok and e1["runs_passed"] == e1["runs_total"]
    if e2["runs_total"]:
        ok = ok and e2["runs_passed"] == e2["runs_total"]
    if readiness["runs_total"]:
        ok = ok and readiness["runs_passed"] == readiness["runs_total"]
    if e3["runs_total"]:
        # E3 has no pass/fail acceptance criterion — it is a measurement, not
        # a functional check. What can fail is provenance and completeness:
        # an invalid run, or a paired block missing one of its topologies.
        ok = ok and not e3["runs_invalid"]
        ok = ok and not e3["latency"]["incomplete_blocks"]
        # A schema-valid, digest-verified record can still state something
        # impossible. Both outcome-classification defects in Task 05 were of
        # exactly that kind, so this is a gate, not a note.
        ok = ok and not e3.get("integrity_problems")
    print(f"\nANALYSE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _protocol_commits(root: Path) -> dict[str, list[str]]:
    """Every protocol commit present, grouped by experiment.

    A results directory accumulates runs across tasks, so it can legitimately
    hold evidence collected at several protocol commits. Reporting one of them
    as *the* commit — whichever manifest happened to sort first — would attach
    the wrong provenance to every other experiment in the summary. Each run
    manifest keeps its own commit; this reports the set honestly.
    """
    found: dict[str, set[str]] = {}
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        commit = manifest.get("protocol_git_commit")
        if commit:
            found.setdefault(manifest.get("experiment", "unknown"), set()).add(commit)
    return {name: sorted(commits) for name, commits in sorted(found.items())}


def _protocol_commit(root: Path) -> str:
    """The single protocol commit, or a marker when the set is mixed."""
    commits = {c for values in _protocol_commits(root).values() for c in values}
    if len(commits) == 1:
        return commits.pop()
    if not commits:
        return "unknown"
    return "mixed — see protocol_git_commits_by_experiment"


if __name__ == "__main__":
    raise SystemExit(main())
