#!/usr/bin/env python3
"""Analysis and validation over result artifacts.

Pipeline order is fixed by experimental-protocol.md §40: digest verification
first, then schema validation, then derived output. Analysing a dataset whose
per-file SHA-256 does not match its manifest is a provenance failure, not a
data question.

For this slice the derived output is an E0 pass/fail summary. No E3
statistics. Every processed artifact carries the frozen provenance triple
plus source digests and run ids.
"""

from __future__ import annotations

import json
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

sys.path.insert(0, "/app/scripts")
from verify_digests import verify as verify_digests  # noqa: E402

#: The analysis implementation may be written or corrected after collection;
#: the specification it implements may not change without a disclosed
#: methodological revision (experimental-protocol.md §3 Phase 4, §40).
ANALYSIS_CODE_COMMIT = "task-01-working-tree"

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
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_run_ids": [run["run_id"] for run in summary["runs"]],
        "source_digests": {
            run["run_id"]: run["source_digests"] for run in summary["runs"]
        },
        "e0_summary": summary,
        "note": (
            "Development validation. publication_data is false; this is not "
            "publication evidence and no formal evidence counter is updated."
        ),
    }
    path = processed_dir / f"e0-summary-{stamp}.json"
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n   processed artifact: {path}")
    print(f"   sha256 {file_sha256(path)}")

    ok = summary["runs_total"] > 0 and summary["runs_passed"] == summary["runs_total"]
    print(f"\nANALYSE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _protocol_commit(root: Path) -> str:
    for manifest_path in sorted(manifests_dir(root).glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        commit = manifest.get("protocol_git_commit")
        if commit:
            return commit
    return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
