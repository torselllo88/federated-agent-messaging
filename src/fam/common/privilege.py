"""Positive evidence that the agent runtime is non-privileged.

C2 says normal operation must not require administrative access. Simply not
handing the agent a credential evidences nothing — it is an assumption. This
module turns the assumption into recorded observations the agent makes about
its own process (Task 01 §24, testbed-architecture.md §2.3, §15).

Evidence for C2 is the combination of ordinary Client-Server functionality,
absence of privileged credentials and mounts, explicit rejection of privileged
API access, and successful E0 operation. No single item here is sufficient on
its own, and the probe least of all.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: Environment variables that would indicate privileged access if present.
FORBIDDEN_ENV_MARKERS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_A_PASSWORD",
    "POSTGRES_B_PASSWORD",
    "REGISTRATION_SHARED_SECRET",
    "SYNAPSE_ADMIN_TOKEN",
    "MACAROON_SECRET_KEY",
    "FORM_SECRET",
)

#: Filesystem paths that would indicate server-side access if mounted.
FORBIDDEN_PATHS = (
    "/synapse",
    "/synapse/a",
    "/synapse/b",
    "/data/homeserver.yaml",
)

#: Read-only admin endpoint used for the privilege-negative probe. Chosen
#: because it changes no state; destructive or state-changing admin
#: operations must never be probed.
ADMIN_PROBE_PATH = "/_synapse/admin/v2/users?from=0&limit=1"


def environment_evidence() -> dict[str, Any]:
    """What the runtime can observe about its own credentials and mounts."""
    present_env = sorted(
        name for name in FORBIDDEN_ENV_MARKERS if os.environ.get(name)
    )
    present_paths = sorted(path for path in FORBIDDEN_PATHS if Path(path).exists())

    signing_keys = []
    for candidate in ("/data", "/synapse", "/tls"):
        base = Path(candidate)
        if base.is_dir():
            signing_keys.extend(str(p) for p in base.rglob("*.signing.key"))

    mounts = _mount_targets()
    synapse_mounts = sorted(m for m in mounts if "synapse" in m or m.startswith("/data"))

    return {
        "no_privileged_env_vars": not present_env,
        "privileged_env_vars_found": present_env,
        "no_server_paths_visible": not present_paths,
        "server_paths_found": present_paths,
        "no_signing_keys_visible": not signing_keys,
        "signing_keys_found": sorted(signing_keys),
        "no_synapse_mounts": not synapse_mounts,
        "synapse_mounts_found": synapse_mounts,
    }


def _mount_targets() -> list[str]:
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    targets = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2:
            targets.append(parts[1])
    return targets


def summarize(evidence: dict[str, Any], probe_status: int | None) -> dict[str, Any]:
    """Combine the environment facts with the probe result.

    ``probe_denied`` is true for 401 and 403. A 404 is NOT treated as denial:
    it would mean the endpoint is absent rather than refused, which evidences
    nothing about privilege.
    """
    denied = probe_status in (401, 403)
    unprivileged = (
        evidence["no_privileged_env_vars"]
        and evidence["no_server_paths_visible"]
        and evidence["no_signing_keys_visible"]
        and evidence["no_synapse_mounts"]
        and denied
    )
    return {
        **evidence,
        "admin_probe_path": ADMIN_PROBE_PATH,
        "admin_probe_status": probe_status,
        "admin_probe_denied": denied,
        "c2_supporting_evidence_complete": unprivileged,
        "note": (
            "Supporting evidence only. C2 rests on the combination of ordinary "
            "Client-Server operation, absent privileged credentials and mounts, "
            "explicit rejection of privileged API access, and successful E0."
        ),
    }
