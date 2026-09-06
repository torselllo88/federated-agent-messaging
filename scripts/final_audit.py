#!/usr/bin/env python3
"""Final evidence-integrity audit over the formal collection (Task 07 §60-§62).

Deliberately narrow. This is not a second architecture review: it asks whether
the evidence is internally consistent, whether any impossible state appears in
it, and whether any secret leaked into it. Everything it reports is derived
from the artifacts themselves, and it changes none of them.

§61 enumerates the impossible states. Each is checked here against every raw
record of the formal campaign, not sampled -- an invariant that holds on a
sample and fails on one record is still a violation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, "/app/src")
sys.path.insert(0, "src")

from fam.common.digests import file_sha256  # noqa: E402
from fam.common.lock import compare, load as load_lock  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    manifests_dir,
    resolve_results_dir,
)

#: A credential shape that must never appear in evidence (§62). Kept to shapes
#: that are unambiguous: a bare word like "key" or "token" occurs in ordinary
#: human prose and rejecting a transcript for containing it would be a false
#: positive, not a finding.
SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[0-9a-f]{16,}", re.I),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsyt_[A-Za-z0-9_\-]{16,}"),          # Matrix access token
    re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@"),  # database credential
]

#: Fields whose presence in evidence would itself be the leak.
FORBIDDEN_KEYS = {
    "api_key",
    "access_token",
    "password",
    "registration_shared_secret",
    "macaroon_secret_key",
    "form_secret",
    "private_key",
}


class Findings:
    def __init__(self) -> None:
        self.checks: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    def report(self) -> bool:
        for name, ok, detail in self.checks:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        return not self.failures


def _records(root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in sorted((root / "raw").rglob("*.runner.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                yield path, json.loads(line)


def _manifests(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(manifests_dir(root).glob("*.json"))
    ]


# ------------------------------------------------------ §61 impossible states


def impossible_states(root: Path, lock: dict[str, Any], findings: Findings) -> None:
    frozen = lock["frozen_parameters"]
    timeout_ns = int(float(frozen["interaction_timeout_seconds"]) * 1e9)
    body_bytes = int(frozen["message_body_bytes"])
    window_ns = int(float(frozen["e3_measurement_seconds"]) * 1e9)
    concurrency_bound = max(
        list(frozen["e3_concurrency_levels"]) + [int(frozen["e3_latency_max_in_flight"])]
    )

    over_timeout: list[str] = []
    reclassified: list[str] = []
    negative_rtt: list[str] = []
    clock_reversal: list[str] = []
    wrong_body: list[str] = []
    success_without_response: list[str] = []
    duplicate_inconsistent: list[str] = []
    ack_count_wrong: list[str] = []
    over_concurrency: list[str] = []
    windows: dict[str, tuple[int, int]] = {}
    sequences: dict[str, set[int]] = {}
    duplicate_sequences: list[str] = []
    interactions = 0

    for path, record in _records(root):
        if record.get("record_type") != "interaction":
            continue
        interactions += 1
        run = record.get("run_id", path.name)
        seq = record.get("sequence_id")
        outcome = record.get("outcome")
        started = record.get("initiated_monotonic_ns")
        finished = record.get("completed_monotonic_ns")
        where = f"{run}#{seq}"

        # 6. no duplicate sequence ids within a run
        seen = sequences.setdefault(run, set())
        if seq in seen:
            duplicate_sequences.append(where)
        seen.add(seq)

        if outcome == "success":
            # 7. a success must name the response event that ended it
            if not record.get("response_event_id"):
                success_without_response.append(where)
            if started is not None and finished is not None:
                rtt = finished - started
                # 3. no negative RTT / 4. no clock reversal
                if rtt < 0:
                    negative_rtt.append(where)
                    clock_reversal.append(where)
                # 1. no success beyond the frozen logical timeout
                elif rtt > timeout_ns:
                    over_timeout.append(f"{where} rtt={rtt / 1e6:.1f}ms")

        # 2. a timeout is terminal; a late ACK may be recorded but must not
        #    reclassify it, and must not fill in the completion timestamp
        if outcome == "timeout" and finished is not None:
            reclassified.append(where)

        # 8, 9. duplicate-ACK evidence must be internally consistent
        ack_count = record.get("ack_count")
        dup_count = record.get("duplicate_ack_count")
        dup_ids = record.get("duplicate_ack_event_ids")
        if ack_count is not None:
            if dup_count is None or dup_ids is None:
                duplicate_inconsistent.append(f"{where} incomplete duplicate evidence")
            else:
                if len(dup_ids) != dup_count:
                    duplicate_inconsistent.append(
                        f"{where} {dup_count} duplicates but {len(dup_ids)} ids"
                    )
                if len(set(dup_ids)) != len(dup_ids):
                    duplicate_inconsistent.append(f"{where} repeated duplicate id")
                expected = dup_count + (1 if record.get("response_event_id") else 0)
                if ack_count != expected:
                    ack_count_wrong.append(
                        f"{where} ack_count={ack_count} expected {expected}"
                    )
                if record.get("response_event_id") in dup_ids:
                    duplicate_inconsistent.append(
                        f"{where} the terminating ACK is listed as a duplicate"
                    )

        # 12. exact message body size, E3 only
        size = record.get("message_body_bytes")
        if size is not None and size != body_bytes:
            wrong_body.append(f"{where} {size}")

        # 5. concurrency never exceeds the frozen bound
        level = record.get("concurrency")
        if level is not None and level > concurrency_bound:
            over_concurrency.append(f"{where} C={level}")

        # 10. the E3 measurement window is exactly the frozen duration
        start_ns, end_ns = record.get("window_start_ns"), record.get("window_end_ns")
        if start_ns is not None and end_ns is not None:
            windows[run] = (start_ns, end_ns)

    findings.record(
        "no success beyond the frozen logical timeout",
        not over_timeout,
        f"{len(over_timeout)} of {interactions} interactions" if over_timeout
        else f"{interactions} interactions checked against "
             f"{frozen['interaction_timeout_seconds']}s",
    )
    findings.record(
        "no timeout carries a completion timestamp",
        not reclassified,
        f"{len(reclassified)} reclassified" if reclassified
        else "terminal outcomes are immutable",
    )
    findings.record("no negative round-trip time", not negative_rtt,
                    f"{len(negative_rtt)}" if negative_rtt else "")
    findings.record("no clock reversal within an interaction", not clock_reversal,
                    f"{len(clock_reversal)}" if clock_reversal else "")
    findings.record(
        "concurrency never exceeds the frozen bound",
        not over_concurrency,
        f"{len(over_concurrency)} above C={concurrency_bound}" if over_concurrency
        else f"bound C={concurrency_bound}",
    )
    findings.record("no duplicate sequence id within a run", not duplicate_sequences,
                    f"{len(duplicate_sequences)}" if duplicate_sequences else
                    f"{len(sequences)} runs")
    findings.record(
        "no success without a response event id",
        not success_without_response,
        f"{len(success_without_response)}" if success_without_response else "",
    )
    findings.record(
        "duplicate-ACK evidence is internally consistent",
        not duplicate_inconsistent,
        "; ".join(duplicate_inconsistent[:3]) if duplicate_inconsistent else "",
    )
    findings.record(
        "ack_count matches the distinct ACKs observed",
        not ack_count_wrong,
        "; ".join(ack_count_wrong[:3]) if ack_count_wrong else "",
    )
    findings.record("every measured body is exactly the frozen size", not wrong_body,
                    "; ".join(wrong_body[:3]) if wrong_body else f"{body_bytes} bytes")

    off_window = [
        f"{run} {(end - start) / 1e9:.6f}s"
        for run, (start, end) in windows.items()
        if end - start != window_ns
    ]
    findings.record(
        "every E3 measurement window is exactly the frozen duration",
        not off_window,
        "; ".join(off_window[:3]) if off_window
        else f"{len(windows)} windows at {frozen['e3_measurement_seconds']}s",
    )


# ---------------------------------------------- §61 schedule and environment


def schedule_and_environment(
    root: Path, lock: dict[str, Any], manifests: list[dict[str, Any]], findings: Findings
) -> None:
    locked: list[str] = []
    for name in sorted(lock["e3_schedule"]):
        locked += [
            (r["block_id"], r["within_block_order"], r["topology"], r["concurrency"])
            for r in lock["e3_schedule"][name]
        ]
    executed = sorted(
        (
            m["block_id"],
            m["within_block_order"],
            m["topology"],
            m["concurrency"],
        )
        for m in manifests
        if m["experiment"] == "E3"
    )
    findings.record(
        "the executed local/federated pair schedule matches the lock",
        sorted(locked) == executed,
        f"{len(executed)} runs against {len(locked)} scheduled",
    )

    versions = {m.get("room_version") for m in manifests}
    findings.record(
        "every room used the frozen room version",
        versions == {lock["frozen_parameters"]["room_version"]},
        f"observed {sorted(versions)}",
    )

    schemas = {m.get("raw_schema_version") for m in manifests}
    findings.record(
        "every formal manifest declares the locked raw schema",
        schemas == {lock["frozen_parameters"]["raw_schema_version"]},
        f"observed {sorted(schemas)}",
    )

    flags = {m.get("publication_data") for m in manifests}
    findings.record(
        "every formal manifest carries publication_data true",
        flags == {True},
        f"observed {sorted(str(f) for f in flags)}",
    )

    invalid = [
        m["run_id"]
        for m in manifests
        if not m.get("validity_classification", {}).get("valid")
    ]
    findings.record(
        "no run is classified invalid",
        not invalid,
        f"{len(invalid)} invalid" if invalid else f"{len(manifests)} runs valid",
    )


# ------------------------------------------------------------ §60 provenance


def provenance(root: Path, lock: dict[str, Any], manifests, findings: Findings) -> None:
    findings.record(
        "the lock still describes the running code",
        not compare(lock),
        "; ".join(compare(lock)[:2]) if compare(lock) else "",
    )

    missing = []
    mismatched = []
    for manifest in manifests:
        for artifact in (manifest.get("raw_artifacts") or []) + (
            manifest.get("evidence_artifacts") or []
        ):
            path = root / artifact["path"]
            if not path.exists():
                missing.append(artifact["path"])
            elif file_sha256(path) != artifact["sha256"]:
                mismatched.append(artifact["path"])
    findings.record("every artifact a manifest names exists", not missing,
                    f"{len(missing)} missing" if missing else "")
    findings.record("every named artifact matches its recorded digest", not mismatched,
                    f"{len(mismatched)} differ" if mismatched else "")

    completion = root / "environment" / "collection-completion.json"
    findings.record(
        "the collection is frozen",
        completion.exists()
        and json.loads(completion.read_text(encoding="utf-8")).get("collection_phase")
        == "complete",
        str(completion),
    )


# ---------------------------------------------------------------- §62 secrets


def secrets(root: Path, findings: Findings) -> None:
    hits: list[str] = []
    keys: list[str] = []

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in FORBIDDEN_KEYS:
                    keys.append(f"{where}:{key}")
                walk(value, where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".csv", ".txt"):
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(root).as_posix()} ~ {pattern.pattern}")
        if path.suffix == ".json":
            try:
                walk(json.loads(text), path.relative_to(root).as_posix())
            except ValueError:
                pass

    findings.record("no credential shape appears in the evidence", not hits,
                    "; ".join(hits[:3]) if hits else f"{scanned} files scanned")
    findings.record("no forbidden field name appears in the evidence", not keys,
                    "; ".join(keys[:3]) if keys else "")


def main() -> int:
    root = ensure_layout(resolve_results_dir())
    lock = load_lock()
    if lock is None:
        print("FAIL: no protocol lock is readable", file=sys.stderr)
        return 1
    manifests = _manifests(root)

    print(f"final evidence-integrity audit over {root}")
    print(f"  campaign {lock['campaign']['campaign_id']}")
    print(f"  lock     {lock['implementation']['git_tag']} "
          f"@ {lock['implementation']['git_commit'][:12]}")
    print(f"  manifests {len(manifests)}")

    findings = Findings()
    print("\n1. impossible states (§61)")
    impossible_states(root, lock, findings)
    n = len(findings.checks)
    findings_so_far = findings.checks[:]
    for name, ok, detail in findings_so_far:
        pass
    print("\n2. schedule and frozen environment (§61)")
    schedule_and_environment(root, lock, manifests, findings)
    print("\n3. provenance (§60)")
    provenance(root, lock, manifests, findings)
    print("\n4. secrets (§62)")
    secrets(root, findings)

    print()
    ok = findings.report()
    passed = len(findings.checks) - len(findings.failures)
    print(f"\n  {passed}/{len(findings.checks)} checks passed")
    print("\nAUDIT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
