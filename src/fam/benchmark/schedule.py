"""Deterministic paired-block schedule, campaign fingerprint, resume ledger.

experimental-protocol.md §24: the complete run order is generated *before*
data collection, from a recorded seed, with the local/federated order inside
each block counterbalanced. Nothing about the second run of a block may depend
on how the first one went — that is the whole point of fixing the order in
advance, and it is why generation lives here rather than inside the runner.

Task 05 §45/§46 add durable resume. A campaign is identified by a fingerprint
over every parameter that could change what the numbers mean; resuming with a
different fingerprint starts a new campaign rather than silently mixing two
parameter sets into one dataset.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fam.common.frozen import (
    E3_BODY_BYTES,
    E3_CONCURRENCY_LEVELS,
    E3_DRAIN_SECONDS,
    E3_INTER_RUN_IDLE_SECONDS,
    E3_LATENCY_MAX_IN_FLIGHT,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_PAIRED_BLOCKS,
    E3_TOPOLOGY_FEDERATED,
    E3_TOPOLOGY_LOCAL,
    E3_WARMUP_SECONDS,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
    EXECUTION_PROTOCOL_VERSION,
)

#: Development default. Task 05 records this as a recommendation; Task 07
#: locks the value it actually uses (§53).
DEFAULT_SCHEDULE_SEED = 20260905

#: Identity of the benchmark runtime that produced a campaign. Bumped when a
#: change to the runtime alters what the measurements mean, so that data from
#: before and after cannot be resumed into one another or pooled by accident.
#:
#: r1  first development campaign
#: r2  interaction outcome fixed at termination — a late ACK no longer turns
#:     a timed-out interaction into a success (experimental-protocol.md §11)
#: r3  the logical-interaction timeout is budgeted from T0 rather than armed
#:     after the send, so no successful interaction can report an RTT longer
#:     than the timeout that bounds it (§9, §10, §11)
RUNTIME_CODE_REVISION = "task-05-r3"


@dataclass(frozen=True)
class ScheduledRun:
    """One benchmark run, fully determined before the campaign starts."""

    workload: str
    block_id: str
    block_index: int
    within_block_order: int
    topology: str
    concurrency: int

    @property
    def key(self) -> str:
        """Stable identity of this scheduled run inside its campaign."""
        return (
            f"{self.workload}-c{self.concurrency:02d}-{self.block_id}-{self.topology}"
        )


def _block_id(workload: str, concurrency: int, index: int) -> str:
    if workload == E3_WORKLOAD_LATENCY:
        return f"lat-block{index:02d}"
    return f"thr-c{concurrency:02d}-block{index:02d}"


def _counterbalanced_orders(rng: random.Random, blocks: int) -> list[bool]:
    """One boolean per block: True means LOCAL first.

    Counterbalanced, then shuffled. Exactly half the blocks lead with each
    topology, so no residual ordering effect can accumulate in one direction;
    the shuffle is what stops the alternation itself from becoming a pattern.
    """
    half = blocks // 2
    orders = [True] * half + [False] * (blocks - half)
    rng.shuffle(orders)
    return orders


def generate_workload_schedule(
    *,
    workload: str,
    concurrency: int,
    seed: int,
    blocks: int = E3_PAIRED_BLOCKS,
) -> list[ScheduledRun]:
    """The complete paired-block order for one workload.

    Seeded per workload and concurrency so that adding a concurrency level
    cannot silently reshuffle one that has already executed.
    """
    rng = random.Random(f"{seed}:{workload}:{concurrency}")
    runs: list[ScheduledRun] = []
    for index, local_first in enumerate(_counterbalanced_orders(rng, blocks), start=1):
        block_id = _block_id(workload, concurrency, index)
        order = (
            (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED)
            if local_first
            else (E3_TOPOLOGY_FEDERATED, E3_TOPOLOGY_LOCAL)
        )
        for position, name in enumerate(order, start=1):
            runs.append(
                ScheduledRun(
                    workload=workload,
                    block_id=block_id,
                    block_index=index,
                    within_block_order=position,
                    topology=name,
                    concurrency=concurrency,
                )
            )
    return runs


def generate_campaign_schedule(
    *,
    seed: int = DEFAULT_SCHEDULE_SEED,
    blocks: int = E3_PAIRED_BLOCKS,
    concurrency_levels: tuple[int, ...] = E3_CONCURRENCY_LEVELS,
    workloads: tuple[str, ...] = (E3_WORKLOAD_LATENCY, E3_WORKLOAD_THROUGHPUT),
) -> list[ScheduledRun]:
    """Every scheduled run of the campaign, in execution order."""
    runs: list[ScheduledRun] = []
    if E3_WORKLOAD_LATENCY in workloads:
        runs += generate_workload_schedule(
            workload=E3_WORKLOAD_LATENCY,
            concurrency=E3_LATENCY_MAX_IN_FLIGHT,
            seed=seed,
            blocks=blocks,
        )
    if E3_WORKLOAD_THROUGHPUT in workloads:
        for level in concurrency_levels:
            runs += generate_workload_schedule(
                workload=E3_WORKLOAD_THROUGHPUT,
                concurrency=level,
                seed=seed,
                blocks=blocks,
            )
    return runs


# --------------------------------------------------------------- fingerprint


def campaign_parameters(
    *,
    seed: int,
    sync_timeline_limit: int | None,
    sync_timeout_ms: int,
    blocks: int = E3_PAIRED_BLOCKS,
    concurrency_levels: tuple[int, ...] = E3_CONCURRENCY_LEVELS,
    protocol_git_commit: str = "",
    config_hashes: dict[str, str] | None = None,
    rate_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything that could change what the campaign numbers mean.

    A resume against a different parameter set is a different experiment, so
    the fingerprint covers the frozen methodology, the two development choices
    Task 05 is allowed to make (seed and sync limit), and the environment
    configuration content hashes.
    """
    return {
        "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
        "runtime_code_revision": RUNTIME_CODE_REVISION,
        "protocol_git_commit": protocol_git_commit,
        "schedule_seed": seed,
        "paired_blocks": blocks,
        "concurrency_levels": list(concurrency_levels),
        "latency_max_in_flight": E3_LATENCY_MAX_IN_FLIGHT,
        "latency_warmup_interactions": E3_LATENCY_WARMUP_INTERACTIONS,
        "latency_measured_interactions": E3_LATENCY_MEASURED_INTERACTIONS,
        "warmup_seconds": E3_WARMUP_SECONDS,
        "measurement_seconds": E3_MEASUREMENT_SECONDS,
        "drain_seconds": E3_DRAIN_SECONDS,
        "inter_run_idle_seconds": E3_INTER_RUN_IDLE_SECONDS,
        "message_body_bytes": E3_BODY_BYTES,
        "sync_timeline_limit": sync_timeline_limit,
        "sync_timeout_ms": sync_timeout_ms,
        "synapse_config_hashes": dict(sorted((config_hashes or {}).items())),
        "rate_limit_configuration": rate_limits or {},
    }


