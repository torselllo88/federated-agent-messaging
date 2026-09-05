#!/usr/bin/env python3
"""E3 — Controlled Federation Overhead. Development campaign driver.

Executes the frozen E3 schedule: 20 paired latency blocks and 20 paired
throughput blocks at each of C = 8 and C = 32, in the counterbalanced order
generated in advance from a recorded seed (experimental-protocol.md §24).

    Latency          20 blocks x 2 topologies =  40 runs
    Throughput C=8   20 blocks x 2 topologies =  40 runs
    Throughput C=32  20 blocks x 2 topologies =  40 runs
                                                ---
                                                120 runs

Runs no other experiment. The campaign is resumable: a completed run is never
re-executed and never overwritten, and a resume against different parameters
becomes a different campaign rather than a silently mixed dataset (§45, §46).

Task 05 runs are development runs. ``publication_data`` is false, no formal
evidence counter moves, and the numbers these produce are implementation
validation — Task 07 collects the formal E3 evidence.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from fam.benchmark.runner import (  # noqa: E402
    artifact_digests,
    execute_benchmark_run,
)
from fam.benchmark.schedule import (  # noqa: E402
    DEFAULT_SCHEDULE_SEED,
    CampaignState,
    ScheduledRun,
    campaign_parameters,
    generate_campaign_schedule,
    pending,
)
from fam.common.env import protocol_git_commit, publication_data  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    E3_CONCURRENCY_LEVELS,
    E3_INTER_RUN_IDLE_SECONDS,
    E3_PAIRED_BLOCKS,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
)
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)
from fam.common.validity import InvalidRun, InvalidRunClass  # noqa: E402

#: The sync timeline limit is a development choice confirmed by the pilot
#: (§11, §53). It is deliberately far above the bounded-concurrency envelope:
#: a limit chosen just above C would make ordinary E3 load truncate timelines,
#: putting Task 04 gap recovery on the measurement path.
DEFAULT_SYNC_TIMELINE_LIMIT = 500
DEFAULT_SYNC_TIMEOUT_MS = 30_000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _load_environment_manifest(root: Path) -> dict[str, Any]:
    path = environment_dir(root) / "environment-latest.json"
    if not path.exists():
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"{path} not found; run `make setup` and `make verify` first",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _rate_limits_from(manifest: dict[str, Any]) -> dict[str, Any]:
    """Frozen rate-limit values, received as manifest data.

    The runner never reads Synapse configuration: it holds no administrator
    credential and no server filesystem access, and that restriction is part
    of the C2 evidence (experimental-protocol.md §28).
    """
    out: dict[str, Any] = {}
    for domain, config in (manifest.get("sanitized_config") or {}).items():
        out[domain] = {
            key: value for key, value in config.items() if key.startswith("rc_")
        }
    return out


def _check_offered_load(rate_limits: dict[str, Any], peak_per_second: int) -> list[str]:
    """Compare planned offered load against the frozen limits (§28).

    A limit that binds is not automatically a defect — if the correctly frozen
    configuration is genuinely reached, that is an experimental result. What
    must never happen is reaching it *unnoticed*, so this reports rather than
    adjusts anything.
    """
    problems: list[str] = []
    for domain, limits in sorted(rate_limits.items()):
        message = limits.get("rc_message") or {}
        per_second = message.get("per_second", 0)
        if per_second < peak_per_second:
            problems.append(
                f"domain {domain}: rc_message per_second={per_second} is below the "
                f"planned peak offered load of {peak_per_second}/s"
            )
    return problems


def _describe(run: ScheduledRun) -> str:
    if run.workload == E3_WORKLOAD_LATENCY:
        return f"{run.block_id} {run.topology} (latency, C=1)"
    return f"{run.block_id} {run.topology} (throughput, C={run.concurrency})"


async def main_async() -> int:
    root = ensure_layout(resolve_results_dir())
    environment = _load_environment_manifest(root)
    rate_limits = _rate_limits_from(environment)
    config_hashes = environment.get("config_hashes") or {}

    seed = _env_int("FAM_E3_SCHEDULE_SEED", DEFAULT_SCHEDULE_SEED)
    sync_limit = _env_int("FAM_E3_TIMELINE_LIMIT", DEFAULT_SYNC_TIMELINE_LIMIT)
    sync_timeout_ms = _env_int("FAM_E3_SYNC_TIMEOUT_MS", DEFAULT_SYNC_TIMEOUT_MS)
    blocks = _env_int("FAM_E3_BLOCKS", E3_PAIRED_BLOCKS)

    workloads_raw = os.environ.get("FAM_E3_WORKLOADS", "").strip()
    workloads = (
        tuple(part.strip() for part in workloads_raw.split(",") if part.strip())
        if workloads_raw
        else (E3_WORKLOAD_LATENCY, E3_WORKLOAD_THROUGHPUT)
    )

    parameters = campaign_parameters(
        seed=seed,
        sync_timeline_limit=sync_limit,
        sync_timeout_ms=sync_timeout_ms,
        blocks=blocks,
        concurrency_levels=E3_CONCURRENCY_LEVELS,
        protocol_git_commit=protocol_git_commit(),
        config_hashes=config_hashes,
        rate_limits=rate_limits,
    )
    parameters["workloads"] = list(workloads)

    state = CampaignState.open(root, parameters)
    schedule = generate_campaign_schedule(
        seed=seed,
        blocks=blocks,
        concurrency_levels=E3_CONCURRENCY_LEVELS,
        workloads=workloads,
    )
    schedule_path = state.write_schedule(schedule)

    print("E3 — Controlled Federation Overhead (development campaign)")
    print(f"results:      {root}")
    print(f"campaign:     {state.campaign_id}")
    print(f"fingerprint:  {state.fingerprint}")
    print(f"schedule:     {schedule_path.name}  seed={seed}  blocks={blocks}")
    print(f"sync limit:   timeline={sync_limit}  timeout={sync_timeout_ms}ms")
    print(f"publication_data: {publication_data()}")
    if not publication_data():
        print("development campaign — not publication evidence")

    if state.resumed:
        problems = state.verify_completed(root)
        if problems:
            print("\nresume refused: recorded evidence does not verify")
            for problem in problems:
                print(f"  ! {problem}")
            return 2
        print(
            f"\nresuming: {len(state.completed)}/{len(schedule)} runs already "
            "complete and digest-verified"
        )

    # §28: the runner compares its planned offered load against the frozen
    # limits it receives as manifest data.
    peak = max(E3_CONCURRENCY_LEVELS) * 20
    load_problems = _check_offered_load(rate_limits, peak)
    print(f"\nrate-limit envelope (planned peak {peak}/s):")
    if load_problems:
        for problem in load_problems:
            print(f"  ! {problem}")
        print("  proceeding: M_LIMIT_EXCEEDED is recorded, never retried away")
    else:
        for domain, limits in sorted(rate_limits.items()):
            message = limits.get("rc_message") or {}
            print(
                f"  domain {domain}: rc_message per_second="
                f"{message.get('per_second')} burst={message.get('burst_count')}"
            )

    todo = list(pending(schedule, state))
    print(f"\n{len(todo)} runs to execute\n")

    executed = 0
    invalid = 0
    for index, scheduled in enumerate(todo, start=1):
        # Deterministic, derived from the campaign and the scheduled position
        # rather than from the clock. A run interrupted before it wrote a
        # manifest is re-executed under the same identity, so it replaces its
        # own debris instead of leaving an orphan stream behind.
        run_id = (
            f"{state.campaign_id}-{scheduled.workload[:3]}"
            f"-c{scheduled.concurrency:02d}-b{scheduled.block_index:02d}"
            f"-{scheduled.topology}"
        )
        print(f"[{index}/{len(todo)}] {_describe(scheduled)}")

        result = await execute_benchmark_run(
            scheduled=scheduled,
            run_id=run_id,
            root=root,
            sync_timeline_limit=sync_limit,
            sync_timeout_ms=sync_timeout_ms,
            campaign_id=state.campaign_id,
            campaign_fingerprint=state.fingerprint,
            schedule_seed=seed,
            rate_limit_reference=rate_limits,
            environment_manifest="environment/environment-latest.json",
        )
        executed += 1

        workload = result.workload_result
        sender = result.sender_transport
        if result.validity.valid:
            initiated = workload.initiated if workload else 0
            success = sum(
                1 for r in (workload.records if workload else []) if r["outcome"] == "success"
            )
            print(
                f"    room {result.room_id} v{result.room_version}  "
                f"initiated={initiated} success={success} "
                f"send_errors={workload.send_errors if workload else 0}"
            )
            print(
                f"    transport: recovery_episodes="
                f"{sender.get('live_recovery_episodes')} "
                f"pages={sender.get('live_recovery_pages')} "
                f"max_timeline={sender.get('max_timeline_events_observed')} "
                f"agent_episodes={result.agent_transport.get('live_recovery_episodes')}"
            )
        else:
            invalid += 1
            print(f"    INVALID: {result.validity.invalid_class.value}")
            for problem in result.problems:
                print(f"      ! {problem}")

        state.record(
            scheduled,
            run_id=run_id,
            digests=artifact_digests(result),
            status=result.completion_status,
            manifest=(
                result.manifest_path.relative_to(root).as_posix()
                if result.manifest_path
                else ""
            ),
        )

        # §25: inter-run quiescence. Nothing is outstanding at this point —
        # every slot awaited its own final interaction — so this is idle time,
        # not a drain.
        if index < len(todo):
            await asyncio.sleep(E3_INTER_RUN_IDLE_SECONDS)

    print(f"\ncampaign {state.campaign_id}")
    print(f"  executed this invocation  {executed}")
    print(f"  invalid this invocation   {invalid}")
    print(f"  complete                  {len(state.completed)}/{len(schedule)}")
    if len(state.completed) < len(schedule):
        print("  campaign incomplete; rerun `make e3` to resume")
        return 1
    print("\nnext: make analyse")
    return 0


def main() -> int:
    try:
        return asyncio.run(main_async())
    except InvalidRun as exc:
        print(f"INVALID RUN: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
