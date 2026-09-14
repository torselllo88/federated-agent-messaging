"""The analysis entry point, run the way a reviewer runs it.

`make analyse` executes inside the toolbox image, where `/app` exists and the
dependencies are installed, so nothing here was ever exercised by it. Off
container the schema directory resolved to a path that does not exist, every
runner record was reported as unvalidatable, and the run stopped with
`FAIL (schema)` before computing anything — a message that blamed the data for
a broken lookup.

These tests run the real entry point from outside the container. Asserting
that `SCHEMA_DIR.exists()` would not have caught it: the old value was a
constant that is perfectly valid in the one environment nobody was testing.
"""

from __future__ import annotations

import importlib.util
import json
import os
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
    (root / "raw" / "e0" / "run-01.runner.jsonl").write_text(
        json.dumps(runner) + "\n", encoding="utf-8"
    )
    (root / "raw" / "e0" / "run-01.agent.jsonl").write_text(
        json.dumps(agent) + "\n", encoding="utf-8"
    )
    (root / "manifests" / "run-01.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return root


def _records(**overrides):
    runner = {
        "schema_version": "2",
        "stream": "runner",
        "experiment": "E0",
        "run_id": "run-01",
        "record_type": "interaction",
        "outcome": "success",
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
        "manifest_type": "run_manifest",
        "manifest_schema_version": 1,
        "experiment": "E0",
        "execution_protocol_version": "1.2",
        "execution_analysis_spec_version": "1.2",
        "protocol_git_commit": "c0ffee",
        "raw_schema_version": "2",
        "publication_data": False,
        "run_id": "run-01",
        "room_id": "!room:hs-a.test",
        "room_version": "12",
        "participants": ["@agent:hs-a.test"],
        "execution_host_identifier": "test",
        "start_timestamp": "2026-01-01T00:00:00Z",
        "completion_status": "pass",
        "validity_classification": {"valid": True, "invalid_class": None},
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
