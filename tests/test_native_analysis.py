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

import ast
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


def test_no_entry_point_hardcodes_the_container_path():
    """No script may name `/app` to find anything.

    Both halves of this were wrong in the same way. The schema directory was
    `/app/results/schemas`, which off container resolves to nothing and left
    the caller to interpret the silence. The import bootstrap was
    `/app/src`, which off container fails differently but just as flatly, and
    the working-directory fallback some scripts carried only helped when they
    were run from the repository root. Both now resolve from `__file__`, which
    is correct in the container, in a clone, and under an absolute path from
    anywhere.

    Asserting `SCHEMA_DIR.exists()` would not have caught the original bug,
    because the constant was valid in the one environment being tested. This
    looks for the shape instead.
    """
    offenders = []
    pattern = re.compile(r"""["']/app/""")
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
def test_a_conforming_tree_that_passes_its_criteria_reaches_PASS(
    tmp_path, monkeypatch, capsys
):
    """The baseline the other verdicts are measured against."""
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 0
    assert "ANALYSE: PASS" in out


@needs_jsonschema
def test_a_run_that_did_not_pass_is_an_acceptance_failure(
    tmp_path, monkeypatch, capsys
):
    """The one verdict that is a statement about the experiments.

    Everything else says the pipeline could not reach a statement. This one
    says it did, and the evidence fell short — so it must not be reachable by
    breaking the tool, and the tool failures must not be reachable by failing
    an experiment.
    """
    runner, agent, manifest = _records()
    manifest["completion_status"] = "fail"
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (acceptance)" in out
    assert "validation unavailable" not in out
    assert "FAIL (schema)" not in out


@needs_jsonschema
def test_a_broken_analysis_still_ends_on_a_verdict(tmp_path, monkeypatch, capsys):
    """A crash used to print a traceback and no verdict at all.

    Anything reading the last line for a result then saw whatever the run had
    printed before it died, which for a long analysis is a list of passing
    experiments. The traceback is still printed — it is what makes the failure
    fixable — and the verdict follows it.
    """
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)

    def explode(*_args, **_kwargs):
        raise RuntimeError("deliberate")

    monkeypatch.setattr(analyse, "summarize_e0", explode)
    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (analysis)" in out
    assert "FAIL (acceptance)" not in out


def test_an_unusable_results_directory_is_a_precondition_failure(monkeypatch, capsys):
    monkeypatch.delenv("FAM_RESULTS_DIR", raising=False)

    code = analyse.main()
    out = capsys.readouterr().out

    assert code == 2
    assert "FAIL (precondition)" in out
    assert "FAM_RESULTS_DIR" in out


@needs_jsonschema
def test_a_failing_stage_leaves_no_partial_artifact(tmp_path, monkeypatch, capsys):
    """Fail-closed means nothing is written, not that nothing is reported.

    A half-written summary is worse than none: it carries the provenance
    triple, looks complete, and is wrong. Checked on the validation stage,
    which is the one that used to fail on every off-container run.
    """
    runner, agent, manifest = _records()
    root = _tree(tmp_path, runner=runner, agent=agent, manifest=manifest)
    monkeypatch.setitem(sys.modules, "jsonschema", None)

    code, out = _run_main(root, monkeypatch, capsys)

    assert code == 1
    assert "FAIL (validation unavailable)" in out
    processed = root / "processed"
    written = sorted(p.name for p in processed.rglob("*")) if processed.exists() else []
    assert written == [], f"partial output after a failed run: {written}"


# --------------------------------------------------------------- line endings


def _crlf(path: Path) -> int:
    return path.read_bytes().count(b"\r\n")


def test_analysis_tables_are_written_with_lf_only(tmp_path):
    """The csv dialect terminates lines, not the platform.

    `DictWriter` emits CRLF everywhere unless pinned — inside the Linux image
    too — while the copies committed to the repository are LF. The two matched
    only by accident: `core.autocrlf` normalised on commit and converted back
    on checkout, so a Windows working tree held CRLF and compared equal to its
    own output. Pinning line endings in `.gitattributes` removed the second
    half of that accident, and the tables stopped reproducing byte for byte
    from a clean clone. This is the assertion that would have caught it.
    """
    from fam.analysis import e3

    summary = {
        "latency": {
            "by_topology": {
                "local": {
                    "p50_ms": 1.0, "p95_ms": 2.0, "p99_ms": 3.0,
                    "successful_interactions": 1, "initiated_interactions": 1,
                    "failure_rate": 0.0, "runs": 1,
                }
            },
            "bootstrap": {},
        },
        "throughput": {"by_concurrency": {}},
    }
    written = e3.write_tables(summary, tmp_path)
    assert written, "the writer produced no table to check"
    for path in written:
        assert _crlf(path) == 0, f"{path.name} carries CRLF"
        assert path.read_bytes().endswith(b"\n")


def _csv_writer_calls(tree: ast.AST):
    """Yield every `csv.DictWriter(...)` or `csv.writer(...)` call node.

    Parsed rather than scanned. A text scanner has to decide where the
    argument list ends, and the arguments here contain calls of their own —
    the first attempt read `lineterminator` as absent because a non-greedy
    match stopped at the `)` of an inner call. The parser already knows.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"DictWriter", "writer"}
            and isinstance(func.value, ast.Name)
            and func.value.id == "csv"
        ):
            yield node


def test_no_csv_writer_leaves_its_line_terminator_to_the_dialect():
    """Guards the writers this repository has not written yet.

    The tests above assert the output of the two that exist. A third added
    later would default to CRLF and reintroduce exactly this, so the
    constructor is checked as well as its result.
    """
    unpinned = []
    for path in sorted((ROOT / "scripts").glob("*.py")) + sorted(
        (ROOT / "src").rglob("*.py")
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _csv_writer_calls(tree):
            if not any(kw.arg == "lineterminator" for kw in call.keywords):
                unpinned.append(f"{path.name}:{call.lineno}")
    assert not unpinned, (
        "csv writers default to CRLF on every platform; pin "
        'lineterminator="\\n" in:\n  ' + "\n  ".join(unpinned)
    )


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
