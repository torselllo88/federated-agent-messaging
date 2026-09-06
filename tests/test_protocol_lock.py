"""Task 07 pre-lock corrections.

Two independent things are checked here. First, that a manifest's wall-clock
span describes the run rather than the instant the manifest was assembled --
the defect that produced 497 zero-length and 4 negative-length spans across the
development dataset. Second, that the protocol lock is load-bearing: a
publication run whose effective parameters differ from the lock must stop, not
warn.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fam.common.frozen import (
    E2_OFFLINE_REQUESTS,
    E2_SYNC_TIMELINE_LIMIT,
    E3_BOOTSTRAP_REPLICATES,
    E3_BOOTSTRAP_SEED,
    E3_SCHEDULE_SEED,
    EXECUTION_ANALYSIS_SPEC_VERSION,
    EXECUTION_PROTOCOL_VERSION,
    RAW_SCHEMA_VERSION,
)
from fam.common.lock import (
    LOCK_ARTIFACT,
    ProtocolLockError,
    compare,
    enforce,
    frozen_parameters,
)
from fam.common.validity import VALID
from fam.instrumentation.manifest import RunManifest

# --------------------------------------------------------------- versioning


def test_the_lock_carries_final_versions_not_development_markers():
    """A campaign executing under `1.2-dev` would record a version that the
    frozen document set does not define."""
    assert EXECUTION_PROTOCOL_VERSION == "1.2"
    assert EXECUTION_ANALYSIS_SPEC_VERSION == "1.2"
    assert RAW_SCHEMA_VERSION == "2"
    for value in (EXECUTION_PROTOCOL_VERSION, EXECUTION_ANALYSIS_SPEC_VERSION):
        assert "dev" not in value


def test_one_definition_per_frozen_parameter():
    """frozen.py owns the value; the analysis imports it rather than restating.

    The two used to disagree: frozen.py said ten thousand replicates and the
    analysis used two thousand, so the lock would have recorded a number the
    code never applied.
    """
    from fam.analysis import e3

    assert e3.DEFAULT_REPLICATES is E3_BOOTSTRAP_REPLICATES
    assert e3.DEFAULT_BOOTSTRAP_SEED is E3_BOOTSTRAP_SEED
    assert E3_BOOTSTRAP_REPLICATES == 2000
    assert E3_BOOTSTRAP_SEED == E3_SCHEDULE_SEED == 20260905


def test_e2_parameters_are_frozen_centrally():
    assert E2_OFFLINE_REQUESTS == 100
    assert E2_SYNC_TIMELINE_LIMIT == 10
    assert E2_SYNC_TIMELINE_LIMIT < E2_OFFLINE_REQUESTS, (
        "a limit at or above the offline count lets the post-restart sync "
        "return everything and never enters the recovery branch under test"
    )


# ------------------------------------------------------- manifest timestamps


def _manifest(**overrides):
    defaults = dict(
        experiment="E0",
        run_id="e0-test-01",
        room_id="!r:hs-a.test",
        participants={"human_a": "@human-a:hs-a.test"},
        topology="same-domain",
        publication_data=False,
        protocol_git_commit="deadbeef",
        started_at="2026-09-06T12:00:00Z",
        completed_at="2026-09-06T12:02:11Z",
        completion_status="pass",
        validity=VALID,
    )
    defaults.update(overrides)
    return RunManifest(**defaults)


def test_started_at_has_no_default_so_it_cannot_be_manufactured():
    """The regression itself.

    `started_at` used to be `field(default_factory=utc_now)`, which fires when
    the dataclass is constructed -- and every caller constructs the manifest
    after the run has finished. Making the field required forces each entry
    point to observe the real start.
    """
    import dataclasses

    field = {f.name: f for f in dataclasses.fields(RunManifest)}["started_at"]
    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING

    with pytest.raises(TypeError):
        RunManifest(  # type: ignore[call-arg]
            experiment="E0",
            run_id="e0-test-01",
            room_id="!r:hs-a.test",
            participants={},
            topology="same-domain",
            publication_data=False,
            protocol_git_commit="deadbeef",
        )


def test_a_manifest_records_the_span_it_was_given():
    payload = _manifest().to_dict()
    assert payload["start_timestamp"] == "2026-09-06T12:00:00Z"
    assert payload["completion_timestamp"] == "2026-09-06T12:02:11Z"
    assert payload["start_timestamp"] < payload["completion_timestamp"]


def test_a_backwards_span_is_refused_rather_than_written():
    """Four development manifests completed one second before they started."""
    with pytest.raises(ValueError, match="precedes"):
        _manifest(
            started_at="2026-09-06T12:00:01Z", completed_at="2026-09-06T12:00:00Z"
        ).to_dict()


def test_an_equal_span_is_allowed():
    """Whole-second stamps, so a genuinely brief run may start and finish
    inside the same second. Equality is not evidence of the old defect."""
    payload = _manifest(
        started_at="2026-09-06T12:00:00Z", completed_at="2026-09-06T12:00:00Z"
    ).to_dict()
    assert payload["start_timestamp"] == payload["completion_timestamp"]


def test_the_e3_manifest_path_actually_runs():
    """Exercises the path rather than grepping it.

    An earlier version of this test only checked that the string `started_at=`
    appeared in each source file. It passed while `_write_evidence` referenced
    a name that existed solely in its caller's scope -- which compiles, because
    Python resolves globals at call time, and fails on the first real run. The
    fix was to carry the stamp on the result object; this test is what would
    have caught the original.
    """
    pytest.importorskip("nio", reason="the benchmark runner needs matrix-nio")

    from fam.benchmark.engine import RunConfig, WorkloadResult
    from fam.benchmark.runner import BenchmarkRun, _write_evidence
    from fam.benchmark.schedule import ScheduledRun

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw = root / "raw" / "e3" / "latency"
        raw.mkdir(parents=True)

        scheduled = ScheduledRun(
            workload="latency",
            block_id="lat-block01",
            block_index=1,
            within_block_order=1,
            topology="local",
            concurrency=1,
        )
        run = BenchmarkRun(
            scheduled=scheduled,
            run_id="e3-test-01",
            started_at="2026-09-06T12:00:00Z",
            room_id="!r:hs-a.test",
        )
        run.runner_stream = raw / "e3-test-01.runner.jsonl"
        run.agent_stream = raw / "e3-test-01.agent.jsonl"
        run.workload_result = WorkloadResult()
        run.validity = VALID
        run.completion_status = "complete"

        _write_evidence(
            run,
            root=root,
            campaign_id="fam-formal-0123456789abcdef",
            campaign_fingerprint="f" * 64,
            schedule_seed=E3_SCHEDULE_SEED,
            rate_limit_reference={},
            environment_manifest="environment/environment-latest.json",
            sync_timeline_limit=500,
            sync_timeout_ms=30_000,
            config=RunConfig(
                run_id="e3-test-01",
                workload="latency",
                block_id="lat-block01",
                within_block_order=1,
                topology_name="local",
                receiver_role="agent",
                sender="@benchmark-human:hs-a.test",
                room_id="!r:hs-a.test",
                concurrency=1,
            ),
        )

        written = list((root / "manifests").glob("*.json"))
        assert len(written) == 1, "the run wrote no manifest"
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        assert payload["start_timestamp"] == "2026-09-06T12:00:00Z"
        assert payload["completion_timestamp"] >= payload["start_timestamp"]


# ---------------------------------------------------------------- the lock


FORMAL_HOST = "fam-formal-host"


def _lock_document(**overrides):
    document = {
        "artifact": LOCK_ARTIFACT,
        "lock_schema_version": "1",
        "implementation": {"git_commit": "abc123", "git_tag": "protocol-v1.2"},
        "frozen_parameters": frozen_parameters(),
        "environment": {"formal_run_host_identifier": FORMAL_HOST},
        "campaign": {"campaign_id": "fam-formal-0123456789abcdef"},
        "e4": {"llm_model": "anthropic/claude-haiku-4.5"},
    }
    document.update(overrides)
    return document


def _on_the_formal_host(monkeypatch, path):
    """The preconditions a real formal run arrives with: the right host, a
    clean tree, and the lock in place."""
    monkeypatch.setenv("FAM_PROTOCOL_LOCK", str(path))
    monkeypatch.setenv("FAM_EXECUTION_HOST", FORMAL_HOST)
    monkeypatch.setenv("FAM_WORKTREE_STATUS", "")


def test_the_lock_describes_the_code_it_was_generated_from():
    assert compare(_lock_document()) == []


def test_a_changed_frozen_parameter_is_reported():
    document = _lock_document()
    document["frozen_parameters"]["e3_measurement_seconds"] = 30.0
    problems = compare(document)
    assert len(problems) == 1
    assert "e3_measurement_seconds" in problems[0]


def test_a_parameter_the_lock_never_pinned_is_reported():
    document = _lock_document()
    del document["frozen_parameters"]["e3_paired_blocks"]
    assert any("e3_paired_blocks" in p for p in compare(document))


def test_a_development_run_needs_no_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("FAM_PROTOCOL_LOCK", str(tmp_path / "absent.json"))
    assert enforce(publication_data=False, experiment="E0") is None


def test_a_publication_run_without_a_lock_stops(monkeypatch, tmp_path):
    monkeypatch.setenv("FAM_PROTOCOL_LOCK", str(tmp_path / "absent.json"))
    with pytest.raises(ProtocolLockError, match="no protocol lock"):
        enforce(publication_data=True, experiment="E0")


def test_a_publication_run_matching_its_lock_proceeds(monkeypatch, tmp_path):
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    document = enforce(publication_data=True, experiment="E3")
    assert document is not None
    assert document["campaign"]["campaign_id"].startswith("fam-formal-")


def test_a_publication_run_against_a_stale_lock_stops(monkeypatch, tmp_path):
    document = _lock_document()
    document["frozen_parameters"]["interaction_timeout_seconds"] = 30.0
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    with pytest.raises(ProtocolLockError, match="interaction_timeout_seconds"):
        enforce(publication_data=True, experiment="E3")


def test_an_environment_override_during_a_publication_run_stops(monkeypatch, tmp_path):
    """The knobs exist for pilots. During formal collection, setting one means
    the run would have executed under a configuration the lock does not
    describe -- a §66 stop condition, not a warning."""
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.setenv("FAM_E2_TIMELINE_LIMIT", "40")
    with pytest.raises(ProtocolLockError, match="environment overrides"):
        enforce(publication_data=True, experiment="E2")


def test_a_file_that_is_not_a_lock_is_refused(monkeypatch, tmp_path):
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps({"artifact": "something_else"}), encoding="utf-8")
    monkeypatch.setenv("FAM_PROTOCOL_LOCK", str(path))
    with pytest.raises(ProtocolLockError, match="not a protocol lock"):
        enforce(publication_data=True, experiment="E0")


# ------------------------------------------ formal-run preconditions (§23)


def test_a_publication_run_on_the_wrong_host_stops(monkeypatch, tmp_path):
    """Otherwise the run's environment manifest would describe a machine the
    data was never collected on."""
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.setenv("FAM_EXECUTION_HOST", "somebody-elses-laptop")
    with pytest.raises(ProtocolLockError, match="not the locked formal host"):
        enforce(publication_data=True, experiment="E3")


def test_a_publication_run_that_cannot_identify_its_host_stops(monkeypatch, tmp_path):
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.delenv("FAM_EXECUTION_HOST")
    with pytest.raises(ProtocolLockError, match="FAM_EXECUTION_HOST is unset"):
        enforce(publication_data=True, experiment="E3")


def test_a_publication_run_from_a_dirty_worktree_stops(monkeypatch, tmp_path):
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.setenv("FAM_WORKTREE_STATUS", " M src/fam/common/frozen.py")
    with pytest.raises(ProtocolLockError, match="worktree is not clean"):
        enforce(publication_data=True, experiment="E3")


def test_an_unchecked_worktree_is_not_treated_as_a_clean_one(monkeypatch, tmp_path):
    """The container has no .git, so an absent status means nobody looked.
    Reading that as 'clean' is the precise mistake this check exists to stop."""
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.delenv("FAM_WORKTREE_STATUS")
    with pytest.raises(ProtocolLockError, match="FAM_WORKTREE_STATUS is unset"):
        enforce(publication_data=True, experiment="E3")


def test_the_preconditions_do_not_apply_to_a_development_run(monkeypatch, tmp_path):
    """A pilot on a laptop with a dirty tree is ordinary development work."""
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    monkeypatch.setenv("FAM_PROTOCOL_LOCK", str(path))
    monkeypatch.setenv("FAM_EXECUTION_HOST", "a-laptop")
    monkeypatch.setenv("FAM_WORKTREE_STATUS", " M everything.py")
    monkeypatch.setenv("FAM_E2_TIMELINE_LIMIT", "40")
    assert enforce(publication_data=False, experiment="E2") is not None


# ------------------------------------------------------------- campaign ids


def test_formal_and_development_campaigns_cannot_share_an_identifier():
    from fam.benchmark.schedule import campaign_id, campaign_parameters

    parameters = campaign_parameters(
        seed=E3_SCHEDULE_SEED, sync_timeline_limit=500, sync_timeout_ms=30_000
    )
    development = campaign_id(parameters, publication_data=False)
    formal = campaign_id(parameters, publication_data=True)

    assert development.startswith("e3dev-")
    assert formal.startswith("fam-formal-")
    assert development != formal
    # Same parameters, so the fingerprint half is identical: only the prefix
    # distinguishes them, which is what makes the distinction unmissable.
    assert development.split("-", 1)[1] == formal.split("-", 2)[2]


# ------------------------------------------------------------ lock contents


def test_the_lock_pins_every_parameter_46_requires():
    """§46 names what the lock must contain. Absence is silent, so it is
    checked here rather than trusted to review."""
    pinned = frozen_parameters()
    required = {
        "protocol_version",
        "analysis_spec_version",
        "raw_schema_version",
        "manifest_schema_version",
        "room_version",
        "interaction_timeout_seconds",
        "message_body_bytes",
        "message_padding_character",
        "e0_runs",
        "e1_runs",
        "e1_quiet_interval_seconds",
        "e2_runs",
        "e2_offline_requests",
        "e2_sync_timeline_limit",
        "e3_latency_warmup_interactions",
        "e3_latency_measured_interactions",
        "e3_concurrency_levels",
        "e3_warmup_seconds",
        "e3_measurement_seconds",
        "e3_drain_seconds",
        "e3_paired_blocks",
        "e3_inter_run_idle_seconds",
        "e3_sync_timeline_limit",
        "e3_sync_timeout_ms",
        "e3_schedule_seed",
        "e3_bootstrap_seed",
    }
    missing = required - set(pinned)
    assert not missing, f"the lock does not pin: {sorted(missing)}"


# ------------------------------------------- lock artifact vs tagged commit


def _lock_script():
    """Load scripts/protocol_lock.py by path.

    `scripts/` is not a package, and the file is a command-line entry point
    rather than an importable module, so it is loaded the way the operator
    invokes it: by location.
    """
    import importlib.util

    for candidate in (Path("scripts/protocol_lock.py"),
                      Path("/app/scripts/protocol_lock.py")):
        if candidate.exists():
            spec = importlib.util.spec_from_file_location(
                "fam_protocol_lock_script", candidate
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.skip("protocol_lock.py is not on disk here")


def _drift(monkeypatch, diff, locked="a" * 40, head="b" * 40):
    """The validator's view when git is unavailable, as in the container."""
    lock_script = _lock_script()

    monkeypatch.setattr(lock_script, "_git", lambda *a: "")
    if diff is None:
        monkeypatch.delenv("FAM_IMPLEMENTATION_DIFF", raising=False)
    else:
        monkeypatch.setenv("FAM_IMPLEMENTATION_DIFF", diff)
    return lock_script._implementation_drift(locked, head)


