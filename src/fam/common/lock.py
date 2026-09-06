"""The formal protocol lock: load it, check it, and refuse to run outside it.

experimental-protocol.md §46 defines what the lock contains. Task 07 §23 and
§66 define what it is *for*: before every formal run the effective execution
parameters are compared against the locked ones, and any difference stops the
run instead of quietly producing data under an unrecorded configuration.

The lock is only load-bearing for publication runs. Development runs read it
if it exists and ignore it otherwise, so pilots keep their environment knobs
(``FAM_E2_TIMELINE_LIMIT`` and friends) without those knobs being able to move
a frozen parameter once collection begins.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fam.common.frozen import (
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E0_REQUESTS_PER_PHASE,
    E0_RUNS,
    E1_QUIET_INTERVAL_SECONDS,
    E1_REQUESTS_PER_CLASS,
    E1_RUNS,
    E2_OFFLINE_REQUESTS,
    E2_RUNS,
    E2_SYNC_TIMELINE_LIMIT,
    E3_BODY_BYTES,
    E3_BOOTSTRAP_CONFIDENCE,
    E3_BOOTSTRAP_REPLICATES,
    E3_BOOTSTRAP_SEED,
    E3_CONCURRENCY_LEVELS,
    E3_DRAIN_SECONDS,
    E3_INTER_RUN_IDLE_SECONDS,
    E3_LATENCY_MAX_IN_FLIGHT,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_PAIRED_BLOCKS,
    E3_SCHEDULE_SEED,
    E3_SYNC_TIMELINE_LIMIT,
    E3_SYNC_TIMEOUT_MS,
    E3_WARMUP_SECONDS,
    EXECUTION_ANALYSIS_SPEC_VERSION,
    EXECUTION_PROTOCOL_VERSION,
    MANIFEST_SCHEMA_VERSION,
    PADDING_CHARACTER,
    RAW_SCHEMA_VERSION,
    ROOM_ENCRYPTION_ENABLED,
    ROOM_VERSION,
)

LOCK_ARTIFACT = "formal_protocol_lock"
LOCK_SCHEMA_VERSION = "1"

#: Tracked location. The lock is committed and tagged (§21), so it lives in the
#: worktree rather than under $FAM_RESULTS_DIR — unlike every run artifact.
DEFAULT_LOCK_PATH = Path("/app/results/protocol-lock.json")


class ProtocolLockError(RuntimeError):
    """A §66 stop condition: the run does not match the lock."""


def frozen_parameters() -> dict[str, Any]:
    """Every execution parameter the lock pins, read from the code that uses it.

    Built from ``fam.common.frozen`` rather than restated here, so a constant
    cannot drift away from the value the lock claims to have frozen.
    """
    return {
        "protocol_version": EXECUTION_PROTOCOL_VERSION,
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "room_version": ROOM_VERSION,
        "room_encryption_enabled": ROOM_ENCRYPTION_ENABLED,
        "interaction_timeout_seconds": DEFAULT_INTERACTION_TIMEOUT_SECONDS,
        "message_body_bytes": E3_BODY_BYTES,
        "message_padding_character": PADDING_CHARACTER,
        # --- repetition counts, §47 ---------------------------------------
        "e0_runs": E0_RUNS,
        "e0_requests_per_phase": E0_REQUESTS_PER_PHASE,
        "e1_runs": E1_RUNS,
        "e1_requests_per_class": E1_REQUESTS_PER_CLASS,
        "e1_quiet_interval_seconds": E1_QUIET_INTERVAL_SECONDS,
        "e2_runs": E2_RUNS,
        "e2_offline_requests": E2_OFFLINE_REQUESTS,
        "e2_sync_timeline_limit": E2_SYNC_TIMELINE_LIMIT,
        # --- E3 workloads --------------------------------------------------
        "e3_latency_max_in_flight": E3_LATENCY_MAX_IN_FLIGHT,
        "e3_latency_warmup_interactions": E3_LATENCY_WARMUP_INTERACTIONS,
        "e3_latency_measured_interactions": E3_LATENCY_MEASURED_INTERACTIONS,
        "e3_concurrency_levels": list(E3_CONCURRENCY_LEVELS),
        "e3_warmup_seconds": E3_WARMUP_SECONDS,
        "e3_measurement_seconds": E3_MEASUREMENT_SECONDS,
        "e3_drain_seconds": E3_DRAIN_SECONDS,
        "e3_paired_blocks": E3_PAIRED_BLOCKS,
        "e3_inter_run_idle_seconds": E3_INTER_RUN_IDLE_SECONDS,
        "e3_sync_timeline_limit": E3_SYNC_TIMELINE_LIMIT,
        "e3_sync_timeout_ms": E3_SYNC_TIMEOUT_MS,
        # --- seeds, §16 ------------------------------------------------------
        "e3_schedule_seed": E3_SCHEDULE_SEED,
        "e3_bootstrap_seed": E3_BOOTSTRAP_SEED,
        "e3_bootstrap_replicates": E3_BOOTSTRAP_REPLICATES,
        "e3_bootstrap_confidence": E3_BOOTSTRAP_CONFIDENCE,
    }


def lock_path() -> Path:
    override = os.environ.get("FAM_PROTOCOL_LOCK", "").strip()
    if override:
        return Path(override)
    if DEFAULT_LOCK_PATH.exists():
        return DEFAULT_LOCK_PATH
    # Outside the container image the repository root is wherever we are.
    return Path("results/protocol-lock.json")


def load(path: Path | None = None) -> dict[str, Any] | None:
    target = path or lock_path()
    if not target.exists():
        return None
    document = json.loads(target.read_text(encoding="utf-8"))
    if document.get("artifact") != LOCK_ARTIFACT:
        raise ProtocolLockError(
            f"{target} is not a protocol lock (artifact={document.get('artifact')!r})"
        )
    return document


def compare(document: dict[str, Any]) -> list[str]:
    """Differences between the locked parameters and this code's own values."""
    locked = document.get("frozen_parameters", {})
    effective = frozen_parameters()
    problems: list[str] = []
    for key in sorted(set(locked) | set(effective)):
        if key not in locked:
            problems.append(f"{key}: not in the lock, code has {effective[key]!r}")
        elif key not in effective:
            problems.append(f"{key}: locked as {locked[key]!r}, code no longer defines it")
        elif locked[key] != effective[key]:
            problems.append(
                f"{key}: locked {locked[key]!r}, effective {effective[key]!r}"
            )
    return problems


