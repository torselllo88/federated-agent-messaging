"""Digests, canonical config hashing, and the results-directory guard."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fam.common.digests import bytes_sha256, canonical_json, config_hash, file_sha256, sanitize
from fam.common.results import ensure_layout, raw_dir, resolve_results_dir
from fam.common.validity import InvalidRun, InvalidRunClass


def test_file_digest_matches_bytes_digest(tmp_path: Path):
    payload = b'{"a":1}\n'
    target = tmp_path / "stream.jsonl"
    target.write_bytes(payload)
    assert file_sha256(target) == bytes_sha256(payload)


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_sanitize_redacts_secret_bearing_keys():
    document = {
        "database": {"args": {"user": "synapse", "password": "hunter2"}},
        "registration_shared_secret": "abc",
        "macaroon_secret_key": "def",
        "server_name": "hs-a.test",
    }
    clean = sanitize(document)
    assert clean["database"]["args"]["password"] == "<redacted>"
    assert clean["registration_shared_secret"] == "<redacted>"
    assert clean["macaroon_secret_key"] == "<redacted>"
    assert clean["server_name"] == "hs-a.test"
    assert clean["database"]["args"]["user"] == "synapse"


def test_structural_key_paths_are_not_redacted():
    document = {"signing_key_path": "/data/hs-a.test.signing.key", "trusted_key_servers": []}
    clean = sanitize(document)
    assert clean["signing_key_path"] == "/data/hs-a.test.signing.key"
    assert clean["trusted_key_servers"] == []


def test_config_hash_ignores_secret_values_but_not_structure():
    base = {"server_name": "hs-a.test", "password": "one"}
    same_but_other_secret = {"server_name": "hs-a.test", "password": "two"}
    different_structure = {"server_name": "hs-b.test", "password": "one"}

    assert config_hash(base) == config_hash(same_but_other_secret)
    assert config_hash(base) != config_hash(different_structure)


def test_results_dir_must_be_set(monkeypatch):
    monkeypatch.delenv("FAM_RESULTS_DIR", raising=False)
    with pytest.raises(InvalidRun) as exc:
        resolve_results_dir()
    assert exc.value.validity.invalid_class is InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION


def test_results_dir_inside_the_repository_is_rejected(monkeypatch):
    """The guard that keeps the worktree clean across a run series."""
    import fam.common.results as results_module

    repo = Path(results_module.__file__).resolve().parents[3]
    monkeypatch.setenv("FAM_RESULTS_DIR", str(repo / "results" / "raw"))
    with pytest.raises(InvalidRun) as exc:
        resolve_results_dir()
    assert (
        exc.value.validity.invalid_class
        is InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION
    )
    assert "inside the tracked repository" in str(exc.value)


def test_results_dir_outside_the_repository_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("FAM_RESULTS_DIR", str(tmp_path / "fam-results"))
    root = resolve_results_dir()
    assert root.is_dir()


def test_layout_matches_the_frozen_structure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAM_RESULTS_DIR", str(tmp_path / "out"))
    root = ensure_layout(resolve_results_dir())
    for expected in (
        "raw/e0",
        "raw/e1",
        "raw/e2",
        "raw/e3/latency",
        "raw/e3/throughput",
        "raw/e4",
        "manifests",
        "environment",
        "evidence",
    ):
        assert (root / expected).is_dir(), expected
    # There is no top-level e4 directory; E4 raw data lives under raw/.
    assert not (root / "e4").exists()
    assert raw_dir(root, "E0") == root / "raw" / "e0"
