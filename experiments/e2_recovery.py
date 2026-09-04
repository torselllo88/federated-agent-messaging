#!/usr/bin/env python3
"""E2 — Autonomous Runtime Interruption and Recovery.

Demonstrates the separation between persistent interaction state and transient
autonomous runtime state (experimental-protocol.md §16). The homeservers stay
up throughout; only the agent process disappears.

Topology is E1's federated three-participant room, per the frozen protocol.
Human A on Domain A sends the 100 offline requests, so they also cross the
federation boundary.

What is deliberately NOT tested: homeserver shutdown, database loss, federation
partition, and cold restart with no retained transport checkpoint. The agent
keeps its checkpoint; recovery from total local-state loss is out of scope and
stays visible as a limitation.

Task 03 runs are development runs: publication_data is false.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/src")

from fam.agent.supervisor import AgentProcess  # noqa: E402
from fam.common.digests import file_sha256  # noqa: E402
from fam.common.env import (  # noqa: E402
    account,
    agent_state_dir,
    protocol_git_commit,
    publication_data,
)
from fam.common.frozen import EXECUTION_ANALYSIS_SPEC_VERSION  # noqa: E402
from fam.common.message import Correlation  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    manifests_dir,
    raw_dir,
    resolve_results_dir,
)
from fam.common.validity import VALID, InteractionOutcome, InvalidRun  # noqa: E402
from fam.instrumentation.manifest import RawArtifact, RunManifest  # noqa: E402
from fam.instrumentation.streams import (  # noqa: E402
    JsonlStream,
    monotonic_ns,
    runner_marker,
    runner_record,
)
from fam.matrix.rooms import assert_frozen_room_configuration  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E2"
TOPOLOGY = "federated"

HUMAN_A = "@human-a:hs-a.test"
HUMAN_B = "@human-b:hs-b.test"
AGENT = "@agent:hs-b.test"

#: experimental-protocol.md §16
E2_RUNS = 3
OFFLINE_REQUESTS = 100

#: Selected by the development pilot (scripts/e2_pilot.py) and held fixed for
#: all three runs. Must be below OFFLINE_REQUESTS so the post-restart sync
#: cannot return everything and skip the recovery branch.
TIMELINE_LIMIT = int(os.environ.get("FAM_E2_TIMELINE_LIMIT", "10"))

#: The response deadline begins with the restart and recovery phase, never at
#: offline-send time (experimental-protocol.md §11).
RECOVERY_DEADLINE_SECONDS = 180.0

ANALYSIS_CODE_COMMIT = "task-03-working-tree"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@dataclass
class RunResult:
    run_id: str
    room_id: str = ""
    room_version: str = ""
    encryption_enabled: bool = True
    sent_event_ids: set[str] = field(default_factory=set)
    send_failures: list[str] = field(default_factory=list)
    identity_before: str = ""
    identity_after: str = ""
    checkpoint_resumed: bool = False
    membership: list[str] = field(default_factory=list)
    ack_count: int = 0
    duplicate_acks: int = 0
    recovery: dict = field(default_factory=dict)
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    runner_stream: Path | None = None
    agent_stream: Path | None = None
    comparison_path: Path | None = None

    @property
    def recovered_event_ids(self) -> set[str]:
        return set(self.recovery.get("recovered_event_ids", []))

    @property
    def missing(self) -> set[str]:
        return self.sent_event_ids - self.recovered_event_ids

    @property
    def unexpected(self) -> set[str]:
        return self.recovered_event_ids - self.sent_event_ids


async def execute_run(index: int, root: Path, stamp: str) -> RunResult:
    run_id = f"e2-{stamp}-{index:02d}"
    result = RunResult(run_id=run_id)

    raw = raw_dir(root, "e2")
    runner_path = raw / f"{run_id}.runner.jsonl"
    agent_path = raw / f"{run_id}.agent.jsonl"
    result.runner_stream = runner_path
    result.agent_stream = agent_path

    state_dir = agent_state_dir() / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    account_a = account(HUMAN_A)
    account_b = account(HUMAN_B)
    account_agent = account(AGENT)

    human_a = HumanParticipant(
        homeserver_url=account_a.homeserver_url,
        user_id=HUMAN_A,
        password=account_a.password,
        device_name="fam-human-a",
    )
    human_b = HumanParticipant(
        homeserver_url=account_b.homeserver_url,
        user_id=HUMAN_B,
        password=account_b.password,
        device_name="fam-human-b",
    )
    agent_process = AgentProcess(
        user_id=AGENT,
        password=account_agent.password,
        homeserver=account_agent.homeserver_url,
        experiment=EXPERIMENT,
        run_id=run_id,
        room_id="",
        telemetry=agent_path,
        state_dir=state_dir,
        timeline_limit=TIMELINE_LIMIT,
    )

    stream = JsonlStream(runner_path, "runner")
    correlations: list[Correlation] = []
    try:
        await human_a.start()
        await human_b.start()

        # 1-2. fresh federated room, frozen configuration asserted
        room_id = await human_a.client.create_room(
            name=f"FAM E2 {run_id}", invite=[HUMAN_B, AGENT]
        )
        result.room_id = room_id
        human_a.bind_room(room_id)
        human_b.bind_room(room_id)
        agent_process.room_id = room_id
        result.room_version, result.encryption_enabled = (
            await assert_frozen_room_configuration(human_a.client, room_id)
        )

        # 3. frozen E2 topology
        await human_b.client.join(room_id)

        # 4-6. agent synchronizes and persists its transport checkpoint
        await agent_process.start()
        await asyncio.sleep(3.0)
        result.membership = await human_a.client.joined_members(room_id)
        telemetry = read_jsonl(agent_path)
        result.identity_before = _identity(telemetry)

        # 7-8. stop only the agent runtime; homeservers stay up
        await agent_process.stop()
        stream.write(
            runner_marker(
                experiment=EXPERIMENT,
                run_id=run_id,
                marker="agent_runtime_stopped",
                room_id=room_id,
                monotonic_ns=monotonic_ns(),
                note="homeserver infrastructure remains running",
            )
        )
        healthy = await _homeservers_healthy(human_a, human_b, room_id)
        stream.write(
            runner_marker(
                experiment=EXPERIMENT,
                run_id=run_id,
                marker="infrastructure_check",
                homeservers_responsive_while_agent_absent=healthy,
            )
        )

        # 9-12. exactly 100 offline sends, each returning an event_id
        for sequence in range(1, OFFLINE_REQUESTS + 1):
            correlation = Correlation(EXPERIMENT, run_id, sequence)
            correlations.append(correlation)
            try:
                interaction = await human_a.send_offline(correlation)
            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                result.send_failures.append(f"seq {sequence}: {exc}")
                stream.write(
                    runner_record(
                        experiment=EXPERIMENT,
                        topology=TOPOLOGY,
                        run_id=run_id,
                        sequence_id=sequence,
                        run_phase="offline",
                        request_class="offline_send",
                        room_id=room_id,
                        sender=HUMAN_A,
                        receiver_role="federated_agent",
                        request_txn_id=correlation.txn_id("request"),
                        request_event_id=None,
                        response_txn_id=correlation.txn_id("response"),
                        response_event_id=None,
                        initiated_monotonic_ns=monotonic_ns(),
                        completed_monotonic_ns=None,
                        outcome=InteractionOutcome.SEND_ERROR.value,
                        note=str(exc),
                    )
                )
                continue
            if interaction.request_event_id:
                result.sent_event_ids.add(interaction.request_event_id)
            stream.write(
                runner_record(
                    experiment=EXPERIMENT,
                    topology=TOPOLOGY,
                    run_id=run_id,
                    sequence_id=sequence,
                    run_phase="offline",
                    request_class="offline_send",
                    room_id=room_id,
                    sender=HUMAN_A,
                    receiver_role="federated_agent",
                    request_txn_id=interaction.request_txn_id,
                    request_event_id=interaction.request_event_id,
                    response_txn_id=correlation.txn_id("response"),
                    response_event_id=None,
                    initiated_monotonic_ns=interaction.initiated_monotonic_ns,
                    completed_monotonic_ns=None,
                    # Not a timeout: no runtime exists yet by design. The
                    # deadline begins at restart (experimental-protocol.md §11).
                    outcome=InteractionOutcome.OFFLINE_SEND.value,
                )
            )

        # 13. restart the same identity from the saved checkpoint
        restart_ns = monotonic_ns()
        stream.write(
            runner_marker(
                experiment=EXPERIMENT,
                run_id=run_id,
                marker="agent_restart_and_recovery_begins",
                monotonic_ns=restart_ns,
                note="offline-send response deadline starts here",
            )
        )
        await agent_process.start()

        # 14-20. recovery happens inside the runtime; the runner only waits
        result.ack_count = await human_a.await_acks(
            correlations, timeout=RECOVERY_DEADLINE_SECONDS
        )
        await asyncio.sleep(3.0)
        result.duplicate_acks = human_a.duplicate_acks()

        for correlation in correlations:
            interaction = human_a.interaction(correlation)
            if interaction is None:
                continue
            stream.write(
                runner_record(
                    experiment=EXPERIMENT,
                    topology=TOPOLOGY,
                    run_id=run_id,
                    sequence_id=correlation.sequence_id,
                    run_phase="post_restart",
                    request_class="offline_send",
                    room_id=room_id,
                    sender=HUMAN_A,
                    receiver_role="federated_agent",
                    request_txn_id=interaction.request_txn_id,
                    request_event_id=interaction.request_event_id,
                    response_txn_id=correlation.txn_id("response"),
                    response_event_id=interaction.response_event_id,
                    initiated_monotonic_ns=restart_ns,
                    completed_monotonic_ns=interaction.completed_monotonic_ns,
                    outcome=(
                        InteractionOutcome.SUCCESS.value
                        if interaction.completed_monotonic_ns
                        else InteractionOutcome.TIMEOUT.value
                    ),
                )
            )

        telemetry = read_jsonl(agent_path)
        result.identity_after = _identity(telemetry, last=True)
        result.checkpoint_resumed = _checkpoint_resumed(telemetry)
        result.recovery = _recovery_summary(telemetry)

    finally:
        await agent_process.stop()
        await human_a.close()
        await human_b.close()
        stream.close()

    _evaluate(result)
    return result


async def _homeservers_healthy(human_a, human_b, room_id: str) -> bool:
    """Both domains still serve ordinary clients while the agent is gone."""
    try:
        await human_a.client.joined_members(room_id)
        await human_b.client.joined_members(room_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def _identity(telemetry: list[dict], last: bool = False) -> str:
    items = [r for r in telemetry if r.get("action") == "connected"]
    if not items:
        return ""
    return str((items[-1] if last else items[0]).get("agent_mxid", ""))


def _checkpoint_resumed(telemetry: list[dict]) -> bool:
    loads = [r for r in telemetry if r.get("action") == "checkpoint_loaded"]
    return bool(loads) and bool(loads[-1].get("resumed_from_checkpoint"))


def _recovery_summary(telemetry: list[dict]) -> dict:
    completes = [r for r in telemetry if r.get("action") == "recovery_complete"]
    syncs = [r for r in telemetry if r.get("action") == "post_restart_sync"]
    finals = [
        r for r in telemetry if r.get("action") == "recovery_processing_complete"
    ]
    responded = [r for r in telemetry if r.get("action") == "responded"]
    summary = dict(completes[-1]) if completes else {}
    if syncs:
        summary["requests_directly_in_sync"] = syncs[-1].get(
            "requests_directly_in_sync", 0
        )
        summary["sync_limited"] = syncs[-1].get("timeline_limited", False)
    if finals:
        summary["logically_processed"] = finals[-1].get("logically_processed", 0)
    summary["responded_count"] = len(responded)
    summary["distinct_responded_sequences"] = len(
        {r.get("sequence_id") for r in responded if r.get("sequence_id") is not None}
    )
    return summary


def _evaluate(result: RunResult) -> None:
    """Acceptance criteria, experimental-protocol.md §16 and Task 03 §22."""
    reasons: list[str] = []
    recovery = result.recovery

    if result.send_failures:
        reasons.append(f"{len(result.send_failures)} offline sends failed")
    if len(result.sent_event_ids) != OFFLINE_REQUESTS:
        reasons.append(
            f"|S_sent| = {len(result.sent_event_ids)}, expected {OFFLINE_REQUESTS}"
        )
    if result.room_version != "12":
        reasons.append(f"room version {result.room_version!r}")
    if result.encryption_enabled:
        reasons.append("room encryption enabled")
    if not result.identity_before or result.identity_before != result.identity_after:
        reasons.append(
            f"agent identity changed across restart: "
            f"{result.identity_before!r} -> {result.identity_after!r}"
        )
    if not result.checkpoint_resumed:
        reasons.append("restarted runtime did not resume from the saved checkpoint")
    if not recovery.get("sync_limited"):
        reasons.append(
            "post-restart sync was not limited; the recovery path was not exercised"
        )
    if not recovery.get("pagination_invoked"):
        reasons.append("history pagination was not invoked")
    if result.missing:
        reasons.append(f"{len(result.missing)} sent events missing from recovery")
    if result.unexpected:
        reasons.append(f"{len(result.unexpected)} unexpected events in recovery")
    if recovery.get("logically_processed") != OFFLINE_REQUESTS:
        reasons.append(
            f"{recovery.get('logically_processed')} logical processing operations, "
            f"expected {OFFLINE_REQUESTS}"
        )
    if recovery.get("distinct_responded_sequences") != OFFLINE_REQUESTS:
        reasons.append(
            f"{recovery.get('distinct_responded_sequences')} distinct sequences "
            f"responded to, expected {OFFLINE_REQUESTS}"
        )
    if recovery.get("responded_count") != OFFLINE_REQUESTS:
        reasons.append(
            f"{recovery.get('responded_count')} ACKs sent, expected {OFFLINE_REQUESTS}"
        )
    if result.ack_count != OFFLINE_REQUESTS:
        reasons.append(
            f"{result.ack_count}/{OFFLINE_REQUESTS} ACKs observed by the sender"
        )
    if result.duplicate_acks:
        reasons.append(f"{result.duplicate_acks} duplicate ACKs")

    result.reasons = reasons
    result.passed = not reasons


def _write_artifacts(result: RunResult, root: Path) -> None:
    artifacts = []
    if result.runner_stream and result.runner_stream.exists():
        artifacts.append(RawArtifact("runner_interaction_stream", result.runner_stream))
    if result.agent_stream and result.agent_stream.exists():
        artifacts.append(RawArtifact("agent_telemetry_stream", result.agent_stream))

    recovery = result.recovery
    provenance = {
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": protocol_git_commit(),
        "source_run_id": result.run_id,
        "source_raw_digests": {
            artifact.role: file_sha256(artifact.path) for artifact in artifacts
        },
    }
    comparison = {
        **provenance,
        "run_id": result.run_id,
        "room_id": result.room_id,
        "offline_request_count": OFFLINE_REQUESTS,
        "timeline_limit": TIMELINE_LIMIT,
        "sync_limited": recovery.get("sync_limited"),
        "sent_event_ids": sorted(result.sent_event_ids),
        "sync_event_ids": sorted(recovery.get("sync_event_ids", [])),
        "history_recovered_event_ids": sorted(recovery.get("history_event_ids", [])),
        "recovered_event_ids": sorted(result.recovered_event_ids),
        "missing_from_recovery": sorted(result.missing),
        "unexpected_in_recovery": sorted(result.unexpected),
        "recovered_from_sync_count": recovery.get("recovered_from_sync"),
        "recovered_from_history_count": recovery.get("recovered_from_history"),
        "duplicate_observation_count": recovery.get("duplicate_observations"),
        "duplicate_processing_count": max(
            0, (recovery.get("logically_processed") or 0) - len(result.recovered_event_ids)
        ),
        "logical_request_count": recovery.get("logically_processed"),
        "ack_count": result.ack_count,
        "duplicate_ack_count": result.duplicate_acks,
        "pagination_invoked": recovery.get("pagination_invoked"),
        "history_pages_fetched": recovery.get("history_pages_fetched"),
        "same_agent_identity": result.identity_before == result.identity_after
        and bool(result.identity_before),
        "checkpoint_resumed": result.checkpoint_resumed,
        "overall_result": "PASS" if result.passed else "FAIL",
        "acceptance_failures": result.reasons,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_note": (
            "Recovery from a retained transport checkpoint. Cold restart with "
            "no checkpoint, homeserver failure, database loss and federation "
            "partition are all out of scope and untested."
        ),
    }
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    path = processed / f"{result.run_id}.recovery-comparison.json"
    path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8")
    result.comparison_path = path

    RunManifest(
        experiment=EXPERIMENT,
        run_id=result.run_id,
        room_id=result.room_id,
        participants={"human_a": HUMAN_A, "human_b": HUMAN_B, "agent": AGENT},
        topology=TOPOLOGY,
        publication_data=publication_data(),
        protocol_git_commit=protocol_git_commit(),
        environment_manifest="environment/environment-latest.json",
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        completion_status="pass" if result.passed else "fail",
        validity=VALID,
        artifacts=artifacts,
        results_root=root,
        extra={
            "offline_request_count": OFFLINE_REQUESTS,
            "timeline_limit": TIMELINE_LIMIT,
            "sent_count": len(result.sent_event_ids),
            "recovered_count": len(result.recovered_event_ids),
            "missing_from_recovery": sorted(result.missing),
            "unexpected_in_recovery": sorted(result.unexpected),
            "sync_limited": recovery.get("sync_limited"),
            "pagination_invoked": recovery.get("pagination_invoked"),
            "history_pages_fetched": recovery.get("history_pages_fetched"),
            "recovered_from_sync": recovery.get("recovered_from_sync"),
            "recovered_from_history": recovery.get("recovered_from_history"),
            "duplicate_observations": recovery.get("duplicate_observations"),
            "logically_processed": recovery.get("logically_processed"),
            "ack_count": result.ack_count,
            "duplicate_acks": result.duplicate_acks,
            "agent_identity_before_restart": result.identity_before,
            "agent_identity_after_restart": result.identity_after,
            "checkpoint_resumed": result.checkpoint_resumed,
            "membership": result.membership,
            "recovery_comparison_artifact": path.relative_to(root).as_posix(),
            "acceptance_failures": result.reasons,
        },
    ).write(manifests_dir(root))


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"E2 — Autonomous Runtime Interruption and Recovery ({E2_RUNS} runs)")
    print(f"results: {root}")
    print(f"timeline_limit: {TIMELINE_LIMIT} (< {OFFLINE_REQUESTS} offline requests)")
    print(f"publication_data: {publication_data()}")
    if not publication_data():
        print("development run — not publication evidence\n")

    results: list[RunResult] = []
    for index in range(1, E2_RUNS + 1):
        print(f"--- run {index}/{E2_RUNS} ---")
        try:
            result = await execute_run(index, root, stamp)
        except InvalidRun as exc:
            print(f"  INVALID RUN: {exc}")
            return 2
        results.append(result)
        _write_artifacts(result, root)
        recovery = result.recovery
        print(f"  run_id             {result.run_id}")
        print(f"  room               {result.room_id} (v{result.room_version})")
        print(f"  offline sends      {len(result.sent_event_ids)}/{OFFLINE_REQUESTS}")
        print(
            f"  post-restart sync  limited={recovery.get('sync_limited')} "
            f"direct={recovery.get('requests_directly_in_sync')}"
        )
        print(
            f"  pagination         invoked={recovery.get('pagination_invoked')} "
            f"pages={recovery.get('history_pages_fetched')}"
        )
        print(
            f"  recovered          {len(result.recovered_event_ids)} "
            f"(sync={recovery.get('recovered_from_sync')} "
            f"history={recovery.get('recovered_from_history')} "
            f"dup_obs={recovery.get('duplicate_observations')})"
        )
        print(f"  missing/unexpected {len(result.missing)}/{len(result.unexpected)}")
        print(
            f"  processed / ACKs   {recovery.get('logically_processed')} / "
            f"{result.ack_count}  dup_ack={result.duplicate_acks}"
        )
        print(
            f"  identity           {result.identity_before} -> {result.identity_after} "
            f"(checkpoint resumed={result.checkpoint_resumed})"
        )
        print(f"  {'PASS' if result.passed else 'FAIL'}")
        for reason in result.reasons:
            print(f"    ! {reason}")
        print()

    passed = sum(1 for item in results if item.passed)
    print(f"E2: {passed}/{len(results)} runs PASS")
    return 0 if passed == E2_RUNS else 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
