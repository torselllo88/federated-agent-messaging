#!/usr/bin/env python3
"""Sanitized environment manifest.

Produced by the bootstrap / verifier layer rather than the experiment runner:
the runner has no access to server configuration, and that restriction is part
of the C2 evidence (testbed-architecture.md §33, experimental-protocol.md §39).

Secrets are stripped before hashing and before publication. Configuration
hashes are the SHA-256 of the canonicalized, secret-stripped document.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/src")

import yaml  # noqa: E402

from fam.common.digests import config_hash, sanitize  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    EXECUTION_ANALYSIS_SPEC_VERSION,
    EXECUTION_PROTOCOL_VERSION,
    MANIFEST_SCHEMA_VERSION,
    RAW_SCHEMA_VERSION,
    ROOM_VERSION,
)
from fam.common.results import ensure_layout, environment_dir, resolve_results_dir  # noqa: E402

CONFIGS = {"A": Path("/synapse/a/homeserver.yaml"), "B": Path("/synapse/b/homeserver.yaml")}


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=20, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return "unknown"


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _memory_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return 0


def _virtualization() -> str:
    """Record the container runtime honestly.

    A virtualized desktop container runtime introduces scheduling noise of the
    same order as the effect E3 measures, so where a run executes on one, the
    manifest says so and the manuscript treats it as a limitation
    (experimental-protocol.md §39).
    """
    markers = []
    if Path("/.dockerenv").exists():
        markers.append("docker")
    try:
        version = Path("/proc/version").read_text(encoding="utf-8").lower()
        if "microsoft" in version or "wsl" in version:
            markers.append("wsl2")
    except OSError:
        pass
    return "+".join(markers) or "unknown"


def main() -> int:
    root = ensure_layout(resolve_results_dir())
    env_dir = environment_dir(root)

    hashes: dict[str, str] = {}
    sanitized: dict[str, dict] = {}
    for key, path in CONFIGS.items():
        if not path.exists():
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        hashes[key] = config_hash(document)
        sanitized[key] = sanitize(document)

    manifest = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "bootstrap/environment-verifier",
        "publication_data": os.environ.get("FAM_PUBLICATION_DATA", "false") == "true",
        "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
        "execution_analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "protocol_git_commit": os.environ.get("FAM_PROTOCOL_GIT_COMMIT", "unknown"),
        "formal_run_host_identifier": os.environ.get(
            "FAM_EXECUTION_HOST", platform.node()
        ),
        "matrix_room_version": ROOM_VERSION,
        "software": {
            "python": platform.python_version(),
            "matrix_nio": _package_version("matrix-nio"),
            "synapse_image": os.environ.get("SYNAPSE_IMAGE", "unset"),
            "postgres_image": os.environ.get("POSTGRES_IMAGE", "unset"),
        },
        "host": {
            "os": platform.system(),
            "kernel": platform.release(),
            "cpu_model": _cpu_model(),
            "logical_cpus": os.cpu_count(),
            "available_ram_bytes": _memory_bytes(),
            "virtualization": _virtualization(),
        },
        "config_hashes": hashes,
        "sanitized_config": sanitized,
        "notes": [
            "Synapse and PostgreSQL image digests are recorded by the host "
            "tooling; see environment/image-digests.json when present.",
            "Development environment. publication_data is false and these "
            "values are not publication evidence.",
        ],
    }

    stamp = manifest["generated_at"].replace(":", "").replace("-", "")
    path = env_dir / f"environment-{stamp}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (env_dir / "environment-latest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"environment manifest: {path}")
    for key, digest in hashes.items():
        print(f"  domain {key} config sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
