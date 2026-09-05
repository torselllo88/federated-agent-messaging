#!/usr/bin/env python3
"""E4 validation: check the sessions that were recorded, without rerunning them.

Reads the E4 manifests and their evidence and confirms, independently of the
session runner, that each session actually established what it claims:
three-party membership across two domains, at least three answered
natural-language requests from the actual human, exact request/response
correspondence, intact evidence digests, and no secret in any artifact.

E4 passes overall at 3/3 valid sessions (experimental-protocol.md §41).
Partial sessions are never aggregated: a session with two answered requests
does not contribute two-thirds of a pass.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from fam.common.digests import file_sha256  # noqa: E402
from fam.common.frozen import EXECUTION_ANALYSIS_SPEC_VERSION  # noqa: E402
from fam.common.results import (  # noqa: E402
    manifests_dir,
    resolve_results_dir,
)

ACTUAL_HUMAN = "@actual-human:hs-a.test"
HUMAN_ROLE_B = "@human-role-b:hs-b.test"
LLM_AGENT = "@llm-agent:hs-b.test"
EXPECTED_MEMBERSHIP = {ACTUAL_HUMAN, HUMAN_ROLE_B, LLM_AGENT}
MINIMUM_REQUESTS = 3
REQUIRED_SESSIONS = 3

ANALYSIS_CODE_COMMIT = "task-06-working-tree"

#: Shapes that must never appear in an artifact. Checked against the whole
#: manifest and transcript text, not only the fields we expect to be present.
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"x-api-key", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
)


def load_manifests(root: Path) -> list[dict[str, Any]]:
    found = []
    for path in sorted(manifests_dir(root).glob("*.manifest.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("experiment") == "E4":
            payload["_path"] = path
            found.append(payload)
    return found


def _secret_findings(text: str) -> list[str]:
    return sorted({pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)})


def validate_session(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    session_id = manifest.get("run_id", "?")

    if manifest.get("manifest_type") != "human_llm_validation_manifest":
        problems.append(
            f"manifest_type is {manifest.get('manifest_type')!r}, expected the "
            "frozen E4 variant"
        )
    if manifest.get("room_version") != "12":
        problems.append(f"room version {manifest.get('room_version')!r}")
    if manifest.get("publication_data") is not False:
        problems.append("publication_data is not false for a development session")

    participants = set((manifest.get("participants") or {}).values())
    if participants != EXPECTED_MEMBERSHIP:
        problems.append(f"participants {sorted(participants)}")
    if not manifest.get("three_party_topology_confirmed"):
        problems.append("three-party topology not confirmed")

    # Correspondence: every recorded interaction must name both events, and at
    # least three must come from the actual human.
    exchanges = manifest.get("interaction_event_ids") or []
    from_human = [e for e in exchanges if e.get("request_sender") == ACTUAL_HUMAN]
    answered = [e for e in from_human if e.get("response_event_id")]
    if len(answered) < MINIMUM_REQUESTS:
        problems.append(
            f"{len(answered)} answered requests from the actual human, "
            f"minimum {MINIMUM_REQUESTS}"
        )
    request_ids = [e.get("request_event_id") for e in answered]
    response_ids = [e.get("response_event_id") for e in answered]
    if len(set(request_ids)) != len(request_ids):
        problems.append("duplicate request event ids")
    if len(set(response_ids)) != len(response_ids):
        problems.append("one response event answers more than one request")
    for exchange in answered:
        if not exchange.get("llm_model"):
            problems.append(
                f"{exchange.get('request_event_id')} records no model identifier"
            )

    reported_models = {e.get("llm_model") for e in answered if e.get("llm_model")}
    if len(reported_models) > 1:
        problems.append(
            f"responses came from more than one model: {sorted(reported_models)}"
        )

    if not manifest.get("human_confirmed_responses_visible"):
        problems.append("the human did not confirm the responses were visible")

    # Evidence must exist and still match its recorded digest.
    evidence = manifest.get("evidence_artifacts") or []
    if not any(item.get("role") == "transcript" for item in evidence):
        problems.append("no transcript evidence referenced")
    transcript_text = ""
    for item in evidence:
        path = root / item["path"]
        if not path.exists():
            problems.append(f"evidence missing: {item['path']}")
            continue
        if file_sha256(path) != item.get("sha256"):
            problems.append(f"evidence digest mismatch: {item['path']}")
        if item.get("role") == "transcript":
            transcript_text = path.read_text(encoding="utf-8")

    # Transcript must corroborate the manifest rather than merely exist.
    if transcript_text:
        try:
            transcript = json.loads(transcript_text)
        except ValueError:
            problems.append("transcript is not valid JSON")
            transcript = {}
        if transcript.get("room_id") != manifest.get("room_id"):
            problems.append("transcript room id does not match the manifest")
        transcript_pairs = {
            (e.get("request_event_id"), e.get("response_event_id"))
            for e in transcript.get("exchanges", [])
        }
        manifest_pairs = {
            (e.get("request_event_id"), e.get("response_event_id"))
            for e in exchanges
        }
        if not manifest_pairs <= transcript_pairs:
            problems.append("manifest interactions are not all in the transcript")

    # No secret in any artifact this session produced.
    blob = json.dumps({k: v for k, v in manifest.items() if k != "_path"})
    leaks = _secret_findings(blob) + _secret_findings(transcript_text)
    if leaks:
        problems.append(f"possible secret material in artifacts: {sorted(set(leaks))}")

    validity = manifest.get("validity_classification") or {}
    return {
        "session_id": session_id,
        "room_id": manifest.get("room_id"),
        "valid": bool(validity.get("valid")),
        "invalid_class": validity.get("invalid_class"),
        "completion_status": manifest.get("completion_status"),
        "functional_result": manifest.get("functional_result"),
        "answered_requests_from_human": len(answered),
        "participants": sorted(participants),
        "llm_provider": manifest.get("llm_provider"),
        "llm_model": manifest.get("llm_model"),
        # Derived from the interaction records rather than read from the
        # manifest, so it is available for every session regardless of which
        # fields its manifest happened to carry.
        "llm_models_reported_by_provider": sorted(
            {e.get("llm_model") for e in answered if e.get("llm_model")}
        ),
        "agent_configuration_hash": manifest.get("agent_configuration_hash"),
        "evidence": [
            {"role": item.get("role"), "path": item.get("path")} for item in evidence
        ],
        "problems": problems,
        "passed": not problems and manifest.get("functional_result") == "pass",
    }


def main() -> int:
    root = resolve_results_dir(create=False)
    print(f"E4 validation over {root}\n")

    manifests = load_manifests(root)
    if not manifests:
        print("no E4 sessions found")
        return 1

    results = [validate_session(root, manifest) for manifest in manifests]
    valid_passes = [r for r in results if r["passed"] and r["valid"]]

    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        marker = "" if item["valid"] else f"  [INVALID: {item['invalid_class']}]"
        print(f"  {item['session_id']}  {status}{marker}")
        print(f"    room        {item['room_id']}")
        print(f"    participants {item['participants']}")
        print(
            f"    answered    {item['answered_requests_from_human']} "
            f"natural-language requests from the human"
        )
        reported = item["llm_models_reported_by_provider"]
        print(f"    model       {item['llm_provider']} / {item['llm_model']}")
        if reported and reported != [item["llm_model"]]:
            print(f"                provider actually used: {', '.join(reported)}")
        print(f"    evidence    {[e['path'] for e in item['evidence']]}")
        for problem in item["problems"]:
            print(f"    ! {problem}")

    print(
        f"\n  valid sessions passed: {len(valid_passes)} / {REQUIRED_SESSIONS} required"
    )
    invalid = [r for r in results if not r["valid"]]
    if invalid:
        print(f"  invalid sessions preserved: {[r['session_id'] for r in invalid]}")

    ok = len(valid_passes) >= REQUIRED_SESSIONS
    report = {
        "artifact": "e4_validation_summary",
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "publication_data": False,
        "required_sessions": REQUIRED_SESSIONS,
        "sessions": results,
        "valid_sessions_passed": len(valid_passes),
        "verdict": f"{len(valid_passes)}/{REQUIRED_SESSIONS} PASS",
        "scope_note": (
            "Development validation. C4 is not marked collected and no "
            "evidence counter is updated (Task 06 §33)."
        ),
    }
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    path = processed / "e4-validation-latest.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n  report: {path}")
    print(f"\nE4 VALIDATE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
