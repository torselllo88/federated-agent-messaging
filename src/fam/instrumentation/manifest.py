"""Run manifests.

The common envelope and its two type-specific bodies are frozen in
experimental-protocol.md §38. This module writes the envelope plus the
``automated_experiment_manifest`` body; the ``human_llm_validation_manifest``
body belongs to E4 and is not implemented in this slice.

Manifests are written under ``$FAM_RESULTS_DIR`` with every other
run-generated artifact. Archival copies are imported into ``results/manifests/``
only after the campaign completes (experimental-protocol.md §3 Phase 5).
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fam.common.digests import file_sha256
from fam.common.frozen import (
    EXECUTION_ANALYSIS_SPEC_VERSION,
    EXECUTION_PROTOCOL_VERSION,
    MANIFEST_SCHEMA_VERSION,
    RAW_SCHEMA_VERSION,
    ROOM_VERSION,
)
from fam.common.validity import RunValidity

AUTOMATED = "automated_experiment_manifest"
HUMAN_LLM = "human_llm_validation_manifest"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def execution_host_identifier() -> str:
    """Identifier of the host that executed the run.

    For formal runs this must be the designated Linux host recorded in the
    protocol lock (experimental-protocol.md §39). Development runs record
    whatever they ran on, which is exactly why they are marked
    ``publication_data = false``.
    """
    explicit = os.environ.get("FAM_EXECUTION_HOST", "").strip()
    if explicit:
        return explicit
    return f"{platform.node()}/{platform.system()}-{platform.machine()}"


@dataclass
class RawArtifact:
    role: str
    path: Path

    def to_dict(self, results_root: Path) -> dict[str, Any]:
        try:
            relative = self.path.relative_to(results_root).as_posix()
        except ValueError:
            relative = self.path.name
        return {
            "role": self.role,
            "path": relative,
            "sha256": file_sha256(self.path),
            "bytes": self.path.stat().st_size,
        }


@dataclass
class RunManifest:
    experiment: str
    run_id: str
    room_id: str
    participants: dict[str, str]
    topology: str
    publication_data: bool
    protocol_git_commit: str
    environment_manifest: str | None = None
    started_at: str = field(default_factory=utc_now)
    completed_at: str | None = None
    completion_status: str = "incomplete"
    validity: RunValidity | None = None
    artifacts: list[RawArtifact] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    results_root: Path = Path("/results")

    def to_dict(self) -> dict[str, Any]:
        if self.validity is None:
            raise ValueError("a manifest must carry a validity classification")
        return {
            # --- common envelope, experimental-protocol.md §38 -------------
            "manifest_type": AUTOMATED,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "experiment": self.experiment,
            "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
            "execution_analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
            "protocol_git_commit": self.protocol_git_commit,
            "raw_schema_version": RAW_SCHEMA_VERSION,
            "publication_data": self.publication_data,
            "run_id": self.run_id,
            "room_id": self.room_id,
            "room_version": ROOM_VERSION,
            "participants": self.participants,
            "environment_manifest": self.environment_manifest,
            "execution_host_identifier": execution_host_identifier(),
            "start_timestamp": self.started_at,
            "completion_timestamp": self.completed_at,
            "completion_status": self.completion_status,
            "validity_classification": self.validity.to_manifest(),
            # --- automated_experiment_manifest body ------------------------
            "topology": self.topology,
            "raw_artifacts": [
                artifact.to_dict(self.results_root) for artifact in self.artifacts
            ],
            **self.extra,
        }

    def write(self, manifests_dir: Path) -> Path:
        manifests_dir.mkdir(parents=True, exist_ok=True)
        path = manifests_dir / f"{self.run_id}.manifest.json"
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        path.write_text(payload + "\n", encoding="utf-8")
        return path
