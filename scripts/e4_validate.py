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
from fam.common.env import publication_data  # noqa: E402
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

# --------------------------------------------------------------- secrets
#
# Two different questions, deliberately separated.
#
# In *structured* fields a credential must never appear at all, and those
# fields have known shapes, so anything credential-like there is a defect.
#
# In *transcript prose* the human and the model write whatever they like. A
# person who types the word "authorization" has not leaked anything, and the
# previous keyword scan would have failed that session. Matrix event ids made
# it worse: they are random base64 and contain the substring "sk-" roughly
# once in seven thousand, which was observed dozens of times across E3.
#
# Prose is therefore matched only against high-confidence, provider-specific
# credential formats — the actual prefixes real keys carry.

#: Concrete credential formats. Deliberately specific: a generic "sk-" prefix
#: matches ordinary identifiers, these do not.
CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{24,}"),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{24,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}"),
)

#: Additionally forbidden inside structured fields, where a credential header
#: or an environment dump has no business appearing at all.
STRUCTURED_ONLY_PATTERNS = (
    re.compile(r"x-api-key", re.IGNORECASE),
    re.compile(r"\bauthorization\b\s*[:=]", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"FAM_LLM_API_KEY", re.IGNORECASE),
)

#: Manifest keys whose values are free prose written by a person or a model.
#: Everything else in a manifest is structured.
PROSE_FIELDS = frozenset({"request_text", "response_text", "note", "system_prompt"})


def load_manifests(root: Path) -> list[dict[str, Any]]:
    found = []
    for path in sorted(manifests_dir(root).glob("*.manifest.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("experiment") == "E4":
            payload["_path"] = path
            found.append(payload)
    return found


#: Key names that must not appear anywhere in a structured artifact. A field
#: called `authorization` is a credential header whatever it holds, so the
#: name alone disqualifies it and no value pattern needs to match.
FORBIDDEN_KEYS = frozenset(
    {
        "authorization",
        "x-api-key",
        "api_key",
        "apikey",
        "api-key",
        "fam_llm_api_key",
        "access_token",
        "password",
        "secret",
    }
)


def _split_prose(
    document: object, prose: list[str], structured: list[str], keys: list[str]
) -> None:
    """Walk a JSON document, separating human prose from structured content."""
    if isinstance(document, dict):
        for key, value in document.items():
            keys.append(str(key))
            if key in PROSE_FIELDS and isinstance(value, str):
                prose.append(value)
            else:
                structured.append(str(key))
                _split_prose(value, prose, structured, keys)
    elif isinstance(document, list):
        for item in document:
            _split_prose(item, prose, structured, keys)
    elif isinstance(document, str):
        structured.append(document)


def scan_secrets(document: object) -> list[str]:
    """Report credential material, without failing a session over prose.

    Structured content is held to both rule sets. Prose is held only to the
    concrete credential formats: a real key pasted into a chat message is
    still caught, a person writing the word "authorization" is not.
    """
    prose: list[str] = []
    structured: list[str] = []
    keys: list[str] = []
    _split_prose(document, prose, structured, keys)

    problems: list[str] = []
    for key in keys:
        if key.strip().lower() in FORBIDDEN_KEYS:
            problems.append(f"artifact carries a credential-bearing field: {key!r}")
    structured_text = "\n".join(structured)
    for pattern in CREDENTIAL_PATTERNS + STRUCTURED_ONLY_PATTERNS:
        if pattern.search(structured_text):
            problems.append(f"structured field matches {pattern.pattern}")
    prose_text = "\n".join(prose)
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(prose_text):
            problems.append(f"transcript prose contains a credential: {pattern.pattern}")
    return sorted(set(problems))


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
    # The mode is an input, never an assumption. This check was written when
    # every E4 session was development and asserted `is False`, which is the
    # opposite of what formal evidence must carry (Task 07 §22). It guards both
    # directions: a development session must not present itself as publication
    # evidence, and a formal session must not present itself as development.
    expected = publication_data()
    if manifest.get("publication_data") is not expected:
        problems.append(
            f"publication_data is {manifest.get('publication_data')!r}, but "
            f"this validation expects {expected!r} "
            f"(FAM_PUBLICATION_DATA={os.environ.get('FAM_PUBLICATION_DATA', 'unset')!r})"
        )

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

    # No credential in any artifact this session produced. Prose is judged
    # differently from structured fields; see scan_secrets.
    leaks = scan_secrets({k: v for k, v in manifest.items() if k != "_path"})
    if transcript_text:
        try:
            leaks += scan_secrets(json.loads(transcript_text))
        except ValueError:
            pass
    if leaks:
        problems.append(f"credential material in artifacts: {sorted(set(leaks))}")

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
