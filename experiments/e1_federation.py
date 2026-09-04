#!/usr/bin/env python3
"""E1 — Federated Persistent Multi-Party Interaction.

The first experiment that exercises C5. A persistent three-participant room
spans hs-a.test and hs-b.test over native Matrix Server-Server federation, and
the experimental event set observed independently through each domain must be
exactly equal (experimental-protocol.md §15).

Topology, per the frozen protocol:

    @human-a:hs-a.test    Domain A participant and Domain-A observation point
    @human-b:hs-b.test    Domain B participant and Domain-B observation point
    @agent:hs-b.test      autonomous participant on Domain B

Two request classes are tracked separately and never pooled: Human A's
requests cross the federation boundary, Human B's do not. Pooling them would
let a federated failure hide behind same-domain successes.

Task 02 runs are development runs: publication_data is false and they are not
publication evidence.
"""

from __future__ import annotations

import asyncio
import json
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
from fam.common.frozen import (  # noqa: E402
    DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    E1_QUIET_INTERVAL_SECONDS,
    E1_REQUESTS_PER_CLASS,
    E1_RUNS,
    EXECUTION_ANALYSIS_SPEC_VERSION,
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
)
from fam.instrumentation.federation import FederationComparison  # noqa: E402
from fam.instrumentation.manifest import RawArtifact, RunManifest  # noqa: E402
from fam.instrumentation.streams import JsonlStream, runner_record  # noqa: E402
from fam.matrix.rooms import assert_frozen_room_configuration, collect_domain_view  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E1"
TOPOLOGY = "federated"

HUMAN_A = "@human-a:hs-a.test"
HUMAN_B = "@human-b:hs-b.test"
AGENT = "@agent:hs-b.test"
EXPECTED_MEMBERSHIP = {HUMAN_A, HUMAN_B, AGENT}

CLASS_CROSS_DOMAIN = "cross_domain"
CLASS_SAME_DOMAIN = "same_domain"

SETTLE_SECONDS = 2.0
ANALYSIS_CODE_COMMIT = "task-02-working-tree"


@dataclass
class ClassResult:
    name: str
    sender: str
    sent: int = 0
    acked: int = 0
    timeouts: int = 0
    duplicates: int = 0
    request_event_ids: set[str] = field(default_factory=set)
    ack_event_ids: set[str] = field(default_factory=set)


@dataclass
class RunResult:
    run_id: str
    room_id: str = ""
    room_version: str = ""
    encryption_enabled: bool = True
    classes: dict[str, ClassResult] = field(default_factory=dict)
    comparison: FederationComparison | None = None
    membership_after_join: dict[str, list[str]] = field(default_factory=dict)
    passed: bool = False
    reasons: list[str] = field(default_factory=list)
    runner_stream: Path | None = None
    agent_stream: Path | None = None
    comparison_path: Path | None = None


def read_jsonl(path: Path) -> list[dict]:
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