def test_the_tagging_commit_may_add_the_lock_and_nothing_else(monkeypatch):
    """A lock cannot name the commit that carries it: the artifact has to exist
    before it can be committed. So the tag sits one commit ahead of the
    implementation the lock names, and that gap must be the lock alone."""
    assert _drift(monkeypatch, "results/protocol-lock.json") == []


def test_code_changing_between_generation_and_tagging_is_caught(monkeypatch):
    problems = _drift(
        monkeypatch, "results/protocol-lock.json\nsrc/fam/common/frozen.py"
    )
    assert len(problems) == 1
    assert "frozen.py" in problems[0]
    assert "more than the lock artifact" in problems[0]


def test_a_gap_that_is_not_the_lock_is_caught(monkeypatch):
    problems = _drift(monkeypatch, "docs/experimental-protocol.md")
    assert problems and "more than the lock artifact" in problems[0]


def test_an_unexamined_gap_is_not_assumed_harmless(monkeypatch):
    """Same rule as the worktree status: an absent answer means nobody looked,
    and the validator must not read that as agreement."""
    problems = _drift(monkeypatch, None)
    assert problems and "never examined" in problems[0]


# ------------------------------------------- pre-flight trace remediation


def test_the_schedule_seed_has_one_definition():
    """It had two: frozen.py declared the value the lock reports, and
    schedule.py held the value E3 actually read. They agreed by coincidence."""
    from fam.benchmark import schedule

    assert schedule.DEFAULT_SCHEDULE_SEED is E3_SCHEDULE_SEED


