#!/usr/bin/env python3
"""E3 readiness — live gap recovery under bounded-concurrency stress.

Task 03 found that startup recovery handled limited timelines but the live
sync loop did not. Synapse truncates a sync timeline at 10 events even with no
filter, so a dense workload can produce one at any moment; an unhandled
limited timeline silently drops persisted events. Under E3 at C=32 that loss
would be misread as federation or workload behaviour.

This validates that the corrected live loop does not lose or duplicate
controlled requests when the timeline becomes limited.

It is NOT a performance experiment. No latency, no throughput, no comparison.
Completion counts here are correctness evidence, never rates.

publication_data = false. Readiness runs are not E3 runs.
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
    resolve_results_dir,
)
from fam.common.validity import VALID, InteractionOutcome, InvalidRun  # noqa: E402
from fam.instrumentation.manifest import RawArtifact, RunManifest  # noqa: E402
from fam.instrumentation.streams import JsonlStream, runner_record  # noqa: E402
from fam.matrix.rooms import assert_frozen_room_configuration  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E3READINESS"
TOPOLOGY = "federated"

HUMAN_A = "@human-a:hs-a.test"
AGENT = "@agent:hs-b.test"

RUNS = 3
#: Development parameters, not frozen E3 measurement parameters.
REQUEST_COUNT = int(os.environ.get("FAM_READINESS_REQUESTS", "500"))
MAX_IN_FLIGHT = int(os.environ.get("FAM_READINESS_CONCURRENCY", "32"))
#: Small enough to make limited live timelines certain. Task 04 does not
#: redefine the E3 formal sync configuration.
DEV_TIMELINE_LIMIT = int(os.environ.get("FAM_READINESS_TIMELINE_LIMIT", "10"))

REQUEST_TIMEOUT_SECONDS = 120.0
DRAIN_SECONDS = 20.0
ANALYSIS_CODE_COMMIT = "task-04-working-tree"


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
    sent_event_ids: set[str] = field(default_factory=set)
    processed_event_ids: set[str] = field(default_factory=set)
    acked_correlations: set[int] = field(default_factory=set)
    send_failures: int = 0
    ack_count: int = 0
    duplicate_acks: int = 0
    agent: dict = field(default_factory=dict)
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    runner_stream: Path | None = None
    agent_stream: Path | None = None
    artifact_path: Path | None = None

    @property
    def missing_processed(self) -> set[str]:
        return self.sent_event_ids - self.processed_event_ids

    @property
    def unexpected_processed(self) -> set[str]:
        return self.processed_event_ids - self.sent_event_ids


async def execute_run(index: int, root: Path, stamp: str) -> RunResult:
    run_id = f"e3ready-{stamp}-{index:02d}"
    result = RunResult(run_id=run_id)

    # Deliberately NOT under raw/e3/: readiness is development validation,
    # not E3 data, and must not sit inside the tree a reader would treat as
    # E3 evidence (Task 04 §29).
    raw = root / "raw" / "readiness"
    raw.mkdir(parents=True, exist_ok=True)
    runner_path = raw / f"{run_id}.runner.jsonl"
    agent_path = raw / f"{run_id}.agent.jsonl"
    result.runner_stream = runner_path
    result.agent_stream = agent_path

    state_dir = agent_state_dir() / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    account_a = account(HUMAN_A)
    account_agent = account(AGENT)

    human = HumanParticipant(
        homeserver_url=account_a.homeserver_url,
        user_id=HUMAN_A,
        password=account_a.password,
        device_name="fam-readiness-sender",
    )
    # The sender observes ACKs through the same truncatable sync, so it needs
    # the same gap handling; otherwise a lost ACK would look like a lost
    # request and the readiness result would be measuring the wrong thing.
    human.client.timeline_limit = DEV_TIMELINE_LIMIT

    agent_process = AgentProcess(
        user_id=AGENT,
        password=account_agent.password,
        homeserver=account_agent.homeserver_url,
        experiment=EXPERIMENT,
        run_id=run_id,
        room_id="",
        telemetry=agent_path,
        state_dir=state_dir,
        timeline_limit=DEV_TIMELINE_LIMIT,
    )

    stream = JsonlStream(runner_path, "runner")
    try:
        await human.start()
        human.client.tracked_rooms = set()

        room_id = await human.client.create_room(
            name=f"FAM E3 readiness {run_id}", invite=[AGENT]
        )
        result.room_id = room_id
        human.bind_room(room_id)
        human.client.tracked_rooms = {room_id}
        agent_process.room_id = room_id
        await assert_frozen_room_configuration(human.client, room_id)

        await agent_process.start()
        await asyncio.sleep(3.0)

        semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)
        results: list = [None] * REQUEST_COUNT

        async def one(sequence: int) -> None:
            correlation = Correlation(EXPERIMENT, run_id, sequence)
            async with semaphore:
                try:
                    interaction = await human.request(
                        correlation, timeout=REQUEST_TIMEOUT_SECONDS
                    )
                except Exception as exc:  # noqa: BLE001
                    result.send_failures += 1
                    results[sequence - 1] = ("error", correlation, str(exc))
                    return
            results[sequence - 1] = ("ok", correlation, interaction)

        await asyncio.gather(*(one(i) for i in range(1, REQUEST_COUNT + 1)))
        await asyncio.sleep(DRAIN_SECONDS)

        for entry in results:
            if entry is None:
                continue
            status, correlation, payload = entry
            if status == "error":
                stream.write(
                    runner_record(
                        experiment=EXPERIMENT, topology=TOPOLOGY, run_id=run_id,
                        sequence_id=correlation.sequence_id, run_phase="stress",
                        request_class="readiness", room_id=room_id, sender=HUMAN_A,
                        receiver_role="federated_agent",
                        request_txn_id=correlation.txn_id("request"),
                        request_event_id=None,
                        response_txn_id=correlation.txn_id("response"),
                        response_event_id=None,
                        initiated_monotonic_ns=0, completed_monotonic_ns=None,
                        outcome=InteractionOutcome.SEND_ERROR.value, note=payload,
                    )
                )
                continue
            interaction = payload
            if interaction.request_event_id:
                result.sent_event_ids.add(interaction.request_event_id)
            if interaction.completed_monotonic_ns is not None:
                result.ack_count += 1
                result.acked_correlations.add(correlation.sequence_id)
            stream.write(
                runner_record(
                    experiment=EXPERIMENT, topology=TOPOLOGY, run_id=run_id,
                    sequence_id=correlation.sequence_id, run_phase="stress",
                    request_class="readiness", room_id=room_id, sender=HUMAN_A,
                    receiver_role="federated_agent",
                    request_txn_id=interaction.request_txn_id,
                    request_event_id=interaction.request_event_id,
                    response_txn_id=correlation.txn_id("response"),
                    response_event_id=interaction.response_event_id,
                    initiated_monotonic_ns=interaction.initiated_monotonic_ns,
                    completed_monotonic_ns=interaction.completed_monotonic_ns,
                    outcome=(
                        InteractionOutcome.SUCCESS.value
                        if interaction.completed_monotonic_ns
                        else InteractionOutcome.TIMEOUT.value
                    ),
                )
            )
        result.duplicate_acks = human.duplicate_acks()

    finally:
        await agent_process.stop()
        await human.close()
        stream.close()

    result.agent = _agent_summary(read_jsonl(agent_path))
    result.processed_event_ids = set(result.agent.get("processed_event_ids", []))
    _evaluate(result)
    return result


def _agent_summary(telemetry: list[dict]) -> dict:
    responded = [r for r in telemetry if r.get("action") == "responded"]
    episodes = [r for r in telemetry if r.get("action") == "live_recovery_complete"]
    failures = [r for r in telemetry if r.get("action") == "live_recovery_failed"]
    summaries = [r for r in telemetry if r.get("action") == "live_sync_summary"]
    final = summaries[-1] if summaries else {}
    return {
        "processed_event_ids": sorted(
            {r["request_event_id"] for r in responded if r.get("request_event_id")}
        ),
        "responded_count": len(responded),
        "distinct_responded_sequences": len(
            {r.get("sequence_id") for r in responded if r.get("sequence_id") is not None}
        ),
        "live_limited_syncs": final.get("live_limited_syncs", len(episodes)),
        "live_recovery_episodes": final.get("live_recovery_episodes", len(episodes)),
        "live_history_pages_fetched": final.get(
            "live_history_pages_fetched",
            sum(e.get("history_pages_fetched", 0) for e in episodes),
        ),
        "live_duplicate_observations": final.get(
            "live_duplicate_observations",
            sum(e.get("duplicate_observations", 0) for e in episodes),
        ),
        "live_recovery_failures": final.get("live_recovery_failures", len(failures)),
        "checkpoint_commits": final.get("checkpoint_commits", 0),
        "recovered_from_history_total": sum(
            e.get("recovered_from_history", 0) for e in episodes
        ),
    }


def _evaluate(result: RunResult) -> None:
    reasons: list[str] = []
    agent = result.agent

    if result.send_failures:
        reasons.append(f"{result.send_failures} sends failed")
    if len(result.sent_event_ids) != REQUEST_COUNT:
        reasons.append(
            f"|S_sent| = {len(result.sent_event_ids)}, expected {REQUEST_COUNT}"
        )
    if result.missing_processed:
        reasons.append(
            f"{len(result.missing_processed)} sent requests never logically processed"
        )
    if result.unexpected_processed:
        reasons.append(
            f"{len(result.unexpected_processed)} unexpected requests processed"
        )
    if agent.get("responded_count") != REQUEST_COUNT:
        reasons.append(
            f"{agent.get('responded_count')} ACKs emitted, expected {REQUEST_COUNT}"
        )
    if agent.get("distinct_responded_sequences") != REQUEST_COUNT:
        reasons.append(
            f"{agent.get('distinct_responded_sequences')} distinct sequences "
            f"answered, expected {REQUEST_COUNT} (duplicate logical processing)"
        )
    if result.ack_count != REQUEST_COUNT:
        reasons.append(
            f"{result.ack_count}/{REQUEST_COUNT} ACKs observed by the sender"
        )
    if len(result.acked_correlations) != REQUEST_COUNT:
        reasons.append("ACKs are not one-to-one with requests")
    if result.duplicate_acks:
        reasons.append(f"{result.duplicate_acks} duplicate ACKs")
    if agent.get("live_recovery_failures"):
        reasons.append(f"{agent.get('live_recovery_failures')} live recovery failures")

    # The readiness criterion is behavioural: the path must actually run.
    if not agent.get("live_limited_syncs"):
        reasons.append(
            "no live sync was limited; the live recovery path was not exercised"
        )
    if not agent.get("recovered_from_history_total"):
        reasons.append(
            "history pagination contributed no events; recovery was decorative"
        )

    result.reasons = reasons
    result.passed = not reasons


def _write_artifacts(result: RunResult, root: Path) -> None:
    artifacts = []
    if result.runner_stream and result.runner_stream.exists():
        artifacts.append(RawArtifact("runner_interaction_stream", result.runner_stream))
    if result.agent_stream and result.agent_stream.exists():
        artifacts.append(RawArtifact("agent_telemetry_stream", result.agent_stream))

    agent = result.agent
    payload = {
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": protocol_git_commit(),
        "source_run_id": result.run_id,
        "source_raw_digests": {
            artifact.role: file_sha256(artifact.path) for artifact in artifacts
        },
        "run_id": result.run_id,
        "room_id": result.room_id,
        "publication_data": publication_data(),
        "request_count": REQUEST_COUNT,
        "max_in_flight": MAX_IN_FLIGHT,
        "development_timeline_limit": DEV_TIMELINE_LIMIT,
        "limited_sync_count": agent.get("live_limited_syncs"),
        "recovery_episode_count": agent.get("live_recovery_episodes"),
        "history_pages_fetched": agent.get("live_history_pages_fetched"),
        "events_recovered_via_history": agent.get("recovered_from_history_total"),
        "sent_event_ids": sorted(result.sent_event_ids),
        "processed_event_ids": sorted(result.processed_event_ids),
        "missing_processed": sorted(result.missing_processed),
        "unexpected_processed": sorted(result.unexpected_processed),
        "duplicate_observation_count": agent.get("live_duplicate_observations"),
        "duplicate_processing_count": max(
            0, (agent.get("responded_count") or 0) - len(result.processed_event_ids)
        ),
        "duplicate_ack_count": result.duplicate_acks,
        "ack_count": result.ack_count,
        "checkpoint_commit_count": agent.get("checkpoint_commits"),
        "recovery_failure_count": agent.get("live_recovery_failures"),
        "overall_result": "PASS" if result.passed else "FAIL",
        "acceptance_failures": result.reasons,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope_note": (
            "Transport readiness only. Completion counts are correctness "
            "evidence, not throughput. No latency, throughput or comparative "
            "performance claim is made or derivable from this artifact."
        ),
    }
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    path = processed / f"{result.run_id}.readiness.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    result.artifact_path = path

    RunManifest(
        experiment=EXPERIMENT,
        run_id=result.run_id,
        room_id=result.room_id,
        participants={"sender": HUMAN_A, "agent": AGENT},
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
            "request_count": REQUEST_COUNT,
            "max_in_flight": MAX_IN_FLIGHT,
            "development_timeline_limit": DEV_TIMELINE_LIMIT,
            "limited_sync_count": agent.get("live_limited_syncs"),
            "recovery_episode_count": agent.get("live_recovery_episodes"),
            "history_pages_fetched": agent.get("live_history_pages_fetched"),
            "events_recovered_via_history": agent.get("recovered_from_history_total"),
            "sent_count": len(result.sent_event_ids),
            "processed_count": len(result.processed_event_ids),
            "missing_processed": sorted(result.missing_processed),
            "unexpected_processed": sorted(result.unexpected_processed),
            "duplicate_observations": agent.get("live_duplicate_observations"),
            "duplicate_acks": result.duplicate_acks,
            "ack_count": result.ack_count,
            "checkpoint_commits": agent.get("checkpoint_commits"),
            "recovery_failures": agent.get("live_recovery_failures"),
            "readiness_artifact": path.relative_to(root).as_posix(),
            "acceptance_failures": result.reasons,
            "not_a_performance_measurement": True,
        },
    ).write(manifests_dir(root))


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"E3 readiness — live gap recovery under stress ({RUNS} runs)")
    print(
        f"requests={REQUEST_COUNT}  max_in_flight={MAX_IN_FLIGHT}  "
        f"dev timeline_limit={DEV_TIMELINE_LIMIT}"
    )
    print("transport readiness only — no latency, throughput or comparison\n")

    results: list[RunResult] = []
    for index in range(1, RUNS + 1):
        print(f"--- readiness run {index}/{RUNS} ---")
        try:
            result = await execute_run(index, root, stamp)
        except InvalidRun as exc:
            print(f"  INVALID RUN: {exc}")
            return 2
        results.append(result)
        _write_artifacts(result, root)
        agent = result.agent
        print(f"  run_id              {result.run_id}")
        print(f"  room                {result.room_id}")
        print(f"  requests sent       {len(result.sent_event_ids)}/{REQUEST_COUNT}")
        print(
            f"  limited live syncs  {agent.get('live_limited_syncs')} "
            f"(episodes={agent.get('live_recovery_episodes')}, "
            f"pages={agent.get('live_history_pages_fetched')}, "
            f"via history={agent.get('recovered_from_history_total')})"
        )
        print(
            f"  processed / ACKs    {len(result.processed_event_ids)} / "
            f"{result.ack_count}  dup_obs={agent.get('live_duplicate_observations')} "
            f"dup_ack={result.duplicate_acks}"
        )
        print(
            f"  missing/unexpected  {len(result.missing_processed)}/"
            f"{len(result.unexpected_processed)}"
        )
        print(
            f"  checkpoint commits  {agent.get('checkpoint_commits')}  "
            f"recovery failures={agent.get('live_recovery_failures')}"
        )
        print(f"  {'PASS' if result.passed else 'FAIL'}")
        for reason in result.reasons:
            print(f"    ! {reason}")
        print()

    passed = sum(1 for item in results if item.passed)
    print(f"E3 readiness: {passed}/{len(results)} runs PASS")
    return 0 if passed == RUNS else 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