def runtime_overrides() -> list[str]:
    """Environment knobs that would move a locked parameter if they were set.

    A publication run must take every frozen value from the lock. These exist
    for pilots; finding one set during formal collection means the run would
    have executed under a configuration the lock does not describe.
    """
    watched = (
        "FAM_E2_TIMELINE_LIMIT",
        "FAM_E3_TIMELINE_LIMIT",
        "FAM_E3_SYNC_TIMEOUT_MS",
        "FAM_E3_BLOCKS",
        "FAM_E3_WORKLOADS",
        "FAM_E3_BOOTSTRAP_REPLICATES",
        "FAM_E3_CONCURRENCY",
    )
    return [name for name in watched if os.environ.get(name, "").strip()]


def enforce(*, publication_data: bool, experiment: str) -> dict[str, Any] | None:
    """Gate a run against the lock. Returns the lock, or None for a dev run.

    Raises rather than warning: §66 makes a lock mismatch a stop condition, and
    a warning printed into a 4-hour campaign log is not a stop.
    """
    document = load()
    if not publication_data:
        return document

    if document is None:
        raise ProtocolLockError(
            f"{experiment}: publication_data is true but no protocol lock was "
            f"found at {lock_path()}. Formal collection runs under a lock "
            f"(experimental-protocol.md §46)."
        )

    problems = compare(document)
    overrides = runtime_overrides()
    if overrides:
        problems.append(
            "environment overrides set during a publication run: "
            + ", ".join(overrides)
        )
    if problems:
        raise ProtocolLockError(
            f"{experiment}: protocol lock mismatch — "
            + "; ".join(problems)
        )
    return document
