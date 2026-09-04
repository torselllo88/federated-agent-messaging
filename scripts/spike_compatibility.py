#!/usr/bin/env python3
"""Development-only compatibility spike (Task 01 §2).

Checks the frozen combination before anything is built around it:

    Synapse 1.159.0 + PostgreSQL 16 + matrix-nio 0.26.0
    room created explicitly at room version 12
    resulting room version is exactly 12
    encryption disabled
    ordinary-client join / send / receive path

If this combination does not behave as specified, stop and report the
conflict rather than building around it.

publication_data = false. This produces no evidence.
"""

from __future__ import annotations

import asyncio
import sys
from importlib.metadata import version as pkg_version

sys.path.insert(0, "/app/src")

from fam.common.env import account  # noqa: E402
from fam.common.frozen import ROOM_VERSION  # noqa: E402
from fam.common.message import Correlation, build_request, parse  # noqa: E402
from fam.matrix.client import MatrixParticipant  # noqa: E402

HUMAN_A = "@human-a:hs-a.test"
AGENT_LOCAL = "@agent-local:hs-a.test"


class Spike:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)


async def main_async() -> int:
    spike = Spike()
    print("Compatibility spike — frozen baseline")
    print("publication_data = false; this produces no evidence\n")

    spike.record("matrix-nio version", True, pkg_version("matrix-nio"))
    spike.record("python version", True, sys.version.split()[0])

    human_account = account(HUMAN_A)
    agent_account = account(AGENT_LOCAL)

    human = MatrixParticipant(
        homeserver_url=human_account.homeserver_url,
        user_id=HUMAN_A,
        device_name="fam-spike-human",
    )
    agent = MatrixParticipant(
        homeserver_url=agent_account.homeserver_url,
        user_id=AGENT_LOCAL,
        device_name="fam-spike-agent",
    )

    received: asyncio.Queue = asyncio.Queue()
    room_id = ""

    try:
        await human.login(human_account.password)
        spike.record("ordinary password login (human-a)", True, HUMAN_A)
        await agent.login(agent_account.password)
        spike.record("ordinary password login (agent-local)", True, AGENT_LOCAL)

        room_id = await human.create_room(
            name="FAM compatibility spike",
            invite=[AGENT_LOCAL],
            room_version=ROOM_VERSION,
        )
        spike.record(f"create room at explicit version {ROOM_VERSION}", True, room_id)

        observed = await human.room_version_of(room_id)
        spike.record(
            f"resulting room version is exactly {ROOM_VERSION}",
            observed == ROOM_VERSION,
            f"observed {observed!r}",
        )

        encrypted = await human.room_encryption_enabled(room_id)
        spike.record("encryption disabled", not encrypted, f"encrypted={encrypted}")

        await agent.join(room_id)
        spike.record("ordinary client join", True)

        members = await human.joined_members(room_id)
        spike.record(
            "both participants joined",
            HUMAN_A in members and AGENT_LOCAL in members,
            ", ".join(members),
        )

        async def handler(event) -> None:
            await received.put(event)

        agent.on_event(handler)
        await agent.prime_sync()
        agent.start_sync()

        correlation = Correlation("SPIKE", "spike-001", 1)
        body = build_request(correlation)
        event_id = await human.send_text(room_id, body, correlation.txn_id("request"))
        spike.record("ordinary client send", bool(event_id), event_id)

        try:
            event = await asyncio.wait_for(received.get(), timeout=30)
            parsed = parse(event.body)
            spike.record(
                "ordinary client receive and parse",
                parsed is not None and parsed.correlation.key() == correlation.key(),
                f"event_id={event.event_id}",
            )
        except asyncio.TimeoutError:
            spike.record("ordinary client receive and parse", False, "no event within 30s")

    except Exception as exc:  # noqa: BLE001
        spike.record("spike completed without error", False, f"{type(exc).__name__}: {exc}")
    finally:
        await agent.close()
        await human.close()

    print(f"\nSPIKE: {'PASS' if spike.ok else 'FAIL'}")
    if not spike.ok:
        print(
            "\nThe frozen combination did not behave as specified.\n"
            "Stop and report the conflict; do not build around it."
        )
    return 0 if spike.ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