async def send_class(
    *,
    human: HumanParticipant,
    stream: JsonlStream,
    run_id: str,
    room_id: str,
    request_class: str,
    first_sequence: int,
) -> ClassResult:
    result = ClassResult(name=request_class, sender=human.user_id)
    for offset in range(E1_REQUESTS_PER_CLASS):
        sequence = first_sequence + offset
        correlation = Correlation(EXPERIMENT, run_id, sequence)
        interaction = await human.request(
            correlation, timeout=DEFAULT_INTERACTION_TIMEOUT_SECONDS
        )
        result.sent += 1
        if interaction.request_event_id:
            result.request_event_ids.add(interaction.request_event_id)
        if interaction.completed_monotonic_ns is None:
            outcome = InteractionOutcome.TIMEOUT
            result.timeouts += 1
        else:
            outcome = InteractionOutcome.SUCCESS
            result.acked += 1
            if interaction.response_event_id:
                result.ack_event_ids.add(interaction.response_event_id)
        stream.write(
            runner_record(
                experiment=EXPERIMENT,
                topology=TOPOLOGY,
                run_id=run_id,
                sequence_id=sequence,
                run_phase=request_class,
                request_class=request_class,
                room_id=room_id,
                sender=human.user_id,
                receiver_role="federated_agent",
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


async def execute_run(index: int, root: Path, stamp: str) -> RunResult:
    run_id = f"e1-{stamp}-{index:02d}"
    result = RunResult(run_id=run_id)

    raw = raw_dir(root, "e1")
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
    )

    stream = JsonlStream(runner_path, "runner")
    try:
        await human_a.start()
        await human_b.start()

        # 1. fresh room on Domain A, inviting both Domain B participants
        room_id = await human_a.client.create_room(
            name=f"FAM E1 {run_id}", invite=[HUMAN_B, AGENT]
        )
        result.room_id = room_id
        human_a.bind_room(room_id)
        human_b.bind_room(room_id)
        agent_process.room_id = room_id

        # 2. assert the frozen room configuration (protocol §4.2)
        version, encrypted = await assert_frozen_room_configuration(
            human_a.client, room_id
        )
        result.room_version = version
        result.encryption_enabled = encrypted

        # 3-5. Human A is the creator. Human B and the agent join from Domain B
        # through ordinary Matrix APIs over native federation. This join is the
        # first room-level federation operation in the whole study.
        await human_b.client.join(room_id)
        await agent_process.start()
        await asyncio.sleep(2.0)

        # 6. three-participant membership through ordinary views on both sides
        result.membership_after_join = {
            "A": await human_a.client.joined_members(room_id),
            "B": await human_b.client.joined_members(room_id),
        }

        # 7-9. cross-domain class: Human A (hs-a) -> agent (hs-b)
        result.classes[CLASS_CROSS_DOMAIN] = await send_class(
            human=human_a,
            stream=stream,
            run_id=run_id,
            room_id=room_id,
            request_class=CLASS_CROSS_DOMAIN,
            first_sequence=1,
        )

        # 10-11. same-domain class: Human B (hs-b) -> agent (hs-b)
        result.classes[CLASS_SAME_DOMAIN] = await send_class(
            human=human_b,
            stream=stream,
            run_id=run_id,
            room_id=room_id,
            request_class=CLASS_SAME_DOMAIN,
            first_sequence=E1_REQUESTS_PER_CLASS + 1,
        )

        # ACK event ids come from the agent's own send responses: the homeserver
        # that created the event told the sender its id.
        telemetry = read_jsonl(agent_path)
        ack_ids = {
            record["response_event_id"]
            for record in telemetry
            if record.get("action") == "responded" and record.get("response_event_id")
        }

        cross = result.classes[CLASS_CROSS_DOMAIN]
        same = result.classes[CLASS_SAME_DOMAIN]
        comparison = FederationComparison(
            run_id=run_id,
            room_id=room_id,
            quiet_interval_seconds=E1_QUIET_INTERVAL_SECONDS,
            expected_request_ids_a=set(cross.request_event_ids),
            expected_request_ids_b=set(same.request_event_ids),
            expected_ack_ids=set(ack_ids),
            expected_membership=set(EXPECTED_MEMBERSHIP),
        )
        comparison.expected_event_ids = (
            comparison.expected_request_ids_a
            | comparison.expected_request_ids_b
            | comparison.expected_ack_ids
        )

        # 16. frozen quiet interval, then the final comparisons
        await asyncio.sleep(E1_QUIET_INTERVAL_SECONDS)

        # 14-15. independent domain views through ordinary participant accounts
        comparison.view_a = await collect_domain_view(
            human_a.client,
            domain="A",
            room_id=room_id,
            experiment=EXPERIMENT,
            run_id=run_id,
        )
        comparison.view_b = await collect_domain_view(
            human_b.client,
            domain="B",
            room_id=room_id,
            experiment=EXPERIMENT,
            run_id=run_id,
        )
        result.comparison = comparison

    finally:
        await agent_process.stop()
        await human_a.close()
        await human_b.close()
        stream.close()

    _evaluate(result)
    return result


def _evaluate(result: RunResult) -> None:
    """Acceptance criteria, experimental-protocol.md §15 and Task 02 §17."""
    reasons: list[str] = []

    for name in (CLASS_CROSS_DOMAIN, CLASS_SAME_DOMAIN):
        item = result.classes.get(name)
        if item is None:
            reasons.append(f"{name}: class not executed")
            continue
        if item.sent != E1_REQUESTS_PER_CLASS:
            reasons.append(f"{name}: sent {item.sent}, expected {E1_REQUESTS_PER_CLASS}")
        if item.acked != E1_REQUESTS_PER_CLASS:
            reasons.append(
                f"{name}: {item.acked}/{E1_REQUESTS_PER_CLASS} requests received exactly one ACK"
            )
        if item.duplicates:
            reasons.append(f"{name}: {item.duplicates} duplicate ACKs")

    if result.room_version != "12":
        reasons.append(f"room version {result.room_version!r}")
    if result.encryption_enabled:
        reasons.append("room encryption enabled")

    comparison = result.comparison
    if comparison is None:
        reasons.append("no federation comparison produced")
    else:
        reasons.extend(comparison.failure_reasons())

    result.reasons = reasons
    result.passed = not reasons


def _write_artifacts(result: RunResult, root: Path) -> None:
    artifacts = []
    if result.runner_stream and result.runner_stream.exists():
        artifacts.append(RawArtifact("runner_interaction_stream", result.runner_stream))
    if result.agent_stream and result.agent_stream.exists():
        artifacts.append(RawArtifact("agent_telemetry_stream", result.agent_stream))

    # The comparison is derived from the raw streams, so it carries their
    # digests alongside the frozen provenance triple.
    provenance = {
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": protocol_git_commit(),
        "source_run_id": result.run_id,
        "source_raw_digests": {
            artifact.role: file_sha256(artifact.path) for artifact in artifacts
        },
    }
    if result.comparison is not None:
        result.comparison_path = result.comparison.write(root / "processed", provenance)

    cross = result.classes.get(CLASS_CROSS_DOMAIN)
    same = result.classes.get(CLASS_SAME_DOMAIN)
    comparison = result.comparison

    manifest = RunManifest(
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
            "request_classes": {
                name: {
                    "sender": item.sender,
                    "sent": item.sent,
                    "acked": item.acked,
                    "timeouts": item.timeouts,
                    "duplicates": item.duplicates,
                }
                for name, item in result.classes.items()
            },
            "cross_domain_acked": cross.acked if cross else 0,
            "same_domain_acked": same.acked if same else 0,
            "membership_after_join": result.membership_after_join,
            "quiet_interval_seconds": E1_QUIET_INTERVAL_SECONDS,
            "event_set_equal": comparison.event_set_equal if comparison else False,
            "membership_compatible": comparison.membership_compatible if comparison else False,
            "a_requests_visible_on_b": comparison.a_requests_visible_on_b if comparison else False,
            "b_requests_visible_on_a": comparison.b_requests_visible_on_a if comparison else False,
            "expected_event_count": len(comparison.expected_event_ids) if comparison else 0,
            "domain_a_event_count": len(comparison.set_a) if comparison else 0,
            "domain_b_event_count": len(comparison.set_b) if comparison else 0,
            "missing_on_a": sorted(comparison.missing_on_a) if comparison else [],
            "missing_on_b": sorted(comparison.missing_on_b) if comparison else [],
            "unexpected_on_a": sorted(comparison.unexpected_on_a) if comparison else [],
            "unexpected_on_b": sorted(comparison.unexpected_on_b) if comparison else [],
            "federation_comparison_artifact": (
                result.comparison_path.relative_to(root).as_posix()
                if result.comparison_path
                else None
            ),
            "acceptance_failures": result.reasons,
        },
    )
    manifest.write(manifests_dir(root))


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"E1 — Federated Persistent Multi-Party Interaction ({E1_RUNS} runs)")
    print(f"results: {root}")
    print(f"publication_data: {publication_data()}")
    if not publication_data():
        print("development run — not publication evidence\n")

    results: list[RunResult] = []
    for index in range(1, E1_RUNS + 1):
        print(f"--- run {index}/{E1_RUNS} ---")
        try:
            result = await execute_run(index, root, stamp)
        except InvalidRun as exc:
            print(f"  INVALID RUN: {exc}")
            print("  classified under the §35 taxonomy; not an experimental failure")
            return 2
        results.append(result)
        _write_artifacts(result, root)

        cross = result.classes.get(CLASS_CROSS_DOMAIN)
        same = result.classes.get(CLASS_SAME_DOMAIN)
        comparison = result.comparison
        print(f"  run_id            {result.run_id}")
        print(f"  room              {result.room_id} (v{result.room_version})")
        print(f"  A->agent (cross)  {cross.acked}/{cross.sent} ACK  dup={cross.duplicates}")
        print(f"  B->agent (same)   {same.acked}/{same.sent} ACK  dup={same.duplicates}")
        if comparison:
            print(
                f"  event sets        expected={len(comparison.expected_event_ids)} "
                f"A={len(comparison.set_a)} B={len(comparison.set_b)} "
                f"equal={comparison.event_set_equal}"
            )
            print(
                f"  propagation       A->B={comparison.a_requests_visible_on_b} "
                f"B->A={comparison.b_requests_visible_on_a}"
            )
            print(f"  membership        {sorted(comparison.membership_a)}")
            print(f"                    compatible={comparison.membership_compatible}")
        print(f"  {'PASS' if result.passed else 'FAIL'}")
        for reason in result.reasons:
            print(f"    ! {reason}")
        print()

    passed = sum(1 for item in results if item.passed)
    print(f"E1: {passed}/{len(results)} runs PASS")
    return 0 if passed == E1_RUNS else 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
