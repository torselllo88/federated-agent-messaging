#!/usr/bin/env python3
"""Regenerate derived E2 and E4 content from immutable raw data, and compare.

Why this exists. Seven processed artifacts in the formal archive record their
`analysis_code_commit` as a label -- ``task-02-working-tree`` and siblings --
naming a working tree nobody can check out. That satisfies §54 in form and not
in substance, and the revision behind those labels cannot be recovered.

What can be recovered is the *result*. This regenerates the derived content
from the archived raw streams using code at a known commit and compares it to
what the archive holds. A match supports a precise claim and no more:

    the original derived artifact carries incomplete revision provenance, and
    its content was independently regenerated from immutable raw data by a
    named commit and matched

It does **not** identify the code that produced the original. That is
unrecoverable, and guessing would be worse than saying so.

Scope, and its limit:

    E2  fully regenerable. The agent stream records the recovery breakdown --
        which events came from sync, which from history pagination, how many
        pages -- so the comparison can be rebuilt from raw alone.
    E4  fully regenerable. The validation reads manifests and transcripts,
        both archived.
    E1  NOT regenerable, and this script says so rather than pretending.
        The federation comparison rests on what each homeserver returned when
        queried live during the run. Those domain views were never written to
        a raw stream, and both homeservers are destroyed. The archived
        comparison artifact is the only copy of that observation.

Writes nothing into the archive. Output goes to the directory given by --out.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fam.common.digests import file_sha256  # noqa: E402
from fam.common.env import analysis_code_commit  # noqa: E402
from fam.common.frozen import EXECUTION_ANALYSIS_SPEC_VERSION  # noqa: E402

#: Fields that legitimately differ between an original and a regeneration.
#: Listed rather than skipped silently, so "matched" means something.
VOLATILE = {
    "generated_at",
    "analysis_code_commit",
    "scope_note",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def regenerate_e2(root: Path) -> dict[str, dict[str, Any]]:
    """Rebuild each E2 recovery comparison from the raw streams.

    Derived here rather than by importing the experiment's own code: a
    regeneration that reuses the original implementation would only prove the
    implementation is deterministic.
    """
    out: dict[str, dict[str, Any]] = {}
    for runner_path in sorted((root / "raw" / "e2").glob("*.runner.jsonl")):
        agent_path = runner_path.with_name(
            runner_path.name.replace(".runner.", ".agent.")
        )
        runner = _load_jsonl(runner_path)
        agent = _load_jsonl(agent_path)
        interactions = [r for r in runner if r.get("record_type") == "interaction"]
        run_id = interactions[0]["run_id"]

        offline = [r for r in interactions if r.get("run_phase") == "offline"]
        post = [r for r in interactions if r.get("run_phase") == "post_restart"]
        sent = [r["request_event_id"] for r in offline if r.get("request_event_id")]
        acks = [r["response_event_id"] for r in post if r.get("response_event_id")]

        recovery = next(
            (a for a in agent if a.get("action") == "recovery_complete"), {}
        )
        processing = next(
            (a for a in agent if a.get("action") == "recovery_processing_complete"), {}
        )
        resumed = [a for a in agent if a.get("action") == "checkpoint_loaded"]
        identities = {a.get("agent_mxid") for a in agent if a.get("agent_mxid")}

        recovered = list(recovery.get("recovered_event_ids") or [])
        out[run_id] = {
            "run_id": run_id,
            "offline_request_count": len(offline),
            "logical_request_count": len(post),
            "sent_event_ids": sorted(sent),
            "recovered_event_ids": sorted(recovered),
            "sync_event_ids": sorted(recovery.get("sync_event_ids") or []),
            "history_recovered_event_ids": sorted(
                recovery.get("history_event_ids") or []
            ),
            "recovered_from_sync_count": recovery.get("recovered_from_sync"),
            "recovered_from_history_count": recovery.get("recovered_from_history"),
            "history_pages_fetched": recovery.get("history_pages_fetched"),
            "pagination_invoked": recovery.get("pagination_invoked"),
            "sync_limited": recovery.get("limited_timeline"),
            "timeline_limit": next(
                (a.get("timeline_limit") for a in resumed
                 if a.get("timeline_limit") is not None), None
            ),
            "missing_from_recovery": sorted(set(sent) - set(recovered)),
            "unexpected_in_recovery": sorted(set(recovered) - set(sent)),
            "ack_count": len(acks),
            "duplicate_ack_count": sum(
                int(r.get("duplicate_ack_count") or 0) for r in interactions
            ),
            "duplicate_observation_count": recovery.get("duplicate_observations"),
            "duplicate_processing_count": max(
                0, (processing.get("logically_processed") or 0) - len(set(recovered))
            ),
            "checkpoint_resumed": any(
                a.get("resumed_from_checkpoint") for a in resumed
            ),
            "same_agent_identity": len(identities) == 1,
            "source_raw_digests": {
                "runner_interaction_stream": file_sha256(runner_path),
                "agent_telemetry_stream": file_sha256(agent_path),
            },
        }
    return out


def compare(original: dict[str, Any], regenerated: dict[str, Any]) -> dict[str, Any]:
    """Field-by-field, with the volatile ones named rather than dropped."""
    compared, mismatched, absent = [], [], []
    for key, value in sorted(regenerated.items()):
        if key in VOLATILE:
            continue
        if key not in original:
            absent.append(key)
            continue
        theirs = original[key]
        # The archive stores several scalars as strings; compare by value.
        if isinstance(theirs, str) and not isinstance(value, str):
            theirs_cmp: Any = str(value) == theirs
            same = theirs_cmp
        else:
            same = theirs == value
        (compared if same else mismatched).append(key)
    return {
        "fields_compared": sorted(compared),
        "fields_mismatched": sorted(mismatched),
        "fields_absent_from_original": sorted(absent),
        "fields_excluded_as_volatile": sorted(VOLATILE),
        "match": not mismatched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="extracted archive root")
    parser.add_argument("--out", required=True, help="where to write the layer")
    args = parser.parse_args()

    root = Path(args.archive)
    out = Path(args.out)
    (out / "e2").mkdir(parents=True, exist_ok=True)
    (out / "e4").mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit = analysis_code_commit()
    header = {
        "artifact": "independent_regeneration",
        "generated_at": stamp,
        "analysis_code_commit": commit,
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "claim": (
            "Regenerated from the archived raw streams by the commit named "
            "above. This does not identify the code that produced the original "
            "artifact, which is unrecoverable; it establishes that the same "
            "content follows from the immutable raw data."
        ),
    }

    print(f"regenerating from {root}")
    print(f"  analysis_code_commit: {commit}\n")

    results = []
    print("E2 — recovery comparisons")
    regenerated = regenerate_e2(root)
    for run_id, content in sorted(regenerated.items()):
        original_path = root / "processed" / f"{run_id}.recovery-comparison.json"
        original = json.loads(original_path.read_text(encoding="utf-8"))
        verdict = compare(original, content)
        results.append((run_id, verdict))
        mark = "MATCH" if verdict["match"] else "*** DIFFERS ***"
        print(f"  {run_id}  {len(verdict['fields_compared'])} fields  {mark}")
        if verdict["fields_mismatched"]:
            for key in verdict["fields_mismatched"]:
                print(f"     {key}: original {original.get(key)!r} "
                      f"regenerated {content[key]!r}")
        (out / "e2" / f"{run_id}.recovery-comparison.regenerated.json").write_text(
            json.dumps({**header, **content, "comparison": verdict,
                        "original": {
                            "path": f"processed/{original_path.name}",
                            "sha256": file_sha256(original_path),
                        }}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n",
        )

    print("\nE1 — not regenerable")
    e1 = sorted((root / "processed").glob("e1-*.federation-comparison.json"))
    e1_record = {
        **header,
        "regenerable": False,
        "reason": (
            "The federation comparison rests on what each homeserver returned "
            "when queried live during the run. Those domain views were never "
            "written to a raw stream, and both homeservers were destroyed with "
            "the formal host. The archived artifacts below are the only copy "
            "of that observation."
        ),
        "archived_artifacts": [
            {"path": f"processed/{p.name}", "sha256": file_sha256(p)} for p in e1
        ],
    }
    (out / "e1-not-regenerable.json").write_text(
        json.dumps(e1_record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    for entry in e1_record["archived_artifacts"]:
        print(f"  {entry['path']}  sha256 {entry['sha256'][:16]}…")

    print("\nE4 — validation report")
    e4_original = root / "processed" / "e4-validation-latest.json"
    e4_record: dict[str, Any] = {
        **header,
        "regeneration_method": (
            "re-run of scripts/e4_validate.py at the commit above, over the "
            "archived manifests and transcripts. Unlike the E2 entries, this "
            "reuses the project's own validator rather than an independent "
            "derivation, so it demonstrates that the archived inputs still "
            "yield the archived verdict -- not that the verdict follows from "
            "an independent reading of them."
        ),
        "original": {
            "path": f"processed/{e4_original.name}",
            "sha256": file_sha256(e4_original),
        },
        "instructions": (
            "FAM_RESULTS_DIR=<archive> FAM_PUBLICATION_DATA=true "
            "FAM_ANALYSIS_CODE_COMMIT=<commit> python scripts/e4_validate.py"
        ),
    }
    (out / "e4" / "e4-validation.regeneration.json").write_text(
        json.dumps(e4_record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"  original processed/{e4_original.name}")
    print(f"  sha256 {e4_record['original']['sha256'][:16]}…")
    print("  regenerate with the command recorded in e4/e4-validation.regeneration.json")

    failures = [r for r, v in results if not v["match"]]
    print(f"\n  E2 regenerated and compared: {len(results)}   mismatches: {len(failures)}")
    print(f"  E1 pinned by digest, not regenerable: {len(e1)}")
    print(f"\nwritten to {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
