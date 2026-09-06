"""Pre-lock remediation: the checks that close the audit findings.

Each test names the finding it closes. They exist because every one of these
defects was silent — the pipeline produced plausible output while a parameter,
a classification or a gate was wrong.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fam.analysis.integrity import (
    COMPARABLE,
    MANIFEST_ONLY,
    check_run,
    verify_campaign,
)
from fam.common.validity import EXCLUDED_FROM_FAILURE_RATE, failure_rate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from e4_validate import scan_secrets  # noqa: E402

SECOND = 1_000_000_000


# =============================================================== H1


def _manifest(**over):
    base = {
        "run_id": "r1",
        "experiment": "E3",
        "topology": "local",
        "workload_type": "throughput",
        "paired_block_id": "thr-c08-block01",
        "concurrency": 8,
        "room_id": "!room:hs-a.test",
        "message_body_bytes": 256,
        "window_start_ns": 1000 * SECOND,
        "window_end_ns": 1060 * SECOND,
    }
    base.update(over)
    return base


def _record(**over):
    base = {
        "run_id": "r1",
        "experiment": "E3",
        "topology": "local",
        "workload": "throughput",
        "block_id": "thr-c08-block01",
        "concurrency": 8,
        "room_id": "!room:hs-a.test",
        "message_body_bytes": 256,
        "window_start_ns": 1000 * SECOND,
        "window_end_ns": 1060 * SECOND,
        "initiated_monotonic_ns": 1000 * SECOND,
        "completed_monotonic_ns": 1001 * SECOND,
        "outcome": "success",
        "phase": "window",
    }
    base.update(over)
    return base


def test_h1_consistent_run_passes():
    problems, compared, records = check_run(_manifest(), [_record(), _record()])
    assert problems == []
    assert compared == len(COMPARABLE)
    assert records == 2


@pytest.mark.parametrize(
    "manifest_field,raw_field,bad",
    [
        ("window_start_ns", "window_start_ns", 999 * SECOND),
        ("window_end_ns", "window_end_ns", 1059 * SECOND),
        ("concurrency", "concurrency", 32),
        ("topology", "topology", "federated"),
        ("paired_block_id", "block_id", "thr-c08-block99"),
        ("workload_type", "workload", "latency"),
        ("run_id", "run_id", "someone-elses-run"),
        ("room_id", "room_id", "!other:hs-a.test"),
        ("message_body_bytes", "message_body_bytes", 128),
    ],
)
def test_h1_detects_every_injected_mismatch(manifest_field, raw_field, bad):
    """A manifest that disagrees with its own raw stream must fail."""
    problems, _, _ = check_run(_manifest(), [_record(**{raw_field: bad})])
    assert problems, f"{manifest_field} mismatch went undetected"
    assert any(manifest_field in p for p in problems)
    assert any("r1" in p for p in problems), "the failing run must be named"


def test_h1_neither_source_is_silently_preferred():
    """The message must state both values, not resolve the disagreement."""
    problems, _, _ = check_run(_manifest(), [_record(window_end_ns=1059 * SECOND)])
    text = " ".join(problems)
    assert "manifest" in text and "raw" in text
    assert str(1060 * SECOND) in text and str(1059 * SECOND) in text


def test_h1_rejects_a_window_of_the_wrong_duration():
    """Both sides can agree and still be wrong: the duration is frozen."""
    manifest = _manifest(window_end_ns=1030 * SECOND)
    record = _record(window_end_ns=1030 * SECOND)
    problems, _, _ = check_run(manifest, [record])
    assert any("frozen duration" in p for p in problems)


def test_h1_rejects_an_inverted_window():
    manifest = _manifest(window_start_ns=1060 * SECOND, window_end_ns=1000 * SECOND)
    record = _record(window_start_ns=1060 * SECOND, window_end_ns=1000 * SECOND)
    problems, _, _ = check_run(manifest, [record])
    assert problems


def test_h1_latency_runs_are_not_faulted_for_having_no_window():
    manifest = _manifest(workload_type="latency", window_start_ns=None, window_end_ns=None)
    record = _record(workload="latency", window_start_ns=None, window_end_ns=None,
                     phase="measured")
    problems, _, _ = check_run(manifest, [record])
    assert problems == []


def test_h1_declares_what_it_cannot_verify():
    """Overstating coverage would be worse than the gap itself."""
    assert "validity_classification" in MANIFEST_ONLY
    assert "publication_data" in MANIFEST_ONLY
    for field in MANIFEST_ONLY:
        assert field not in COMPARABLE


def test_h1_missing_raw_stream_is_a_mismatch(tmp_path):
    (tmp_path / "manifests").mkdir()
    manifest = _manifest()
    manifest["raw_artifacts"] = [
        {"role": "runner_interaction_stream", "path": "raw/gone.jsonl", "sha256": "0"*64}
    ]
    report = verify_campaign(tmp_path, [manifest])
    assert not report.ok
    assert any("missing" in m for m in report.mismatches)


def test_h1_manifest_without_a_raw_stream_is_a_mismatch(tmp_path):
    report = verify_campaign(tmp_path, [_manifest()])
    assert not report.ok
    assert any("no runner interaction stream" in m for m in report.mismatches)


# =============================================================== L11


def test_l11_offline_send_leaves_the_denominator():
    """§12: an offline send is not yet a logical interaction."""
    assert "offline_send" in EXCLUDED_FROM_FAILURE_RATE
    # 2 success, 1 timeout, 1 offline_send -> 1 failure of 3 counted
    assert failure_rate(["success", "success", "timeout", "offline_send"]) == pytest.approx(1 / 3)


def test_l11_all_offline_sends_yield_no_rate():
    """E2 would otherwise report total failure by construction."""
    assert failure_rate(["offline_send"] * 100) is None


def test_l11_empty_is_not_zero():
    assert failure_rate([]) is None


def test_l11_every_non_success_counts_as_failure():
    for outcome in ("timeout", "send_error", "malformed_response",
                    "unexpected_response", "runner_error"):
        assert failure_rate(["success", outcome]) == pytest.approx(0.5), outcome


# =============================================================== M5


@pytest.mark.parametrize(
    "prose",
    [
        "authorization",
        "x-api-key",
        "sk-example",
        "Please explain the authorization model",
        "$AhXhlohAY2bINAPlYAcYipRbsk-HtTFayNxCRBfDqSQ",
        "What does Bearer authentication mean?",
    ],
)
def test_m5_harmless_prose_does_not_fail_a_session(prose):
    """A person writing a credential-shaped word has leaked nothing."""
    assert scan_secrets({"request_text": prose, "response_text": prose}) == []


@pytest.mark.parametrize(
    "token",
    [
        "sk-ant-api03-" + "a" * 40,
        "sk-or-v1-" + "b" * 48,
        "sk-proj-" + "c" * 30,
        "ghp_" + "d" * 36,
    ],
)
def test_m5_real_credential_formats_are_caught_even_in_prose(token):
    assert scan_secrets({"request_text": f"my key is {token}"})


@pytest.mark.parametrize("key", ["authorization", "x-api-key", "api_key",
                                 "FAM_LLM_API_KEY", "access_token", "password"])
def test_m5_credential_bearing_field_names_are_rejected(key):
    """The name alone disqualifies a structured field, whatever it holds."""
    assert scan_secrets({"provider_request": {key: "redacted"}})


def test_m5_matrix_event_ids_are_not_credentials():
    """Event ids are random base64 and contain 'sk-' about once in 7000."""
    document = {
        "interaction_event_ids": [
            {"request_event_id": "$AhXhlohAY2bINAPlYAcYipRbsk-HtTFayNxCRBfDqSQ",
             "response_event_id": "$V8PuNGsK8A-B3Y_d8aKh4hHo-RkWA7tclB8i6ZFTkdE"}
        ]
    }
    assert scan_secrets(document) == []


def test_m5_ordinary_manifest_content_is_clean():
    assert scan_secrets({
        "llm_provider": "openai_compatible",
        "llm_model": "~anthropic/claude-haiku-latest",
        "agent_configuration_hash": "6a" * 32,
    }) == []


# =============================================================== M9


def _schema():
    root = Path(__file__).resolve().parents[1]
    return json.loads(
        (root / "results/schemas/run-manifest.schema.json").read_text(encoding="utf-8")
    )


def _validator():
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(_schema())


def _automated(**over):
    base = {
        "manifest_type": "automated_experiment_manifest",
        "manifest_schema_version": "1",
        "experiment": "E3",
        "execution_protocol_version": "1.1-dev",
        "execution_analysis_spec_version": "1.1-dev",
        "protocol_git_commit": "abc123",
        "raw_schema_version": "1",
        "publication_data": False,
        "run_id": "r1",
        "room_id": "!r:hs-a.test",
        "room_version": "12",
        "participants": {"a": "@x:hs-a.test"},
        "execution_host_identifier": "host",
        "start_timestamp": "2026-09-05T00:00:00Z",
        "completion_status": "complete",
        "validity_classification": {"valid": True, "invalid_class": None, "note": ""},
        "topology": "local",
        "raw_artifacts": [
            {"role": "runner_interaction_stream", "path": "raw/e3/x.jsonl",
             "sha256": "0" * 64, "bytes": 10}
        ],
    }
    base.update(over)
    return base


def test_m9_automated_manifest_without_raw_artifacts_is_invalid():
    validator = _validator()
    bad = _automated()
    del bad["raw_artifacts"]
    assert list(validator.iter_errors(bad)), "a run that recorded nothing must not validate"


def test_m9_automated_manifest_with_empty_raw_artifacts_is_invalid():
    validator = _validator()
    assert list(validator.iter_errors(_automated(raw_artifacts=[])))


def test_m9_raw_artifact_without_a_digest_is_invalid():
    validator = _validator()
    bad = _automated(raw_artifacts=[{"role": "runner", "path": "raw/x.jsonl"}])
    assert list(validator.iter_errors(bad))


def test_m9_a_well_formed_automated_manifest_still_validates():
    validator = _validator()
    assert list(validator.iter_errors(_automated())) == []


def test_m9_e4_manifest_is_not_forced_to_carry_raw_artifacts():
    """E4 legitimately uses evidence_artifacts; §38 keeps the variants apart."""
    validator = _validator()
    e4 = {
        "manifest_type": "human_llm_validation_manifest",
        "manifest_schema_version": "1",
        "experiment": "E4",
        "execution_protocol_version": "1.1-dev",
        "execution_analysis_spec_version": "1.1-dev",
        "protocol_git_commit": "abc",
        "raw_schema_version": "1",
        "publication_data": False,
        "run_id": "e4-1",
        "room_id": "!r:hs-b.test",
        "room_version": "12",
        "participants": {"a": "@actual-human:hs-a.test"},
        "execution_host_identifier": "host",
        "start_timestamp": "2026-09-05T00:00:00Z",
        "completion_status": "pass",
        "validity_classification": {"valid": True, "invalid_class": None, "note": ""},
        "human_client_name": "Element",
        "human_client_version": "1.12.27",
        "human_client_host": "ws",
        "llm_provider": "openai_compatible",
        "llm_model": "m",
        "agent_configuration_hash": "0" * 64,
        "executor_identifier": "llm",
        "interaction_event_ids": [],
        "evidence_artifacts": [
            {"role": "transcript", "path": "evidence/t.json", "sha256": "0" * 64}
        ],
        "functional_result": "pass",
        "three_party_topology_confirmed": True,
    }
    assert list(validator.iter_errors(e4)) == []
