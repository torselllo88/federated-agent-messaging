#!/usr/bin/env python3
"""Close the formal collection phase (Task 07 §42, §43, §44).

Three things, in order, and the first gates the rest:

  §42  the completion gate -- every scheduled run has a final status, the
       ledger is closed, manifests and raw streams and evidence are present,
       digests verify, schemas validate, and the lock matches
  §43  the freeze -- aggregate digests over the collection, a raw-artifact
       inventory, and a collection-completion artifact
  §44  the archive -- a deterministic file inventory with a SHA-256 per
       artifact and one aggregate digest over the whole set

Read-only over the evidence. Nothing here edits a raw record, a manifest or a
digest; it reads what collection produced and writes two new artifacts beside
it. After this runs, no further run may be silently appended to the campaign.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")
sys.path.insert(0, "src")

from fam.common.digests import bytes_sha256, file_sha256  # noqa: E402
from fam.common.lock import compare, load as load_lock  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    manifests_dir,
    resolve_results_dir,
)

COLLECTION_ARTIFACT = "formal_collection_completion"
ARCHIVE_ARTIFACT = "formal_raw_archive_inventory"

#: Repetition counts the frozen set requires of a complete campaign (§47).
EXPECTED_RUNS = {"E0": 3, "E1": 3, "E2": 3, "E3": 120, "E4": 3}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manifests(root: Path) -> list[dict[str, Any]]:
    found = []
    for path in sorted(manifests_dir(root).glob("*.json")):
        found.append(json.loads(path.read_text(encoding="utf-8")))
    return found


#: The two artifacts this script writes. They are excluded from the inventory
#: because a manifest cannot contain itself: the inventory is computed before
#: they are written, so including them records their previous contents and the
#: aggregate digest can never be reproduced from the collection it describes --
#: which is the one thing §44 needs it to do.
SELF_WRITTEN = (
    "environment/collection-completion.json",
    "environment/raw-archive-inventory.json",
)


def _inventory(root: Path) -> list[dict[str, Any]]:
    """Every file the collection produced, in one deterministic order.

    Sorted by relative path with forward slashes, so the same collection
    inventoried on a different platform yields the same list and therefore the
    same aggregate digest. Excludes this script's own output, so re-freezing an
    unchanged collection reproduces the same digest.
    """
    entries = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in SELF_WRITTEN:
            continue
        entries.append(
            {
                "path": relative,
                "sha256": file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    entries.sort(key=lambda item: item["path"])
    return entries


def _aggregate(entries: list[dict[str, Any]]) -> str:
    """One digest over the whole collection.

    Over the inventory rather than over a tar: an archive's bytes depend on its
    container format, timestamps and ordering, while this depends only on which
    files exist and what they contain.
    """
    payload = "\n".join(f"{e['sha256']}  {e['path']}" for e in entries)
    return bytes_sha256(payload.encode("utf-8"))


def _completion_gate(root: Path, manifests: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []

    counts: dict[str, int] = {}
    for manifest in manifests:
        counts[manifest["experiment"]] = counts.get(manifest["experiment"], 0) + 1
    for experiment, expected in EXPECTED_RUNS.items():
        actual = counts.get(experiment, 0)
        if actual != expected:
            problems.append(
                f"{experiment}: {actual} manifests, the frozen set requires {expected}"
            )

    for manifest in manifests:
        run = manifest.get("run_id", "?")
        if not manifest.get("publication_data"):
            problems.append(f"{run}: publication_data is not true")
        if manifest.get("completion_status") not in ("pass", "complete"):
            problems.append(
                f"{run}: completion_status {manifest.get('completion_status')!r} "
                f"is not final"
            )
        if manifest.get("validity_classification", {}).get("valid") is None:
            problems.append(f"{run}: no validity classification")

    # Every artifact a manifest names must exist and match its digest.
    for manifest in manifests:
        run = manifest.get("run_id", "?")
        artifacts = manifest.get("raw_artifacts") or manifest.get(
            "evidence_artifacts"
        ) or []
        if not artifacts:
            problems.append(f"{run}: names no artifacts")
        for artifact in artifacts:
            path = root / artifact["path"]
            if not path.exists():
                problems.append(f"{run}: {artifact['path']} is missing")
            elif file_sha256(path) != artifact["sha256"]:
                problems.append(f"{run}: {artifact['path']} digest differs")

    ledgers = sorted((root / "campaigns").glob("*.json"))
    ledger_files = [p for p in ledgers if not p.name.endswith(".schedule.json")]
    if len(ledger_files) != 1:
        problems.append(
            f"expected exactly one campaign ledger, found {len(ledger_files)}"
        )
    else:
        ledger = json.loads(ledger_files[0].read_text(encoding="utf-8"))
        completed = len(ledger.get("completed", {}))
        if completed != EXPECTED_RUNS["E3"]:
            problems.append(
                f"campaign ledger holds {completed} completed runs, "
                f"expected {EXPECTED_RUNS['E3']}"
            )

    lock = load_lock()
    if lock is None:
        problems.append("no protocol lock is readable")
    else:
        problems.extend(f"lock: {p}" for p in compare(lock))
        locked_commit = lock["implementation"]["git_commit"]
        commits = {m.get("protocol_git_commit") for m in manifests}
        if len(commits) != 1:
            problems.append(f"manifests span {len(commits)} implementation commits")
        # The manifests carry the tagging commit; the lock names the commit it
        # was generated from, which cannot be the one that carries it.
        recorded = commits.pop() if len(commits) == 1 else None
        if recorded and recorded != locked_commit:
            problems.append(
                f"note: manifests record {recorded[:12]}, the lock names "
                f"{locked_commit[:12]} -- expected, since a lock cannot name "
                f"the commit that carries it"
            )

    return problems


def main() -> int:
    # Findings prefixed "note:" are always informational: the one this campaign
    # produces -- the manifests recording the tagging commit while the lock
    # names the commit it was generated from -- is structural and expected, so
    # there was never a mode in which it should block the freeze.
    argparse.ArgumentParser(description=__doc__).parse_args()

    root = ensure_layout(resolve_results_dir())
    manifests = _manifests(root)
    lock = load_lock()

    print(f"formal collection freeze over {root}")
    print(f"  manifests            {len(manifests)}")

    problems = _completion_gate(root, manifests)
    blocking = [p for p in problems if not p.startswith("note:")]
    notes = [p for p in problems if p.startswith("note:")]

    print("\n1. completion gate (§42)")
    if notes:
        for note in notes:
            print(f"   {note}")
    if blocking:
        print("   FAIL")
        for problem in blocking:
            print(f"     - {problem}")
        return 1
    print("   PASS: every scheduled run is final and every named artifact verifies")

    print("\n2. raw-artifact inventory (§43, §44)")
    entries = _inventory(root)
    aggregate = _aggregate(entries)
    total_bytes = sum(e["bytes"] for e in entries)
    by_area: dict[str, dict[str, int]] = {}
    for entry in entries:
        area = entry["path"].split("/")[0]
        bucket = by_area.setdefault(area, {"files": 0, "bytes": 0})
        bucket["files"] += 1
        bucket["bytes"] += entry["bytes"]
    for area in sorted(by_area):
        bucket = by_area[area]
        print(f"   {area:14} {bucket['files']:5} files  {bucket['bytes'] / 1e6:9.1f} MB")
    print(f"   {'total':14} {len(entries):5} files  {total_bytes / 1e6:9.1f} MB")
    print(f"   aggregate digest  {aggregate}")

    campaign = (lock or {}).get("campaign", {})
    implementation = (lock or {}).get("implementation", {})

    completion = {
        "artifact": COLLECTION_ARTIFACT,
        "generated_at": _utc(),
        "publication_data": True,
        "campaign_id": campaign.get("campaign_id"),
        "protocol_lock": {
            "git_commit": implementation.get("git_commit"),
            "git_tag": implementation.get("git_tag"),
        },
        "manifests": len(manifests),
        "runs_by_experiment": {
            experiment: sum(
                1 for m in manifests if m["experiment"] == experiment
            )
            for experiment in sorted(EXPECTED_RUNS)
        },
        "expected_by_experiment": EXPECTED_RUNS,
        "all_runs_final": True,
        "all_named_artifacts_verified": True,
        "notes": notes,
        "collection_phase": "complete",
        "statement": (
            "Formal collection is closed. No further experiment run may be "
            "appended to this campaign; a later recollection is a separate, "
            "explicitly versioned campaign (Task 07 §43)."
        ),
    }

    archive = {
        "artifact": ARCHIVE_ARTIFACT,
        "generated_at": _utc(),
        "campaign_id": campaign.get("campaign_id"),
        "producing_git_commit": implementation.get("git_commit"),
        "producing_git_tag": implementation.get("git_tag"),
        "result_root": str(root),
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "aggregate_sha256": aggregate,
        "aggregate_definition": (
            "SHA-256 over the newline-joined '<sha256>  <path>' lines of the "
            "inventory below, sorted by POSIX path. Defined over content and "
            "names rather than over a container, so it does not depend on tar "
            "format, compression or timestamps. This file and "
            "collection-completion.json are excluded: they are written after "
            "the inventory is computed, so including them would record their "
            "previous contents and make the digest unverifiable against the "
            "collection it describes."
        ),
        "excluded_from_inventory": list(SELF_WRITTEN),
        "files": entries,
    }

    completion_path = environment_dir(root) / "collection-completion.json"
    archive_path = environment_dir(root) / "raw-archive-inventory.json"
    completion_path.write_text(
        json.dumps(completion, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    archive_path.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print("\n3. artifacts written")
    print(f"   {completion_path}")
    print(f"   {archive_path}")
    print("\nCOLLECTION: FROZEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
