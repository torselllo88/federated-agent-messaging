"""The analysis entry point, run the way a reviewer runs it.

`make analyse` executes inside the toolbox image, where `/app` exists and the
dependencies are installed, so nothing here was ever exercised by it. Off
container the schema directory resolved to a path that does not exist, every
runner record was reported as unvalidatable, and the run stopped with
`FAIL (schema)` before computing anything — a verdict that blamed the data for
a broken lookup. That case is now `FAIL (validation unavailable)`, which is
about the tool rather than the evidence.

These tests run the real entry point from outside the container. Asserting
that `SCHEMA_DIR.exists()` would not have caught it: the old value was a
constant that is perfectly valid in the one environment nobody was testing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from fam.common.results import repository_root, schema_dir

ROOT = Path(__file__).resolve().parents[1]

try:
    import jsonschema  # noqa: F401

    HAVE_JSONSCHEMA = True
except ImportError:
    HAVE_JSONSCHEMA = False

needs_jsonschema = pytest.mark.skipif(
    not HAVE_JSONSCHEMA,
    reason="jsonschema is required by scripts/analyse.py; pip install -r requirements.txt",
)


def _load_analyse():
    """Import scripts/analyse.py, which is an entry point rather than a module."""
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "fam_analyse_entry", ROOT / "scripts" / "analyse.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


analyse = _load_analyse()

SCHEMAS_THE_ANALYSIS_NEEDS = (
    "raw-runner-record.schema.json",
    "raw-runner-record-v1.schema.json",
    "raw-agent-record.schema.json",
    "run-manifest.schema.json",
)


# ------------------------------------------------------------------ location


def test_schemas_resolve_independently_of_the_working_directory(tmp_path, monkeypatch):
    """The regression itself.

    One of the three entry points fell back to ``Path("results/schemas")``,
    which holds only while the process happens to sit in the repository root.
    Resolution comes from the package location instead, so this passes from
    anywhere.
    """
    monkeypatch.chdir(tmp_path)
    resolved = schema_dir()
    assert resolved.is_dir(), resolved
    for name in SCHEMAS_THE_ANALYSIS_NEEDS:
        assert (resolved / name).exists(), f"{name} missing from {resolved}"


def test_repository_root_is_found_from_the_package_not_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = repository_root()
    assert root is not None
    assert (root / "src" / "fam").is_dir()


#: Scripts that only ever run inside a container, with the reason. Their
#: absolute paths are correct rather than exempted: bootstrap needs the
#: Synapse data volumes and the TLS material, and the Makefile reaches it only
#: through the bootstrap service.
CONTAINER_ONLY = {
    "bootstrap.py": "privileged setup; every Makefile target runs it in the bootstrap service",
}

#: Probing for `.git` in both places is the fix, not the defect: it is asking
#: whether this process sits in a checkout, and handles both answers.
GIT_PROBE = re.compile(r"/app/\.git")


def test_no_entry_point_hardcodes_the_container_path_for_tracked_inputs():
    """`/app` is fine for imports and wrong for tracked inputs.

    ``sys.path.insert(0, "/app/src")`` is harmless off container — the path is
    simply absent and the next entry wins. A schema read from ``/app`` has no
    such second chance: it resolves to nothing, and the caller decides what
    that silence means. That is how this failed.

    Asserting ``SCHEMA_DIR.exists()`` would not have caught the original bug,
    because the constant was valid in the one environment being tested. This
    looks for the shape instead.
    """
    offenders = []
    pattern = re.compile(r"""["']/app/(?!src|scripts|experiments|tests)""")
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name in CONTAINER_ONLY:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if pattern.search(line) and not GIT_PROBE.search(line):
                offenders.append(f"{path.name}:{number} {line.strip()}")
    assert not offenders, (
        "these read a tracked input from a container-only path, so they cannot "
        "work off container:\n" + "\n".join(offenders) + "\n\nUse "
        "fam.common.results.schema_dir() or repository_root(). If the script "
        "genuinely only ever runs in a container, add it to CONTAINER_ONLY "
        "with the reason."
    )


# ---------------------------------------------------------------- fail closed


