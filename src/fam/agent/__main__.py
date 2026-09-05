"""Agent entry point.

Runs as its own OS process so that the experiment can terminate and restart
the runtime without touching the messaging substrate — the separation E0
step 8-11 and, later, E2 depend on.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
from pathlib import Path

from fam.agent import protocol as request_protocol
from fam.agent.runtime import AgentConfig, serve
from fam.executors.deterministic import DeterministicExecutor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fam-agent")
    parser.add_argument("--homeserver", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--telemetry", required=True, type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument(
        "--timeline-limit",
        type=int,
        default=None,
        help="Constrain the /sync timeline. E2 sets this below the offline "
             "request count so the recovery path is exercised.",
    )
    parser.add_argument(
        "--body-bytes",
        type=int,
        default=None,
        help="Pad responses to an exact size. E3 only; omit for E0-E2.",
    )
    parser.add_argument(
        "--executor",
        choices=("deterministic", "llm"),
        default="deterministic",
        help="Decision function. E0-E3 use the deterministic executor; E4 "
             "substitutes the LLM-backed one and changes nothing else.",
    )
    parser.add_argument(
        "--request-protocol",
        choices=(request_protocol.CONTROLLED, request_protocol.NATURAL_LANGUAGE),
        default=request_protocol.CONTROLLED,
        help="Envelope the agent expects. E4 reads ordinary prose from a "
             "standard Matrix client; E0-E3 read the controlled FAM/1 format.",
    )
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> None:
    config = AgentConfig(
        homeserver_url=args.homeserver,
        user_id=args.user,
        password=args.password,
        experiment=args.experiment,
        run_id=args.run_id,
        room_id=args.room_id,
        telemetry_path=args.telemetry,
        state_dir=args.state_dir,
        timeline_limit=args.timeline_limit,
        request_protocol=args.request_protocol,
    )
    if args.executor == "llm":
        # Imported lazily so E0-E3 never load the provider adapter, and so a
        # missing credential cannot break an experiment that does not use one.
        from fam.executors.llm import executor_from_environment

        executor = executor_from_environment()
    else:
        executor = DeterministicExecutor(body_bytes=args.body_bytes)

    task = asyncio.create_task(serve(config, executor))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
