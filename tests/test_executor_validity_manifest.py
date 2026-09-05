"""Deterministic executor, validity taxonomy, privilege evidence, manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fam.common.frozen import E3_BODY_BYTES, MANIFEST_SCHEMA_VERSION, ROOM_VERSION
from fam.common.message import Correlation, build_ack, build_request, parse
from fam.common.privilege import summarize
from fam.common.validity import (
    VALID,
    InvalidRunClass,
    RunValidity,
    invalid,
)
from fam.executors.deterministic import DeterministicExecutor
from fam.executors.base import ExecutionRequest
from fam.instrumentation.manifest import AUTOMATED, RawArtifact, RunManifest


# ------------------------------------------------------------------ executor


def _controlled(body: str) -> ExecutionRequest:
    """The controlled protocol hands the executor the parsed envelope."""
    return ExecutionRequest(text=body, message=parse(body))


def test_one_deterministic_ack_per_valid_request():
    executor = DeterministicExecutor()
    correlation = Correlation("E0", "run-1", 5)
    request = _controlled(build_request(correlation))
    first = executor.decide(request)
    second = executor.decide(request)
    assert first == second == build_ack(correlation)


def test_executor_ignores_acks():
    executor = DeterministicExecutor()
    ack = _controlled(build_ack(Correlation("E0", "run-1", 5)))
    assert executor.decide(ack) is None


def test_executor_ignores_a_request_with_no_controlled_envelope():
    """E4 prose reaching the deterministic executor is not answerable."""
    executor = DeterministicExecutor()
    assert executor.decide(ExecutionRequest(text="what is the capital of France?")) is None


def test_executor_can_produce_the_frozen_fixed_size_body():
    executor = DeterministicExecutor(body_bytes=E3_BODY_BYTES)
    request = _controlled(build_request(Correlation("E3", "run-1", 5)))
    body = executor.decide(request)
    assert len(body.encode("utf-8")) == E3_BODY_BYTES


def test_executor_rejects_a_non_frozen_body_size():
    with pytest.raises(ValueError):
        DeterministicExecutor(body_bytes=512)


# ------------------------------------------------------------------ validity


def test_invalid_run_must_name_exactly_one_class():
    with pytest.raises(ValueError):
        RunValidity(valid=False)
    with pytest.raises(ValueError):
        RunValidity(valid=True, invalid_class=InvalidRunClass.ENVIRONMENT_CORRUPTION)


def test_taxonomy_is_the_frozen_closed_set_of_nine():
    assert len(InvalidRunClass) == 9
    assert {member.value for member in InvalidRunClass} == {
        "protocol_lock_mismatch",
        "execution_precondition_violation",
        "frozen_configuration_error",
        "instrumentation_or_output_failure",
        "runner_implementation_failure",
        "infrastructure_failure",
        "environment_corruption",
        "external_interference",
        "external_dependency_or_client_environment_failure",
    }


def test_manifest_validity_is_an_enum_value_not_free_text():
    payload = invalid(InvalidRunClass.FROZEN_CONFIGURATION_ERROR, "room v11").to_manifest()
    assert payload["invalid_class"] == "frozen_configuration_error"
    assert payload["note"] == "room v11"


# ---------------------------------------------------------------- privilege


def test_privilege_summary_requires_every_component():
    clean = {
        "no_privileged_env_vars": True,
        "privileged_env_vars_found": [],
        "no_server_paths_visible": True,
        "server_paths_found": [],
        "no_signing_keys_visible": True,
        "signing_keys_found": [],
        "no_synapse_mounts": True,
        "synapse_mounts_found": [],
    }
    assert summarize(clean, 403)["c2_supporting_evidence_complete"] is True
    assert summarize(clean, 401)["c2_supporting_evidence_complete"] is True
    # A 404 means the endpoint is absent, which evidences nothing about privilege.
    assert summarize(clean, 404)["c2_supporting_evidence_complete"] is False
    assert summarize(clean, 200)["c2_supporting_evidence_complete"] is False

    leaky = {**clean, "no_synapse_mounts": False, "synapse_mounts_found": ["/synapse/a"]}
    assert summarize(leaky, 403)["c2_supporting_evidence_complete"] is False


# ----------------------------------------------------------------- manifest


def _manifest(tmp_path: Path, stream: Path) -> RunManifest:
    return RunManifest(
        experiment="E0",
        run_id="e0-test-01",
        room_id="!abc:hs-a.test",
        participants={"human_a": "@human-a:hs-a.test"},
        topology="same-domain",
        publication_data=False,
        protocol_git_commit="deadbeef",
        completion_status="pass",
        validity=VALID,
        artifacts=[RawArtifact("runner_interaction_stream", stream)],
        results_root=tmp_path,
    )


def test_manifest_carries_the_frozen_envelope(tmp_path: Path):
    stream = tmp_path / "raw" / "e0" / "e0-test-01.runner.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text('{"a":1}\n', encoding="utf-8")

    payload = _manifest(tmp_path, stream).to_dict()

    for required in (
        "manifest_type",
        "manifest_schema_version",
        "experiment",
        "execution_protocol_version",
        "execution_analysis_spec_version",
        "protocol_git_commit",
        "raw_schema_version",
        "publication_data",
        "run_id",
        "room_id",
        "room_version",
        "participants",
        "environment_manifest",
        "execution_host_identifier",
        "start_timestamp",
        "completion_status",
        "validity_classification",
        "raw_artifacts",
    ):
        assert required in payload, required

    assert payload["manifest_type"] == AUTOMATED
    assert payload["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
    assert payload["room_version"] == ROOM_VERSION
    assert payload["publication_data"] is False


def test_manifest_records_a_relative_path_and_digest(tmp_path: Path):
    stream = tmp_path / "raw" / "e0" / "s.runner.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text("x\n", encoding="utf-8")

    artifact = _manifest(tmp_path, stream).to_dict()["raw_artifacts"][0]
    assert artifact["path"] == "raw/e0/s.runner.jsonl"
    assert len(artifact["sha256"]) == 64


def test_manifest_without_validity_is_refused(tmp_path: Path):
    stream = tmp_path / "s.jsonl"
    stream.write_text("x\n", encoding="utf-8")
    manifest = _manifest(tmp_path, stream)
    manifest.validity = None
    with pytest.raises(ValueError):
        manifest.to_dict()


def test_manifest_is_written_as_readable_json(tmp_path: Path):
    stream = tmp_path / "raw" / "e0" / "s.runner.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text("x\n", encoding="utf-8")

    path = _manifest(tmp_path, stream).write(tmp_path / "manifests")
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "e0-test-01"
