#!/usr/bin/env python3
"""E0 — Same-Domain Functional Baseline.

Validates persistent non-privileged autonomous participation before federation
is introduced (experimental-protocol.md §14). Three independent runs, each in
a fresh room, each with 40 logical interactions across an agent-runtime
restart.

E0 exercises Domain A only. Nothing here touches federation.

Task 01 runs are development runs: publication_data is false and they are not
publication evidence.
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

from fam.common.env import (  # noqa: E402
    account,
    agent_state_dir,
    protocol_git_commit,
    publication_data,
)
from fam.common.frozen import (  # noqa: E402
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E0_REQUESTS_PER_PHASE,
    E0_RUNS,
    ROOM_VERSION,
)
from fam.common.message import Correlation  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    manifests_dir,
    raw_dir,
    resolve_results_dir,
)
from fam.common.validity import (  # noqa: E402
    VALID,
    InteractionOutcome,
    InvalidRun,
    InvalidRunClass,
    invalid,
)
from fam.agent.supervisor import AgentProcess  # noqa: E402
from fam.instrumentation.manifest import RawArtifact, RunManifest  # noqa: E402
from fam.instrumentation.streams import JsonlStream, runner_record  # noqa: E402
from fam.matrix.rooms import assert_frozen_room_configuration  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E0"
TOPOLOGY = "same-domain"
HUMAN_A = "@human-a:hs-a.test"
AGENT_LOCAL = "@agent-local:hs-a.test"
SETTLE_SECONDS = 2.0


@dataclass
class PhaseResult:
    name: str
    sent: int = 0
    acked: int = 0
    duplicates: int = 0
    timeouts: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: str
    room_id: str = ""
    room_version: str = ""
    encryption_enabled: bool = True
    agent_identity_before: str = ""
    agent_identity_after: str = ""
    membership_before: list[str] = field(default_factory=list)
    membership_after: list[str] = field(default_factory=list)
    membership_while_stopped: list[str] = field(default_factory=list)
    phases: list[PhaseResult] = field(default_factory=list)
    c2_evidence: dict = field(default_factory=dict)
    resumed_from_checkpoint: bool = False
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    runner_stream: Path | None = None
    agent_stream: Path | None = None

    @property
    def total_sent(self) -> int:
        return sum(p.sent for p in self.phases)

    @property
    def total_acked(self) -> int:
        return sum(p.acked for p in self.phases)

    @property
    def total_duplicates(self) -> int:
        return sum(p.duplicates for p in self.phases)


async def run_phase(
    *,
    human: HumanParticipant,
    stream: JsonlStream,
    run_id: str,
    room_id: str,
    phase_name: str,
    first_sequence: int,
) -> PhaseResult:
    result = PhaseResult(name=phase_name)
    for offset in range(E0_REQUESTS_PER_PHASE):
        sequence = first_sequence + offset
        correlation = Correlation(EXPERIMENT, run_id, sequence)
        interaction = await human.request(
            correlation, timeout=DEFAULT_INTERACTION_TIMEOUT_SECONDS
        )
        result.sent += 1
        if interaction.completed_monotonic_ns is None:
            outcome = InteractionOutcome.TIMEOUT
            result.timeouts += 1
            result.failures.append(f"seq {sequence}: no ACK within timeout")
        else:
            outcome = InteractionOutcome.SUCCESS
            result.acked += 1
        stream.write(
            runner_record(
                experiment=EXPERIMENT,
                topology=TOPOLOGY,
                run_id=run_id,
                sequence_id=sequence,
                run_phase=phase_name,
                room_id=room_id,
                sender=HUMAN_A,
                receiver_role="local_agent",
                request_txn_id=interaction.request_txn_id,
                request_event_id=interaction.request_event_id,
                response_txn_id=correlation.txn_id("response"),
                response_event_id=interaction.response_event_id,
                initiated_monotonic_ns=interaction.initiated_monotonic_ns,
                completed_monotonic_ns=interaction.completed_monotonic_ns,
                outcome=outcome.value,
            )
        )
    await asyncio.sleep(SETTLE_SECONDS)
    result.duplicates = human.duplicate_acks()
    return result


def read_agent_telemetry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


async def execute_run(index: int, root: Path, stamp: str) -> RunResult:
    run_id = f"e0-{stamp}-{index:02d}"
    result = RunResult(run_id=run_id)

    raw = raw_dir(root, "e0")
    runner_path = raw / f"{run_id}.runner.jsonl"
    agent_path = raw / f"{run_id}.agent.jsonl"
    result.runner_stream = runner_path
    result.agent_stream = agent_path

    state_dir = agent_state_dir() / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    human_account = account(HUMAN_A)
    agent_account = account(AGENT_LOCAL)

    human = HumanParticipant(
        homeserver_url=human_account.homeserver_url,
        user_id=HUMAN_A,
        password=human_account.password,
    )
    agent_process = AgentProcess(
        user_id=AGENT_LOCAL,
        password=agent_account.password,
        homeserver=agent_account.homeserver_url,
        experiment=EXPERIMENT,
        run_id=run_id,
        room_id="",
        telemetry=agent_path,
        state_dir=state_dir,
    )

    stream = JsonlStream(runner_path, "runner")
    try:
        await human.start()

        # 1. fresh room version 12 room on Domain A
        room_id = await human.client.create_room(
            name=f"FAM E0 {run_id}", invite=[AGENT_LOCAL]
        )
        result.room_id = room_id
        human.bind_room(room_id)
        agent_process.room_id = room_id

        # 2. assert the frozen room configuration (protocol §4.2)
        version, encrypted = await assert_frozen_room_configuration(
            human.client, room_id
        )
        result.room_version = version
        result.encryption_enabled = encrypted

        # 3-4. Human A is the creator and already joined; the agent joins as an
        # ordinary client when its runtime starts.
        await agent_process.start()
        await asyncio.sleep(1.0)
        result.membership_before = await human.client.joined_members(room_id)

        # 5-6. twenty sequential deterministic requests
        pre = await run_phase(
            human=human,
            stream=stream,
            run_id=run_id,
            room_id=room_id,
            phase_name="pre_restart",
            first_sequence=1,
        )
        result.phases.append(pre)

        # 7. record the agent Matrix identity
        telemetry = read_agent_telemetry(agent_path)
        result.agent_identity_before = _agent_identity(telemetry)
        result.c2_evidence = _c2_evidence(telemetry)

        # 8. stop the agent runtime. No administrator operation occurs between
        # the pre-restart and post-restart phases.
        await agent_process.stop()
        result.membership_while_stopped = await human.client.joined_members(room_id)

        # 9-11. restart, resume the same identity, confirm membership
        await agent_process.start()
        await asyncio.sleep(1.0)
        result.membership_after = await human.client.joined_members(room_id)

        telemetry = read_agent_telemetry(agent_path)
        result.agent_identity_after = _agent_identity(telemetry, last=True)
        result.resumed_from_checkpoint = _resumed(telemetry)

        # 12-13. twenty further requests
        post = await run_phase(
            human=human,
            stream=stream,
            run_id=run_id,
            room_id=room_id,
            phase_name="post_restart",
            first_sequence=E0_REQUESTS_PER_PHASE + 1,
        )
        result.phases.append(post)

    finally:
        await agent_process.stop()
        await human.close()
        stream.close()

    _evaluate(result)
    return result


def _agent_identity(telemetry: list[dict], last: bool = False) -> str:
    connects = [r for r in telemetry if r.get("action") == "connected"]
    if not connects:
        return ""
    return str((connects[-1] if last else connects[0]).get("agent_mxid", ""))


def _resumed(telemetry: list[dict]) -> bool:
    connects = [r for r in telemetry if r.get("action") == "connected"]
    if len(connects) < 2:
        return False
    return "resumed from transport checkpoint" in str(connects[-1].get("note", ""))


def _c2_evidence(telemetry: list[dict]) -> dict:
    for record in telemetry:
        if record.get("action") == "privilege_evidence":
            return record.get("c2_evidence", {})
    return {}


def _evaluate(result: RunResult) -> None:
    """Acceptance criteria, experimental-protocol.md §14."""
    reasons: list[str] = []
    expected = E0_REQUESTS_PER_PHASE * 2

    if result.total_sent != expected:
        reasons.append(f"sent {result.total_sent} requests, expected {expected}")
    if result.total_acked != expected:
        reasons.append(
            f"{result.total_acked}/{expected} requests received exactly one ACK"
        )
    if result.total_duplicates:
        reasons.append(f"{result.total_duplicates} duplicate ACKs observed")
    if result.room_version != ROOM_VERSION:
        reasons.append(f"room version {result.room_version!r}")
    if result.encryption_enabled:
        reasons.append("room encryption enabled")
    if not result.agent_identity_before:
        reasons.append("agent identity not observed before restart")
    if result.agent_identity_before != result.agent_identity_after:
        reasons.append(
            f"agent identity changed across restart: "
            f"{result.agent_identity_before!r} -> {result.agent_identity_after!r}"
        )
    if AGENT_LOCAL not in result.membership_after:
        reasons.append("agent membership absent after restart")
    if AGENT_LOCAL not in result.membership_while_stopped:
        reasons.append("agent membership did not survive runtime termination")
    if not result.c2_evidence.get("c2_supporting_evidence_complete"):
        reasons.append("C2 supporting evidence incomplete")

    result.reasons = reasons
    result.passed = not reasons


def _write_manifest(result: RunResult, root: Path) -> Path:
    artifacts = []
    if result.runner_stream and result.runner_stream.exists():
        artifacts.append(RawArtifact("runner_interaction_stream", result.runner_stream))
    if result.agent_stream and result.agent_stream.exists():
        artifacts.append(RawArtifact("agent_telemetry_stream", result.agent_stream))

    manifest = RunManifest(
        experiment=EXPERIMENT,
        run_id=result.run_id,
        room_id=result.room_id,
        participants={"human_a": HUMAN_A, "local_agent": AGENT_LOCAL},
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
            "requests_sent": result.total_sent,
            "requests_acked": result.total_acked,
            "duplicate_acks": result.total_duplicates,
            "phases": [
                {
                    "name": p.name,
                    "sent": p.sent,
                    "acked": p.acked,
                    "timeouts": p.timeouts,
                    "duplicates": p.duplicates,
                }
                for p in result.phases
            ],
            "agent_identity_before_restart": result.agent_identity_before,
            "agent_identity_after_restart": result.agent_identity_after,
            "resumed_from_transport_checkpoint": result.resumed_from_checkpoint,
            "membership_before": result.membership_before,
            "membership_while_agent_stopped": result.membership_while_stopped,
            "membership_after": result.membership_after,
            "c2_evidence": result.c2_evidence,
            "acceptance_failures": result.reasons,
        },
    )
    return manifest.write(manifests_dir(root))


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"E0 — Same-Domain Functional Baseline ({E0_RUNS} independent runs)")
    print(f"results: {root}")
    print(f"publication_data: {publication_data()}")
    if not publication_data():
        print("development run — not publication evidence\n")

    results: list[RunResult] = []
    for index in range(1, E0_RUNS + 1):
        print(f"--- run {index}/{E0_RUNS} ---")
        try:
            result = await execute_run(index, root, stamp)
        except InvalidRun as exc:
            print(f"  INVALID RUN: {exc}")
            print("  classified under the §35 taxonomy; not an experimental failure")
            return 2
        results.append(result)
        _write_manifest(result, root)
        verdict = "PASS" if result.passed else "FAIL"
        print(f"  run_id           {result.run_id}")
        print(f"  room             {result.room_id} (v{result.room_version})")
        print(f"  requests/ACKs    {result.total_acked}/{result.total_sent}")
        print(f"  duplicate ACKs   {result.total_duplicates}")
        print(f"  agent identity   {result.agent_identity_before} -> {result.agent_identity_after}")
        print(f"  resumed session  {result.resumed_from_checkpoint}")
        print(f"  {verdict}")
        for reason in result.reasons:
            print(f"    ! {reason}")
        print()

    passed = sum(1 for r in results if r.passed)
    print(f"E0: {passed}/{len(results)} runs PASS")
    return 0 if passed == E0_RUNS else 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