def test_the_agent_settle_time_is_a_locked_parameter():
    """It governs every E3 run and was pinned only by the commit."""
    from fam.common.frozen import E3_AGENT_SETTLE_SECONDS

    assert frozen_parameters()["e3_agent_settle_seconds"] == E3_AGENT_SETTLE_SECONDS


def test_a_changed_settle_time_stops_a_publication_run(monkeypatch, tmp_path):
    document = _lock_document()
    document["frozen_parameters"]["e3_agent_settle_seconds"] = 30.0
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    with pytest.raises(ProtocolLockError, match="e3_agent_settle_seconds"):
        enforce(publication_data=True, experiment="E3")


@pytest.mark.parametrize(
    "variable",
    [
        "FAM_E3_SCHEDULE_SEED",
        "FAM_E3_BOOTSTRAP_SEED",
        "FAM_LLM_MAX_TOKENS",
        "FAM_LLM_SYSTEM_PROMPT",
    ],
)
def test_the_watchlist_covers_what_silently_changes_execution(
    monkeypatch, tmp_path, variable
):
    """The schedule seed was the omission the pre-flight trace found: with it
    set, the gate passed while block one flipped from local-first to
    federated-first and the lock went on naming seed 20260905."""
    path = tmp_path / "protocol-lock.json"
    path.write_text(json.dumps(_lock_document()), encoding="utf-8")
    _on_the_formal_host(monkeypatch, path)
    monkeypatch.setenv(variable, "1")
    with pytest.raises(ProtocolLockError, match="environment overrides"):
        enforce(publication_data=True, experiment="E3")