def _tree(tmp_path: Path, *, runner: dict, agent: dict, manifest: dict) -> Path:
    root = tmp_path / "results"
    (root / "raw" / "e0").mkdir(parents=True)
    (root / "manifests").mkdir(parents=True)
    runner_path = root / "raw" / "e0" / "run-01.runner.jsonl"
    runner_path.write_text(json.dumps(runner) + "\n", encoding="utf-8")
    (root / "raw" / "e0" / "run-01.agent.jsonl").write_text(
        json.dumps(agent) + "\n", encoding="utf-8"
    )
    # The manifest's digest has to match what was just written, or the run
    # stops at provenance before it reaches the stage under test.
    for artifact in manifest.get("raw_artifacts", []):
        target = root / artifact["path"]
        if target.exists():
            artifact["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    (root / "manifests" / "run-01.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


def _records(**overrides):
    """A tree the real schemas accept.

    Assembled against the schemas rather than against their top-level
    ``required`` lists, which are the smaller half of what they demand: a
    non-marker runner record picks up ten more fields from a conditional
    branch, and the manifest constrains several values by ``const`` and by
    manifest type. A stand-in validator that checks only the top level calls
    this conforming; the real one does not, which is the difference between
    testing the wiring and testing the validation.
    """
    runner = {
        "schema_version": "2",
        "stream": "runner",
        "experiment": "E0",
        "run_id": "run-01",
        "record_type": "interaction",
        "topology": "local",
        "sequence_id": 1,
        "run_phase": "measured",
        "room_id": "!room:hs-a.test",
        "sender": "@human:hs-a.test",
        "request_txn_id": "fam-test-request-00001",
        "initiated_monotonic_ns": 1_000_000,
        "outcome": "success",
        "ack_count": 1,
        "duplicate_ack_count": 0,
    }
    agent = {
        "schema_version": "2",
        "stream": "agent",
        "experiment": "E0",
        "run_id": "run-01",
        "agent_mxid": "@agent:hs-a.test",
        "action": "responded",
    }
    manifest = {
        "manifest_type": "automated_experiment_manifest",
        "manifest_schema_version": "1",
        "experiment": "E0",
        "execution_protocol_version": "1.2",
        "execution_analysis_spec_version": "1.2",
        "protocol_git_commit": "c0ffee",
        "raw_schema_version": "2",
        "publication_data": False,
        "run_id": "run-01",
        "room_id": "!room:hs-a.test",
        "room_version": "12",
        "participants": {"agent": "@agent:hs-a.test"},
        "execution_host_identifier": "test",
        "start_timestamp": "2026-01-01T00:00:00Z",
        "completion_status": "pass",
        "validity_classification": {"valid": True, "invalid_class": None},
        "topology": "local",
        "raw_artifacts": [
            {
                "role": "runner_interaction_stream",
                "path": "raw/e0/run-01.runner.jsonl",
                "sha256": "0" * 64,
            }
        ],
    }
    runner.update(overrides.get("runner", {}))
    agent.update(overrides.get("agent", {}))
    manifest.update(overrides.get("manifest", {}))
    return runner, agent, manifest


def test_missing_library_is_a_dependency_failure_not_a_data_failure(
    tmp_path, monkeypatch
):
    """"We could not check" must never read as "we checked and it was wrong"."""
    root = _tree(tmp_path, **dict(zip(("runner", "agent", "manifest"), _records())))
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    with pytest.raises(analyse.ValidationUnavailable) as raised:
        analyse.validate_streams(root)

    message = str(raised.value)
    assert "jsonschema" in message
    assert "requirements.txt" in message


def test_missing_schema_file_is_a_path_failure(tmp_path, monkeypatch):
    root = _tree(tmp_path, **dict(zip(("runner", "agent", "manifest"), _records())))
    empty = tmp_path / "no-schemas"
    empty.mkdir()
    monkeypatch.setattr(analyse, "schema_dir", lambda: empty)

    with pytest.raises(analyse.ValidationUnavailable) as raised:
        analyse.validate_streams(root)

    assert "results/schemas" in str(raised.value)


def test_validation_is_attempted_before_any_record_is_read(tmp_path, monkeypatch):
    """The failure is about the tool, so it must not name a data file.

    Constructing validators per record is what produced one complaint per
    stream, 261 lines of them, each pointing at an innocent file.
    """
    runner, agent, manifest = _records(runner={"run_id": None})
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    with pytest.raises(analyse.ValidationUnavailable) as raised:
        analyse.validate_streams(root)

    assert "run-01.runner.jsonl" not in str(raised.value)


# ------------------------------------------------------------- real validation


@needs_jsonschema
def test_conforming_tree_validates(tmp_path):
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)
    ok, problems = analyse.validate_streams(root)
    assert (ok, problems) == (True, [])


@needs_jsonschema
def test_a_nonconforming_record_is_a_finding_against_its_file(tmp_path):
    runner, agent, manifest = _records()
    del runner["run_id"]
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    ok, problems = analyse.validate_streams(root)

    assert not ok
    assert any("run-01.runner.jsonl" in p for p in problems), problems


@needs_jsonschema
def test_manifests_are_validated_rather_than_skipped(tmp_path):
    """Manifest validation used to run only if the library happened to load.

    There was no ``else`` on that branch, so an absent dependency skipped it
    in silence — quieter than the runner failure and easy to leave behind
    while fixing it.
    """
    runner, agent, manifest = _records()
    del manifest["room_id"]
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    ok, problems = analyse.validate_streams(root)

    assert not ok
    assert any("run-01.manifest.json" in p for p in problems), problems


# -------------------------------------------------------------- the verdicts


def _run_main(root: Path, monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setenv("FAM_RESULTS_DIR", str(root))
    code = analyse.main()
    return code, capsys.readouterr().out


@needs_jsonschema
def test_the_label_names_the_stage_and_the_message_names_the_cause(
    tmp_path, monkeypatch, capsys
):
    """Two causes, one verdict, because what is known afterwards is the same.

    An absent library and an absent schema file differ in what to do about
    them and not in what they leave established, which is nothing. Both are
    `validation unavailable`; the message separates them. The verdict this
    must never collide with is `FAIL (schema)`, which asserts the opposite --
    that validation ran and the data failed it.
    """
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    missing = tmp_path / "no-schemas"
    missing.mkdir()
    monkeypatch.setattr(analyse, "schema_dir", lambda: missing)
    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (validation unavailable)" in out
    assert "FAIL (schema)" not in out
    assert "results/schemas" in out


@needs_jsonschema
def test_a_schema_violation_keeps_its_own_verdict(tmp_path, monkeypatch, capsys):
    runner, agent, manifest = _records()
    del runner["outcome"]
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (schema)" in out
    assert "validation unavailable" not in out
    assert "'outcome' is a required property" in out


def test_missing_library_reaches_the_same_verdict(tmp_path, monkeypatch, capsys):
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (validation unavailable)" in out
    assert "requirements.txt" in out


@needs_jsonschema
def test_raw_and_agent_records_are_held_to_the_same_standard(tmp_path):
    """The removed fallback applied to agent streams only.

    A malformed agent record was checked against five field names while a
    runner record was checked against a schema, and nothing said so.
    """
    runner, agent, manifest = _records()
    del agent["action"]
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    ok, problems = analyse.validate_streams(root)

    assert not ok
    assert any("run-01.agent.jsonl" in p for p in problems), problems
