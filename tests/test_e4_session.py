"""E4 session acceptance, manifest and evidence validation.

These exercise the rules that decide whether a session counts, without a
homeserver, a provider or a person: what a manifest must contain, what
correspondence between a human request and an LLM response means, and which
sessions must be refused.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fam.common.validity import VALID, InvalidRunClass, invalid
from fam.instrumentation.manifest import (
    HUMAN_LLM,
    EvidenceArtifact,
    HumanValidationManifest,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from e4_validate import validate_session  # noqa: E402

ACTUAL_HUMAN = "@actual-human:hs-a.test"
HUMAN_ROLE_B = "@human-role-b:hs-b.test"
LLM_AGENT = "@llm-agent:hs-b.test"

PARTICIPANTS = {
    "actual_human": ACTUAL_HUMAN,
    "human_role_b": HUMAN_ROLE_B,
    "llm_agent": LLM_AGENT,
}


def _exchange(index: int, sender: str = ACTUAL_HUMAN, answered: bool = True) -> dict:
    return {
        "request_event_id": f"$req{index}",
        "request_sender": sender,
        "response_event_id": f"$res{index}" if answered else None,
        "response_sender": LLM_AGENT,
        "llm_provider": "anthropic",
        "llm_model": "test-model-1",
        "execution": {"executor": "llm", "http_status": 200},
    }


def _manifest(tmp_path: Path, **overrides) -> HumanValidationManifest:
    defaults = dict(
        session_id="e4-20260905T120000Z",
        room_id="!room:hs-b.test",
        participants=PARTICIPANTS,
        publication_data=False,
        protocol_git_commit="abc123",
        human_client_name="Element",
        human_client_version="1.11.0",
        human_client_host="workstation/Windows",
        llm_provider="anthropic",
        llm_model="test-model-1",
        agent_config_hash="0" * 64,
        executor_identifier="llm",
        interaction_event_ids=[_exchange(i) for i in (1, 2, 3)],
        completion_status="pass",
        functional_result="pass",
        validity=VALID,
        three_party_topology_confirmed=True,
        results_root=tmp_path,
        extra={"human_confirmed_responses_visible": True},
    )
    defaults.update(overrides)
    return HumanValidationManifest(**defaults)


def _write_session(tmp_path: Path, **overrides) -> dict:
    """Write a complete, self-consistent session to disk and load it back."""
    evidence_dir = tmp_path / "evidence" / "e4-20260905T120000Z"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    exchanges = overrides.pop("interaction_event_ids", [_exchange(i) for i in (1, 2, 3)])
    transcript = evidence_dir / "transcript.json"
    transcript.write_text(
        json.dumps(
            {
                "artifact": "e4_session_transcript",
                "room_id": overrides.get("room_id", "!room:hs-b.test"),
                "exchanges": exchanges,
            }
        ),
        encoding="utf-8",
    )

    manifest = _manifest(
        tmp_path, interaction_event_ids=exchanges, **overrides
    )
    manifest.evidence = [EvidenceArtifact("transcript", transcript)]
    manifest.write(tmp_path / "manifests")
    path = tmp_path / "manifests" / f"{manifest.session_id}.manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- manifest


def test_manifest_uses_the_frozen_e4_variant(tmp_path):
    payload = _manifest(tmp_path).to_dict()
    assert payload["manifest_type"] == HUMAN_LLM
    assert payload["experiment"] == "E4"
    assert payload["three_party_topology_confirmed"] is True
    assert payload["executor_identifier"] == "llm"
    assert payload["human_client_name"] == "Element"


def test_manifest_omits_benchmark_fields_by_design(tmp_path):
    """§38: E4 produces no measurements, so it carries no timing fields."""
    payload = _manifest(tmp_path).to_dict()
    for field in (
        "concurrency",
        "max_in_flight",
        "window_start_ns",
        "window_end_ns",
        "drain_end_ns",
        "warmup_seconds",
        "measurement_seconds",
        "topology",
    ):
        assert field not in payload, f"{field} has no meaning for E4"


def test_manifest_requires_a_validity_classification(tmp_path):
    manifest = _manifest(tmp_path, validity=None)
    with pytest.raises(ValueError):
        manifest.to_dict()


def test_manifest_records_evidence_digests(tmp_path):
    artifact = tmp_path / "transcript.json"
    artifact.write_text('{"exchanges": []}', encoding="utf-8")
    manifest = _manifest(tmp_path)
    manifest.evidence = [EvidenceArtifact("transcript", artifact)]
    payload = manifest.to_dict()
    entry = payload["evidence_artifacts"][0]
    assert entry["role"] == "transcript"
    assert len(entry["sha256"]) == 64
    assert entry["bytes"] > 0


# -------------------------------------------------------------- acceptance


def test_a_complete_session_passes(tmp_path):
    result = validate_session(tmp_path, _write_session(tmp_path))
    assert result["problems"] == []
    assert result["passed"] is True
    assert result["answered_requests_from_human"] == 3


def test_two_answered_requests_do_not_pass(tmp_path):
    """§22: a session with two exchanges is not two-thirds of a pass."""
    manifest = _write_session(
        tmp_path, interaction_event_ids=[_exchange(1), _exchange(2)]
    )
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("minimum 3" in p for p in result["problems"])


def test_requests_from_the_programmatic_participant_do_not_count(tmp_path):
    """§12: the actual human must send them, not a stand-in."""
    exchanges = [
        _exchange(1),
        _exchange(2, sender=HUMAN_ROLE_B),
        _exchange(3, sender=HUMAN_ROLE_B),
    ]
    result = validate_session(
        tmp_path, _write_session(tmp_path, interaction_event_ids=exchanges)
    )
    assert result["passed"] is False
    assert any("answered requests from the actual human" in p for p in result["problems"])


def test_an_unanswered_request_does_not_count(tmp_path):
    exchanges = [_exchange(1), _exchange(2), _exchange(3, answered=False)]
    result = validate_session(
        tmp_path, _write_session(tmp_path, interaction_event_ids=exchanges)
    )
    assert result["passed"] is False


def test_missing_membership_fails(tmp_path):
    manifest = _write_session(
        tmp_path,
        participants={"actual_human": ACTUAL_HUMAN, "llm_agent": LLM_AGENT},
        three_party_topology_confirmed=False,
    )
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("participants" in p for p in result["problems"])
    assert any("three-party topology" in p for p in result["problems"])


def test_one_response_may_not_answer_two_requests(tmp_path):
    exchanges = [_exchange(1), _exchange(2), _exchange(3)]
    exchanges[2]["response_event_id"] = exchanges[1]["response_event_id"]
    result = validate_session(
        tmp_path, _write_session(tmp_path, interaction_event_ids=exchanges)
    )
    assert result["passed"] is False
    assert any("more than one request" in p for p in result["problems"])


def test_unconfirmed_visibility_fails(tmp_path):
    manifest = _write_session(
        tmp_path, extra={"human_confirmed_responses_visible": False}
    )
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("visible" in p for p in result["problems"])


def test_publication_data_must_be_false_for_development(tmp_path):
    result = validate_session(tmp_path, _write_session(tmp_path, publication_data=True))
    assert result["passed"] is False
    assert any("publication_data" in p for p in result["problems"])


# ---------------------------------------------------------------- evidence


def test_a_tampered_transcript_is_detected(tmp_path):
    manifest = _write_session(tmp_path)
    transcript = tmp_path / manifest["evidence_artifacts"][0]["path"]
    transcript.write_text('{"exchanges": [], "room_id": "!other:hs-b.test"}', encoding="utf-8")
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("digest mismatch" in p for p in result["problems"])


def test_missing_evidence_is_detected(tmp_path):
    manifest = _write_session(tmp_path)
    (tmp_path / manifest["evidence_artifacts"][0]["path"]).unlink()
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("evidence missing" in p for p in result["problems"])


def test_a_transcript_that_omits_an_interaction_is_detected(tmp_path):
    """The transcript must corroborate the manifest, not merely exist."""
    manifest = _write_session(tmp_path)
    transcript_path = tmp_path / manifest["evidence_artifacts"][0]["path"]
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    transcript["exchanges"] = transcript["exchanges"][:1]
    transcript_path.write_text(json.dumps(transcript), encoding="utf-8")
    # Re-point the digest so only the omission, not tampering, is under test.
    from fam.common.digests import file_sha256

    manifest["evidence_artifacts"][0]["sha256"] = file_sha256(transcript_path)
    result = validate_session(tmp_path, manifest)
    assert any("not all in the transcript" in p for p in result["problems"])


def test_a_leaked_credential_in_an_artifact_is_detected(tmp_path):
    manifest = _write_session(tmp_path)
    manifest["llm_model"] = "sk-ant-abcdefghijklmnopqrstuvwxyz012345"
    result = validate_session(tmp_path, manifest)
    assert result["passed"] is False
    assert any("secret material" in p for p in result["problems"])


def test_an_invalid_session_is_reported_not_counted(tmp_path):
    manifest = _write_session(
        tmp_path,
        validity=invalid(
            InvalidRunClass.EXTERNAL_DEPENDENCY_OR_CLIENT_ENVIRONMENT_FAILURE,
            "provider outage before execution",
        ),
    )
    result = validate_session(tmp_path, manifest)
    assert result["valid"] is False
    assert result["invalid_class"] == "external_dependency_or_client_environment_failure"


# ------------------------------------------------- every entry point parses


def test_every_script_and_experiment_parses():
    """A syntax error in an entry point must not reach the operator.

    Nothing imports the experiment runners — they execute inside the toolbox
    container — so a broken one is invisible to the rest of the suite and
    surfaces only when someone tries to run it. Compiling them here is cheap
    and closes that gap.
    """
    import py_compile
    import tempfile

    root = Path(__file__).resolve().parents[1]
    targets = sorted(
        list((root / "experiments").glob("*.py"))
        + list((root / "scripts").glob("*.py"))
        + list((root / "src").rglob("*.py"))
    )
    assert targets, "no entry points found to check"

    broken: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for path in targets:
            try:
                py_compile.compile(
                    str(path),
                    cfile=str(Path(tmp) / (path.stem + ".pyc")),
                    doraise=True,
                )
            except py_compile.PyCompileError as exc:
                broken.append(f"{path.relative_to(root)}: {exc.msg.strip()}")
    assert not broken, "entry points with syntax errors:\n" + "\n".join(broken)
