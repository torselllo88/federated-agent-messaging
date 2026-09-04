#!/usr/bin/env python3
"""E2 development pilot: select a sync timeline limit that exercises recovery.

E2 must not pass merely because ``/sync`` happened to return every missed
request. The frozen design requires a deliberately constrained timeline so the
gap-recovery branch is genuinely exercised (Task 03 §8,
experimental-protocol.md §16 gap-recovery path).

This pilot tries candidate limits against a real room and reports, for each,
whether the post-restart sync reported a limited timeline and how the recovered
set split between sync and history. Selection is based only on exercising the
intended mechanism — never on improving a success metric.

publication_data = false. This produces no evidence and is not one of the three
E2 validation runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app/src")

from fam.agent.supervisor import AgentProcess  # noqa: E402
from fam.common.env import (  # noqa: E402
    account,
    agent_state_dir,
    protocol_git_commit,
    publication_data,
)
from fam.common.frozen import EXECUTION_ANALYSIS_SPEC_VERSION  # noqa: E402
from fam.common.message import Correlation  # noqa: E402
from fam.common.results import ensure_layout, raw_dir, resolve_results_dir  # noqa: E402
from fam.matrix.rooms import assert_frozen_room_configuration  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E2PILOT"
HUMAN_A = "@human-a:hs-a.test"
HUMAN_B = "@human-b:hs-b.test"
AGENT = "@agent:hs-b.test"
OFFLINE_REQUESTS = 100

#: Tried in order. The first that reliably exposes a limited timeline and
#: forces history recovery is recommended.
CANDIDATES = [int(x) for x in os.environ.get("FAM_E2_CANDIDATES", "100,50,10").split(",")]

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


async def try_candidate(limit: int, root: Path, stamp: str) -> dict:
    run_id = f"e2pilot-{stamp}-limit{limit}"
    agent_path = raw_dir(root, "e2") / f"{run_id}.agent.jsonl"
    state_dir = agent_state_dir() / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    account_a = account(HUMAN_A)
    account_b = account(HUMAN_B)
    account_agent = account(AGENT)

    human_a = HumanParticipant(
        homeserver_url=account_a.homeserver_url, user_id=HUMAN_A,
        password=account_a.password, device_name="fam-pilot-a",
    )
    human_b = HumanParticipant(
        homeserver_url=account_b.homeserver_url, user_id=HUMAN_B,
        password=account_b.password, device_name="fam-pilot-b",
    )
    agent = AgentProcess(
        user_id=AGENT, password=account_agent.password,
        homeserver=account_agent.homeserver_url, experiment=EXPERIMENT,
        run_id=run_id, room_id="", telemetry=agent_path, state_dir=state_dir,
        timeline_limit=limit,
    )

    outcome = {"timeline_limit": limit, "run_id": run_id}
    try:
        await human_a.start()
        await human_b.start()
        room_id = await human_a.client.create_room(
            name=f"FAM E2 pilot {run_id}", invite=[HUMAN_B, AGENT]
        )
        human_a.bind_room(room_id)
        agent.room_id = room_id
        outcome["room_id"] = room_id
        await assert_frozen_room_configuration(human_a.client, room_id)
        await human_b.client.join(room_id)

        await agent.start()
        await asyncio.sleep(3.0)
        await agent.stop()

        sent = set()
        for sequence in range(1, OFFLINE_REQUESTS + 1):
            interaction = await human_a.send_offline(
                Correlation(EXPERIMENT, run_id, sequence)
            )
            if interaction.request_event_id:
                sent.add(interaction.request_event_id)
        outcome["offline_sends"] = len(sent)

        await agent.start()
        await asyncio.sleep(20.0)

        telemetry = read_jsonl(agent_path)
        syncs = [r for r in telemetry if r.get("action") == "post_restart_sync"]
        completes = [r for r in telemetry if r.get("action") == "recovery_complete"]
        outcome["timeline_limited_observed"] = bool(
            syncs and syncs[-1].get("timeline_limited")
        )
        outcome["requests_directly_in_sync"] = (
            syncs[-1].get("requests_directly_in_sync") if syncs else None
        )
        if completes:
            last = completes[-1]
            outcome["recovered_from_sync"] = last.get("recovered_from_sync")
            outcome["recovered_from_history"] = last.get("recovered_from_history")
            outcome["pagination_invoked"] = last.get("pagination_invoked")
            outcome["history_pages_fetched"] = last.get("history_pages_fetched")
            outcome["recovered_total"] = last.get("recovered_total")
        outcome["exercises_recovery_path"] = bool(
            outcome.get("timeline_limited_observed")
            and outcome.get("pagination_invoked")
            and (outcome.get("recovered_from_history") or 0) > 0
            and outcome.get("recovered_total") == OFFLINE_REQUESTS
        )
    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
        outcome["error"] = f"{type(exc).__name__}: {exc}"
        outcome["exercises_recovery_path"] = False
    finally:
        await agent.stop()
        await human_a.close()
        await human_b.close()
    return outcome


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("E2 pilot — selecting a sync timeline limit")
    print(f"candidates: {CANDIDATES}   offline requests: {OFFLINE_REQUESTS}")
    print(f"publication_data: {publication_data()} (pilot produces no evidence)\n")

    attempts = []
    selected = None
    for limit in CANDIDATES:
        print(f"--- candidate timeline_limit={limit} ---")
        outcome = await try_candidate(limit, root, stamp)
        attempts.append(outcome)
        print(
            f"  limited={outcome.get('timeline_limited_observed')} "
            f"direct_in_sync={outcome.get('requests_directly_in_sync')} "
            f"from_sync={outcome.get('recovered_from_sync')} "
            f"from_history={outcome.get('recovered_from_history')} "
            f"pages={outcome.get('history_pages_fetched')} "
            f"total={outcome.get('recovered_total')}"
        )
        if outcome.get("error"):
            print(f"  error: {outcome['error']}")
        verdict = "exercises recovery" if outcome["exercises_recovery_path"] else "does NOT exercise recovery"
        print(f"  {verdict}\n")

    # Selection criterion, Task 03 §8: exercise the intended recovery
    # mechanism. Among candidates that work, prefer the one routing the most
    # events through history pagination, since that is what E2 is testing.
    # Explicitly NOT chosen for performance or to improve a success metric.
    workable = [a for a in attempts if a["exercises_recovery_path"]]
    if workable:
        best = max(workable, key=lambda a: a.get("recovered_from_history") or 0)
        selected = best["timeline_limit"]

    artifact = {
        "analysis_spec_version": EXECUTION_ANALYSIS_SPEC_VERSION,
        "analysis_code_commit": ANALYSIS_CODE_COMMIT,
        "protocol_git_commit": protocol_git_commit(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publication_data": publication_data(),
        "offline_request_count": OFFLINE_REQUESTS,
        "candidates_tried": CANDIDATES,
        "attempts": attempts,
        "selected_timeline_limit": selected,
        "selection_rationale": (
            "Among candidates that expose a limited post-restart timeline and "
            "force history pagination, the one routing the most events through "
            "the recovery path is selected, because exercising that mechanism "
            "is what E2 tests. Not chosen for performance and not chosen to "
            "improve any success metric."
        ),
        "note": (
            "Development pilot. publication_data is false; this is not one of "
            "the three E2 validation runs and updates no evidence counter."
        ),
    }
    processed = root / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    path = processed / f"e2-pilot-{stamp}.json"
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"selected timeline_limit: {selected}")
    print(f"pilot artifact: {path}")
    return 0 if selected is not None else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
