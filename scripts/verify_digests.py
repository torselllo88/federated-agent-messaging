#!/usr/bin/env python3
"""Independent verification of recorded result digests.

Digest verification runs before analysis: analysing a dataset whose per-file
SHA-256 does not match its manifest is a provenance failure, not a data
question (experimental-protocol.md §40).

Each manifest records the SHA-256 of both of its raw streams, so the archive
is verifiable file by file rather than only as one blob
(testbed-architecture.md §22).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app/src")

from fam.common.digests import file_sha256  # noqa: E402
from fam.common.results import manifests_dir, resolve_results_dir  # noqa: E402


def verify(root: Path) -> tuple[int, int, list[str]]:
    checked = 0
    failed = 0
    problems: list[str] = []

    directory = manifests_dir(root)
    if not directory.exists():
        return 0, 0, [f"no manifests directory at {directory}"]

    for manifest_path in sorted(directory.glob("*.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_id = manifest.get("run_id", manifest_path.stem)
        artifacts = list(manifest.get("raw_artifacts", []))

        # E4 produces no interaction stream. Its evidence is the transcript,
        # any screenshots, and the agent telemetry — each carrying its own
        # SHA-256 (experimental-protocol.md §38, human_llm_validation_manifest).
        artifacts += list(manifest.get("evidence_artifacts", []))
        telemetry = manifest.get("agent_telemetry")
        if isinstance(telemetry, dict) and telemetry.get("path"):
            artifacts.append(telemetry)

        if not artifacts:
            kind = manifest.get("manifest_type", "manifest")
            problems.append(f"{run_id}: {kind} records no verifiable artifacts")
            failed += 1
            continue
        for artifact in artifacts:
            checked += 1
            target = root / artifact["path"]
            if not target.exists():
                problems.append(f"{run_id}: missing {artifact['path']}")
                failed += 1
                continue
            actual = file_sha256(target)
            if actual != artifact["sha256"]:
                problems.append(
                    f"{run_id}: digest mismatch for {artifact['path']}\n"
                    f"    manifest {artifact['sha256']}\n"
                    f"    actual   {actual}"
                )
                failed += 1
    return checked, failed, problems


def main() -> int:
    root = resolve_results_dir(create=False)
    checked, failed, problems = verify(root)

    print(f"digest verification over {root}")
    print(f"  artifacts checked: {checked}")
    print(f"  failures:          {failed}")
    for problem in problems:
        print(f"  ! {problem}")

    if checked == 0:
        print("\nDIGESTS: NOTHING TO VERIFY")
        return 1
    print(f"\nDIGESTS: {'PASS' if failed == 0 else 'FAIL'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