def fingerprint(parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        parameters, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def campaign_id(parameters: dict[str, Any]) -> str:
    return f"e3dev-{fingerprint(parameters)[:16]}"


# ------------------------------------------------------------- resume ledger


@dataclass
class CampaignState:
    """Durable completion tracking, outside the tracked worktree.

    A partially completed campaign resumes from where it stopped. Completed
    runs are never overwritten and never re-executed: rerunning a finished run
    would either duplicate evidence or, worse, quietly replace a run whose
    result someone did not care for.
    """

    path: Path
    campaign_id: str
    parameters: dict[str, Any]
    fingerprint: str
    created_at: str
    completed: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def open(cls, root: Path, parameters: dict[str, Any]) -> "CampaignState":
        digest = fingerprint(parameters)
        identifier = campaign_id(parameters)
        directory = root / "campaigns"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{identifier}.json"

        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("fingerprint") != digest:
                # Cannot happen while the id derives from the digest, but a
                # hand-edited ledger must not be able to merge two parameter
                # sets into one dataset.
                raise ValueError(
                    f"campaign ledger {path} has fingerprint "
                    f"{payload.get('fingerprint')!r}, expected {digest!r}"
                )
            return cls(
                path=path,
                campaign_id=payload["campaign_id"],
                parameters=payload["parameters"],
                fingerprint=payload["fingerprint"],
                created_at=payload["created_at"],
                completed=payload.get("completed", {}),
            )

        state = cls(
            path=path,
            campaign_id=identifier,
            parameters=parameters,
            fingerprint=digest,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        state.flush()
        return state

    @property
    def resumed(self) -> bool:
        return bool(self.completed)

    def is_done(self, run: ScheduledRun) -> bool:
        return run.key in self.completed

    def record(
        self,
        run: ScheduledRun,
        *,
        run_id: str,
        digests: dict[str, str],
        status: str,
        manifest: str,
    ) -> None:
        self.completed[run.key] = {
            "run_id": run_id,
            "status": status,
            "manifest": manifest,
            "digests": digests,
            "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.flush()

    def verify_completed(self, root: Path) -> list[str]:
        """Confirm every recorded run still has its manifest and raw evidence.

        A ledger entry whose evidence has vanished or changed is not a
        completed run, and resuming past it would leave a hole in the dataset
        that nothing downstream would notice.
        """
        from fam.common.digests import file_sha256

        problems: list[str] = []
        for key, entry in sorted(self.completed.items()):
            manifest = root / entry["manifest"]
            if not manifest.exists():
                problems.append(f"{key}: manifest {entry['manifest']} is missing")
                continue
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            for artifact in payload.get("raw_artifacts", []):
                path = root / artifact["path"]
                if not path.exists():
                    problems.append(
                        f"{key}: raw artifact {artifact['path']} is missing"
                    )
                elif file_sha256(path) != artifact["sha256"]:
                    problems.append(
                        f"{key}: raw artifact {artifact['path']} changed on disk"
                    )
        return problems

    def flush(self) -> None:
        payload = {
            "campaign_id": self.campaign_id,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "parameters": self.parameters,
            "completed": self.completed,
        }
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    def write_schedule(self, runs: list[ScheduledRun]) -> Path:
        path = self.path.with_name(f"{self.campaign_id}.schedule.json")
        path.write_text(
            json.dumps(
                {
                    "campaign_id": self.campaign_id,
                    "fingerprint": self.fingerprint,
                    "schedule_seed": self.parameters["schedule_seed"],
                    "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
                    "runs": [asdict(run) for run in runs],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


def pending(runs: list[ScheduledRun], state: CampaignState) -> Iterator[ScheduledRun]:
    for run in runs:
        if not state.is_done(run):
            yield run