# ------------------------------------------------- E4 executor configuration


def test_the_locked_executor_configuration_admits_a_matching_session():
    from fam.common.lock import enforce_llm_configuration

    document = _lock_document()
    document["e4"]["agent_config_hash"] = "c" * 64
    enforce_llm_configuration(
        document, config_hash="c" * 64, publication_data=True
    )


def test_a_different_executor_configuration_stops_the_session():
    """The model slug can be right while the request reaching it is not: a
    redirected base URL, a widened token budget, an edited system prompt."""
    from fam.common.lock import enforce_llm_configuration

    document = _lock_document()
    document["e4"]["agent_config_hash"] = "c" * 64
    with pytest.raises(ProtocolLockError, match="not the locked configuration"):
        enforce_llm_configuration(
            document, config_hash="d" * 64, publication_data=True
        )


def test_a_lock_without_an_executor_hash_cannot_admit_a_formal_session():
    from fam.common.lock import enforce_llm_configuration

    document = _lock_document()
    document["e4"].pop("agent_config_hash", None)
    with pytest.raises(ProtocolLockError, match="records no agent_config_hash"):
        enforce_llm_configuration(
            document, config_hash="c" * 64, publication_data=True
        )


def test_a_development_session_is_not_held_to_the_locked_executor():
    from fam.common.lock import enforce_llm_configuration

    enforce_llm_configuration(None, config_hash="c" * 64, publication_data=False)
