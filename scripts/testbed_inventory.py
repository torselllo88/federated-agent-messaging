#!/usr/bin/env python3
"""Testbed configuration inventory — an input to Task 07.

Collects, in one machine-readable place, the state of the implementation and
the development values Tasks 01-06 arrived at. Task 07 decides which of these
to lock; this file only reports them.

This is **not** the protocol lock. Nothing here is frozen by writing it down,
and every recommendation is labelled as a development finding with the
evidence that produced it.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from fam.common.digests import config_hash  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E3_BODY_BYTES,
    E3_CONCURRENCY_LEVELS,
    E3_DRAIN_SECONDS,
    E3_INTER_RUN_IDLE_SECONDS,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_PAIRED_BLOCKS,
    E3_WARMUP_SECONDS,
    EXECUTION_ANALYSIS_SPEC_VERSION,
    EXECUTION_PROTOCOL_VERSION,
    MANIFEST_SCHEMA_VERSION,
    RAW_SCHEMA_VERSION,
    ROOM_VERSION,
)
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)

CONFIGS = {
    "A": Path("/synapse/a/homeserver.yaml"),
    "B": Path("/synapse/b/homeserver.yaml"),
}


def _package_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return "unknown"


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=15, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _synapse_version() -> str:
    try:
        import yaml  # noqa: F401
    except ImportError:
        pass
    return os.environ.get("SYNAPSE_IMAGE", "unset")


def _image_digests(root: Path) -> dict[str, str]:
    """Resolved on the host, which is the only place that can ask Docker.

    §19 requires the digests in the inventory. A container cannot obtain them,
    so `make inventory` writes them beside the environment manifest first.
    """
    path = environment_dir(root) / "image-digests.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def main() -> int:
    root = ensure_layout(resolve_results_dir())

    import yaml

    hashes: dict[str, str] = {}
    listeners: dict[str, Any] = {}
    for key, path in CONFIGS.items():
        if not path.exists():
            continue
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        hashes[key] = config_hash(document)
        listeners[key] = [
            {
                "port": item.get("port"),
                "tls": item.get("tls"),
                "resources": [
                    name
                    for resource in item.get("resources", [])
                    for name in resource.get("names", [])
                ],
            }
            for item in document.get("listeners", [])
        ]

    inventory: dict[str, Any] = {
        "artifact": "testbed_configuration_inventory",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": (
            "Input to Task 07. Not the protocol lock: nothing here is frozen "
            "by being recorded, and every recommendation names the evidence "
            "that produced it."
        ),
        "publication_data": False,
        # ---------------------------------------------------- software
        "software": {
            "synapse_image": os.environ.get("SYNAPSE_IMAGE", "unset"),
            "postgres_image": os.environ.get("POSTGRES_IMAGE", "unset"),
            "python": platform.python_version(),
            "matrix_nio": _package_version("matrix-nio"),
            "jsonschema": _package_version("jsonschema"),
            "pyyaml": _package_version("PyYAML"),
            "cryptography": _package_version("cryptography"),
        },
        # ---------------------------------------------------- frozen values
        "frozen": {
            "matrix_room_version": ROOM_VERSION,
            "room_encryption_enabled": False,
            "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
            "execution_analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
            "raw_schema_version": RAW_SCHEMA_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "interaction_timeout_seconds": DEFAULT_INTERACTION_TIMEOUT_SECONDS,
            "e3_message_body_bytes": E3_BODY_BYTES,
            "e3_concurrency_levels": list(E3_CONCURRENCY_LEVELS),
            "e3_latency_warmup_interactions": E3_LATENCY_WARMUP_INTERACTIONS,
            "e3_latency_measured_interactions": E3_LATENCY_MEASURED_INTERACTIONS,
            "e3_warmup_seconds": E3_WARMUP_SECONDS,
            "e3_measurement_seconds": E3_MEASUREMENT_SECONDS,
            "e3_drain_seconds": E3_DRAIN_SECONDS,
            "e3_inter_run_idle_seconds": E3_INTER_RUN_IDLE_SECONDS,
            "e3_paired_blocks": E3_PAIRED_BLOCKS,
        },
        # ------------------------------------------- development findings
        "development_recommendations": {
            "e2_sync_timeline_limit": {
                "value": 10,
                "evidence": "Task 03 pilot: selected to maximise history "
                            "coverage so recovery is genuinely exercised.",
            },
            "e3_sync_timeline_limit": {
                "value": 500,
                "evidence": "Confirmed on the formal Linux host: peak workload "
                            "timeline occupancy 18-22 events, 22.7x headroom, "
                            "zero truncated syncs during the workload and zero "
                            "interactions delivered through gap recovery. The "
                            "development host observed 21-25 events; §14 makes "
                            "the Linux-host observation the formal baseline.",
            },
            "e3_sync_timeout_ms": {"value": 30000, "evidence": "Task 05 pilot."},
            "e3_schedule_seed": {
                "value": 20260905,
                "evidence": "Task 05: counterbalancing verified at 10 "
                            "local-first and 10 federated-first per workload.",
            },
            "e3_bootstrap_seed": {"value": 20260905, "evidence": "Task 05 analysis."},
            "e3_bootstrap_replicates": {
                "value": 2000,
                "evidence": "Task 05: sufficient for a 95% percentile interval "
                            "over twenty clusters.",
            },
        },
        # ------------------------------------------------- implementation
        "implementation": {
            "protocol_git_commit": _git("rev-parse", "HEAD")
            or os.environ.get("FAM_PROTOCOL_GIT_COMMIT", "unknown"),
            "worktree_clean": _git("status", "--porcelain") == "",
            "runtime_code_revision": "task-07-formal",
            "raw_schema_versions_supported": ["1", "2"],
            "terminal_outcome_taxonomy": [
                "success", "timeout", "send_error", "malformed_response",
                "unexpected_response", "runner_error", "offline_send",
            ],
            "post_terminal_integrity_observations": ["duplicate_ack"],
            "median_definition": (
                "sample median; nearest rank is used only for percentiles that "
                "are not medians (experimental-protocol.md 31)"
            ),
            "deterministic_executor": "deterministic",
            "llm_executor": "llm",
            "request_protocols": ["controlled", "natural_language"],
            "e3_agent_concurrency": {
                "mode": "sequential",
                "note": "The agent awaits its response send inline in the sync "
                        "handler, serving roughly 9-12 requests/s regardless of "
                        "offered load. Task 05 measured C=32 returning the same "
                        "throughput as C=8. Deliberately unchanged in Task 06 "
                        "(Task 06 §32); Task 07 reproduces this system unless "
                        "an explicit methodological decision is taken first.",
            },
        },
        # ------------------------------------------------------ endpoints
        "endpoints": {
            "client_server_http_internal": "http://synapse-a:8008",
            "client_server_https_external": (
                f"https://hs-a.test:{os.environ.get('FAM_E4_CS_TLS_PORT', '8449')}"
            ),
            "federation": "https://hs-{a,b}.test:8448",
            "https_external_client_mode": (
                "direct Synapse TLS on a dedicated client listener; no proxy, "
                "no bridge, no relay"
            ),
            "listeners": listeners,
        },
        "synapse_config_hashes": hashes,
        "image_digests": _image_digests(root),
        # §3: the machine, not the container. Nothing inside a container can
        # determine these, so the host passes them in.
        "host": {
            "identifier": os.environ.get("FAM_EXECUTION_HOST", "unrecorded"),
            "distribution": os.environ.get("FAM_HOST_DISTRIBUTION", "unrecorded"),
            "virtualization": os.environ.get("FAM_HOST_VIRTUALIZATION", "unrecorded"),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
        },
        "e4": {
            "llm_provider": os.environ.get("FAM_LLM_PROVIDER", "anthropic"),
            "llm_model": os.environ.get("FAM_LLM_MODEL", "unset"),
            "human_client_name": os.environ.get("FAM_E4_CLIENT_NAME", "unset"),
            "human_client_version": os.environ.get("FAM_E4_CLIENT_VERSION", "unset"),
            "human_client_host": os.environ.get("FAM_E4_CLIENT_HOST", "unset"),
            "conversation_history_turns": 0,
            "note": "No conversational memory. Each request is answered on its "
                    "own; only the system instruction and the one request text "
                    "reach the provider.",
        },
        "known_deviations": [
            "raw/e3/pilot/ and raw/readiness/ sit outside the frozen §37 "
            "experiment tree so pilot and readiness output cannot be mistaken "
            "for campaign evidence. §37's structure is 'Recommended'.",
            "An E4-only HTTPS Client-Server listener runs on port 8449 and is "
            "published for Domain A. Required by testbed-architecture.md §7. "
            "It is the only port reachable from outside the host: both "
            "plaintext Client-Server ports are bound to loopback.",
            "The §22 steady-state check is reported, never gated. The protocol "
            "requires a material difference to be resolved before lock but "
            "defines no threshold, and §25 forbids inventing one. Observed on "
            "this host: completion series flat to within one to two "
            "completions per five-second bucket, half-to-half ratios within "
            "1.1% and not reproducible in direction across repeated pilots.",
            "Warm-up to window completion rate at C=32 federated is "
            "approximately 0.91, reproduced across two pilots on this host, "
            "so a residual transient persists past the frozen 10-second "
            "warm-up. warmup_seconds stays 10 (§15 freezes it, Task 07 §2 "
            "forbids changing it); recorded as an observation, not corrected.",
        ],
    }

    path = environment_dir(root) / "testbed-inventory.json"
    path.write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"testbed inventory: {path}")
    print(f"  room version           {ROOM_VERSION}")
    print(f"  synapse image          {inventory['software']['synapse_image']}")
    print(f"  matrix-nio             {inventory['software']['matrix_nio']}")
    print(f"  runtime revision       {inventory['implementation']['runtime_code_revision']}")
    print(f"  E3 sync timeline limit 500 (development recommendation)")
    print(f"  E2 sync timeline limit 10 (development recommendation)")
    print(f"  E4 model               {inventory['e4']['llm_model']}")
    for key, digest in hashes.items():
        print(f"  domain {key} config hash  {digest[:16]}…")
    print("\nThis is a Task 07 input, not the protocol lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
