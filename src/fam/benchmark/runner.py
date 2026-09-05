"""One E3 benchmark run, end to end.

Room setup, agent lifecycle, workload execution, evidence and manifest. The
topology is a parameter; nothing in this module branches on which one it is
beyond resolving the agent identity, which is what §27 and §31 require.

The run writes:

    $FAM_RESULTS_DIR/raw/e3/<workload>/<run_id>.runner.jsonl
    $FAM_RESULTS_DIR/raw/e3/<workload>/<run_id>.agent.jsonl
    $FAM_RESULTS_DIR/manifests/<run_id>.manifest.json

These are actual E3 development runs, so they belong under the frozen E3 tree
(§37). What distinguishes them from formal evidence is ``publication_data``,
not their location — Task 04 readiness artifacts live elsewhere precisely
because they are *not* E3 runs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fam.benchmark import host
from fam.benchmark.engine import (
    EXPERIMENT,
    RunConfig,
    WorkloadResult,
    run_workload,
)
from fam.benchmark.schedule import ScheduledRun
from fam.benchmark.topology import BenchmarkTopology, topology as resolve_topology
from fam.common.digests import file_sha256
from fam.common.env import (
    account,
    agent_state_dir,
    protocol_git_commit,
    publication_data,
)
from fam.common.frozen import (
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E3_BODY_BYTES,
    E3_DRAIN_SECONDS,
    E3_LATENCY_MEASURED_INTERACTIONS,
    E3_LATENCY_WARMUP_INTERACTIONS,
    E3_MEASUREMENT_SECONDS,
    E3_WARMUP_SECONDS,
    E3_WORKLOAD_LATENCY,
    ROOM_VERSION,
)
from fam.common.validity import VALID, InvalidRun, InvalidRunClass, RunValidity
from fam.instrumentation.manifest import RawArtifact, RunManifest
from fam.instrumentation.streams import JsonlStream
from fam.matrix.rooms import assert_frozen_room_configuration
from fam.participants.human import HumanParticipant

AGENT_SETTLE_SECONDS = 2.0


@dataclass
class BenchmarkRun:
    """The result of one benchmark run. Facts only; metrics come later."""

    scheduled: ScheduledRun
    run_id: str
    room_id: str = ""
    room_version: str = ""
    encryption_enabled: bool = True
    membership: list[str] = field(default_factory=list)
    workload_result: WorkloadResult | None = None
    sender_transport: dict[str, Any] = field(default_factory=dict)
    setup_transport: dict[str, Any] = field(default_factory=dict)
    agent_transport: dict[str, Any] = field(default_factory=dict)
    host_diagnostics: dict[str, Any] = field(default_factory=dict)
    validity: RunValidity = VALID
    completion_status: str = "incomplete"
    problems: list[str] = field(default_factory=list)
    runner_stream: Path | None = None
    agent_stream: Path | None = None
    manifest_path: Path | None = None

    @property
    def topology(self) -> BenchmarkTopology:
        return resolve_topology(self.scheduled.topology)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    import json

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _agent_transport_summary(telemetry: list[dict]) -> dict[str, Any]:
    """Agent-side transport facts, with setup separated from the workload.

    The agent joins the room and performs an initial sync before any request
    exists. That first sync is limited by construction and recovers nothing,
    so counting it as workload gap recovery would misreport a healthy sync
    configuration as a truncating one. An episode that contributed events
    from history is the one that actually sat on the delivery path.
    """
    summaries = [r for r in telemetry if r.get("action") == "live_sync_summary"]
    final = summaries[-1] if summaries else {}
    responded = [r for r in telemetry if r.get("action") == "responded"]
    episodes = [r for r in telemetry if r.get("action") == "live_recovery_complete"]

    productive = [e for e in episodes if e.get("recovered_from_history")]
    first_response = next(
        (
            index
            for index, record in enumerate(telemetry)
            if record.get("action") == "responded"
        ),
        None,
    )
    before_workload = sum(
        1
        for index, record in enumerate(telemetry)
        if record.get("action") == "live_recovery_complete"
        and (first_response is None or index < first_response)
    )
    return {
        "acks_emitted": len(responded),
        "distinct_sequences_answered": len(
            {
                r.get("sequence_id")
                for r in responded
                if r.get("sequence_id") is not None
            }
        ),
        "live_recovery_episodes": final.get("live_recovery_episodes", len(episodes)),
        "live_recovery_pages": final.get("live_history_pages_fetched", 0),
        "live_recovery_failures": final.get("live_recovery_failures", 0),
        "limited_syncs": final.get("live_limited_syncs", 0),
        "checkpoint_commits": final.get("checkpoint_commits", 0),
        "setup_recovery_episodes": before_workload,
        "productive_recovery_episodes": len(productive),
        "events_recovered_from_history": sum(
            e.get("recovered_from_history", 0) for e in productive
        ),
    }


async def execute_benchmark_run(
    *,
    scheduled: ScheduledRun,
    run_id: str,
    root: Path,
    sync_timeline_limit: int | None,
    sync_timeout_ms: int,
    campaign_id: str,
    campaign_fingerprint: str,
    schedule_seed: int,
    rate_limit_reference: dict[str, Any],
    environment_manifest: str | None,
    body_bytes: int = E3_BODY_BYTES,
    warmup_interactions: int = E3_LATENCY_WARMUP_INTERACTIONS,
    measured_interactions: int = E3_LATENCY_MEASURED_INTERACTIONS,
    warmup_seconds: float = E3_WARMUP_SECONDS,
    measurement_seconds: float = E3_MEASUREMENT_SECONDS,
    drain_seconds: float = E3_DRAIN_SECONDS,
    raw_subdir: str | None = None,
) -> BenchmarkRun:
    """Execute one scheduled benchmark run and write its evidence."""
    from fam.agent.supervisor import AgentProcess

    result = BenchmarkRun(scheduled=scheduled, run_id=run_id)
    result.host_diagnostics = host.snapshot(
        note=f"captured before {run_id}"
    )

    topology = result.topology
    subdir = raw_subdir or scheduled.workload
    raw = root / "raw" / "e3" / subdir
    raw.mkdir(parents=True, exist_ok=True)
    runner_path = raw / f"{run_id}.runner.jsonl"
    agent_path = raw / f"{run_id}.agent.jsonl"
    result.runner_stream = runner_path
    result.agent_stream = agent_path

    # Only a run the campaign ledger does not already hold reaches this point,
    # so anything here is debris from an attempt that was interrupted before
    # it could write a manifest. Removing it keeps the stream from being
    # appended to; a completed run is never re-executed and never overwritten.
    runner_path.unlink(missing_ok=True)
    agent_path.unlink(missing_ok=True)

    state_dir = agent_state_dir() / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    sender_account = account(topology.sender)
    agent_account = account(topology.agent)

    human = HumanParticipant(
        homeserver_url=sender_account.homeserver_url,
        user_id=topology.sender,
        password=sender_account.password,
        device_name="fam-benchmark-sender",
    )
    # §27: identical synchronization configuration in both conditions. Set
    # once here, from one value, so the two topologies cannot drift apart.
    human.client.timeline_limit = sync_timeline_limit
    human.client.sync_timeout_ms = sync_timeout_ms

    agent_process = AgentProcess(
        user_id=topology.agent,
        password=agent_account.password,
        homeserver=agent_account.homeserver_url,
        experiment=EXPERIMENT,
        run_id=run_id,
        room_id="",
        telemetry=agent_path,
        state_dir=state_dir,
        body_bytes=body_bytes,
        timeline_limit=sync_timeline_limit,
    )

    config = RunConfig(
        run_id=run_id,
        workload=scheduled.workload,
        block_id=scheduled.block_id,
        within_block_order=scheduled.within_block_order,
        topology_name=topology.name,
        receiver_role=topology.receiver_role,
        sender=topology.sender,
        room_id="",
        concurrency=scheduled.concurrency,
        body_bytes=body_bytes,
        timeout_seconds=DEFAULT_INTERACTION_TIMEOUT_SECONDS,
        warmup_interactions=warmup_interactions,
        measured_interactions=measured_interactions,
        warmup_seconds=warmup_seconds,
        measurement_seconds=measurement_seconds,
        drain_seconds=drain_seconds,
    )

    try:
        # The live loop starts only once the benchmark room exists, so it can
        # never reconcile history from rooms earlier runs left behind.
        await human.start(defer_sync=True)

        # §18: a fresh room for every run, with no prior experimental history.
        room_id = await human.client.create_room(
            name=f"FAM E3 {scheduled.workload} {run_id}", invite=[topology.agent]
        )
        result.room_id = room_id
        config.room_id = room_id
        human.bind_room(room_id)
        human.client.tracked_rooms = {room_id}
        human.begin_sync()
        agent_process.room_id = room_id

        # §4.2: frozen room configuration, asserted at creation.
        version, encrypted = await assert_frozen_room_configuration(
            human.client, room_id
        )
        result.room_version = version
        result.encryption_enabled = encrypted

        await agent_process.start()
        await asyncio.sleep(AGENT_SETTLE_SECONDS)

        # §18: exactly two participants, and no unexpected experimental one.
        result.membership = await human.client.joined_members(room_id)
        # Every run leaves its benchmark room behind, so this grows across the
        # campaign. Recorded rather than cleaned up: leaving rooms would make
        # them unauditable afterwards, and the growth belongs in the evidence.
        result.host_diagnostics["sender_joined_rooms"] = len(
            await human.client.joined_room_ids()
        )
        expected = {topology.sender, topology.agent}
        if set(result.membership) != expected:
            raise InvalidRun(
                InvalidRunClass.FROZEN_CONFIGURATION_ERROR,
                f"benchmark room {room_id} membership is "
                f"{sorted(result.membership)}, frozen membership is "
                f"{sorted(expected)}",
            )

        # Everything up to here is setup. From this point the transport
        # diagnostics describe the workload and nothing else.
        result.setup_transport = human.client.reset_transport_diagnostics()
        result.workload_result = await run_workload(human=human, config=config)
        result.completion_status = "complete"

    except InvalidRun as exc:
        result.validity = exc.validity
        result.completion_status = "invalid"
        result.problems.append(str(exc))
    except Exception as exc:  # noqa: BLE001
        # An implementation failure of the runner is an invalid run under
        # §35, not an experimental outcome.
        result.validity = RunValidity(
            valid=False,
            invalid_class=InvalidRunClass.RUNNER_IMPLEMENTATION_FAILURE,
            note=f"{type(exc).__name__}: {exc}",
        )
        result.completion_status = "invalid"
        result.problems.append(f"{type(exc).__name__}: {exc}")
    finally:
        result.sender_transport = human.client.transport_diagnostics()
        await agent_process.stop()
        await human.close()

    _write_evidence(
        result,
        root=root,
        campaign_id=campaign_id,
        campaign_fingerprint=campaign_fingerprint,
        schedule_seed=schedule_seed,
        rate_limit_reference=rate_limit_reference,
        environment_manifest=environment_manifest,
        sync_timeline_limit=sync_timeline_limit,
        sync_timeout_ms=sync_timeout_ms,
        config=config,
    )
    return result


def _write_evidence(
    result: BenchmarkRun,
    *,
    root: Path,
    campaign_id: str,
    campaign_fingerprint: str,
    schedule_seed: int,
    rate_limit_reference: dict[str, Any],
    environment_manifest: str | None,
    sync_timeline_limit: int | None,
    sync_timeout_ms: int,
    config: RunConfig,
) -> None:
    """Flush the buffered interaction records, then write the manifest.

    Records are written here rather than during the run: an fsync per record
    on the completion path would be a measurement artifact that scales with
    throughput. Durability is still per-record from this point on, and the
    stream is immutable once closed.
    """
    workload = result.workload_result
    stream = JsonlStream(result.runner_stream, "runner")
    try:
        if workload is not None:
            for record in workload.records:
                stream.write(record)
    finally:
        stream.close()

    result.agent_transport = _agent_transport_summary(_read_jsonl(result.agent_stream))

    artifacts = []
    if result.runner_stream and result.runner_stream.exists():
        artifacts.append(RawArtifact("runner_interaction_stream", result.runner_stream))
    if result.agent_stream and result.agent_stream.exists():
        artifacts.append(RawArtifact("agent_telemetry_stream", result.agent_stream))

    scheduled = result.scheduled
    extra: dict[str, Any] = {
        # --- automated_experiment_manifest body, §38 --------------------
        "workload_type": scheduled.workload,
        "paired_block_id": scheduled.block_id,
        "block_id": scheduled.block_id,
        "block_index": scheduled.block_index,
        "within_block_order": scheduled.within_block_order,
        "schedule_seed": schedule_seed,
        "campaign_id": campaign_id,
        "campaign_fingerprint": campaign_fingerprint,
        "concurrency": scheduled.concurrency,
        "max_in_flight": scheduled.concurrency,
        "message_body_bytes": config.body_bytes,
        "interaction_timeout_seconds": config.timeout_seconds,
        "sync_configuration": {
            "timeline_limit": sync_timeline_limit,
            "sync_timeout_ms": sync_timeout_ms,
            "long_poll": True,
            "artificial_delay_between_syncs_seconds": 0,
        },
        "rate_limit_configuration_reference": rate_limit_reference,
        "room_membership": sorted(result.membership),
        "room_encryption_enabled": result.encryption_enabled,
        "host_diagnostics": result.host_diagnostics,
        "sender_transport_diagnostics": result.sender_transport,
        "setup_transport_diagnostics": result.setup_transport,
        "agent_transport_diagnostics": result.agent_transport,
        "problems": result.problems,
        "scope_note": (
            "Development E3 run. publication_data is false; these numbers "
            "are implementation validation and are not publication evidence."
        ),
    }

    if scheduled.workload == E3_WORKLOAD_LATENCY:
        extra.update(
            {
                "warmup_interactions": config.warmup_interactions,
                "measured_interactions": config.measured_interactions,
                "window_start_ns": None,
                "window_end_ns": None,
                "drain_end_ns": None,
                "drain_seconds": None,
            }
        )
    else:
        extra.update(
            {
                "warmup_seconds": config.warmup_seconds,
                "measurement_seconds": config.measurement_seconds,
                "drain_seconds": config.drain_seconds,
                "window_start_ns": workload.window_start_ns if workload else None,
                "window_end_ns": workload.window_end_ns if workload else None,
                "drain_end_ns": workload.drain_end_ns if workload else None,
                "outstanding_at_window_start": (
                    workload.outstanding_at_window_start if workload else 0
                ),
                "outstanding_at_window_end": (
                    workload.outstanding_at_window_end if workload else 0
                ),
            }
        )

    if workload is not None:
        extra.update(
            {
                "interactions_initiated": workload.initiated,
                "send_errors": workload.send_errors,
                "rate_limited_sends": workload.rate_limited_sends,
                "send_errcodes": sorted(set(workload.rate_limit_errcodes)),
            }
        )

    manifest = RunManifest(
        experiment=EXPERIMENT,
        run_id=result.run_id,
        room_id=result.room_id,
        participants=result.topology.participants,
        topology=result.topology.name,
        publication_data=publication_data(),
        protocol_git_commit=protocol_git_commit(),
        environment_manifest=environment_manifest,
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        completion_status=result.completion_status,
        validity=result.validity,
        artifacts=artifacts,
        results_root=root,
        extra=extra,
    )
    result.manifest_path = manifest.write(root / "manifests")


def artifact_digests(result: BenchmarkRun) -> dict[str, str]:
    digests: dict[str, str] = {}
    for role, path in (
        ("runner_interaction_stream", result.runner_stream),
        ("agent_telemetry_stream", result.agent_stream),
    ):
        if path is not None and path.exists():
            digests[role] = file_sha256(path)
    return digests


def room_version_ok(result: BenchmarkRun) -> bool:
    return result.room_version == ROOM_VERSION and not result.encryption_enabled
