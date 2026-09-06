"""Independent reconstruction of analysis inputs from raw observations.

The analysis reads the parameters that define the throughput estimator —
window bounds, concurrency, block identity, topology — from the run manifest.
The manifest is written by experiment code. If that code ever wrote a wrong
window, the analysis would compute a wrong numerator from correct raw data and
nothing downstream would notice, because every consumer reads the same wrong
value.

This module closes that loop. Every field the analysis takes from a manifest
and that is *also* present in the immutable raw stream is compared against it
before analysis proceeds. A mismatch is a hard failure that names the run and
the field; neither source is treated as authoritative, because deciding which
one to believe is precisely the judgement that must not be made silently.

Two fields cannot be checked this way and are reported as such rather than
quietly counted as verified: ``validity_classification`` and
``publication_data`` exist only in the manifest. Nothing in the raw stream
carries them, by design — raw observations never carry a classification
(experimental-protocol.md §22).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from fam.common.frozen import E3_MEASUREMENT_SECONDS, E3_WORKLOAD_THROUGHPUT

#: manifest field -> raw record field. Only pairs that genuinely describe the
#: same fact; a near-synonym would make this check theatre.
COMPARABLE: dict[str, str] = {
    "run_id": "run_id",
    "experiment": "experiment",
    "topology": "topology",
    "workload_type": "workload",
    "paired_block_id": "block_id",
    "concurrency": "concurrency",
    "room_id": "room_id",
    "message_body_bytes": "message_body_bytes",
    "window_start_ns": "window_start_ns",
    "window_end_ns": "window_end_ns",
}

#: Present only in the manifest. Listed so a report can state what was *not*
#: cross-checked instead of implying full coverage.
MANIFEST_ONLY = ("validity_classification", "publication_data")

#: Latency runs have no measurement window; those two fields are absent from
#: both sides and comparing them would manufacture a false mismatch.
WINDOW_FIELDS = ("window_start_ns", "window_end_ns")


@dataclass
class IntegrityReport:
    runs_checked: int = 0
    fields_compared: int = 0
    records_compared: int = 0
    mismatches: list[str] = field(default_factory=list)
    unverifiable_fields: tuple[str, ...] = MANIFEST_ONLY

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs_checked": self.runs_checked,
            "manifest_fields_compared": self.fields_compared,
            "raw_records_compared": self.records_compared,
            "mismatches": self.mismatches,
            "fields_not_reconstructable_from_raw": list(self.unverifiable_fields),
            "note": (
                "Every manifest field the analysis consumes and that the raw "
                "stream also carries is compared against it. Neither source is "
                "authoritative on disagreement: a mismatch fails the analysis "
                "and names the run and field."
            ),
        }


def _load_raw(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
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


def check_run(
    manifest: dict[str, Any], records: Iterable[dict[str, Any]]
) -> tuple[list[str], int, int]:
    """Compare one run's manifest against its raw interaction records."""
    records = list(records)
    run_id = manifest.get("run_id", "?")
    problems: list[str] = []
    compared = 0

    is_throughput = manifest.get("workload_type") == E3_WORKLOAD_THROUGHPUT

    for manifest_key, raw_key in COMPARABLE.items():
        if manifest_key in WINDOW_FIELDS and not is_throughput:
            continue
        expected = manifest.get(manifest_key)
        observed = {record.get(raw_key) for record in records}
        compared += 1
        if not observed:
            continue
        if observed != {expected}:
            problems.append(
                f"{run_id}: manifest {manifest_key}={expected!r} but raw "
                f"{raw_key} holds {sorted(observed, key=repr)!r}"
            )

    # The window is the estimator. Its duration is frozen, so a window of any
    # other length is wrong even when manifest and raw agree with each other.
    if is_throughput:
        start, end = manifest.get("window_start_ns"), manifest.get("window_end_ns")
        if start is None or end is None:
            problems.append(f"{run_id}: throughput run has no measurement window")
        else:
            seconds = (end - start) / 1e9
            if abs(seconds - E3_MEASUREMENT_SECONDS) > 1e-6:
                problems.append(
                    f"{run_id}: measurement window is {seconds:.9f}s, the frozen "
                    f"duration is {E3_MEASUREMENT_SECONDS}s"
                )
            if end <= start:
                problems.append(f"{run_id}: window_end_ns is not after window_start_ns")

    return problems, compared, len(records)


def verify_campaign(root: Path, manifests: Iterable[dict[str, Any]]) -> IntegrityReport:
    """Cross-check every manifest in a campaign against its raw stream."""
    report = IntegrityReport()
    for manifest in manifests:
        artifact = next(
            (
                a
                for a in manifest.get("raw_artifacts", [])
                if a.get("role") == "runner_interaction_stream"
            ),
            None,
        )
        if artifact is None:
            report.mismatches.append(
                f"{manifest.get('run_id','?')}: no runner interaction stream to "
                "reconstruct from"
            )
            continue
        path = root / artifact["path"]
        if not path.exists():
            report.mismatches.append(
                f"{manifest.get('run_id','?')}: raw stream {artifact['path']} is missing"
            )
            continue
        problems, compared, records = check_run(manifest, _load_raw(path))
        report.runs_checked += 1
        report.fields_compared += compared
        report.records_compared += records
        report.mismatches.extend(problems)
    return report
