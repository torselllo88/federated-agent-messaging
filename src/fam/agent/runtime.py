"""The autonomous agent runtime.

An ordinary external Matrix client. No part of it executes inside Synapse, and
it holds no administrator credential, no database access, no signing key and
no server filesystem access (testbed-architecture.md §2.3, §12).

Responsibilities, in the vocabulary of testbed-architecture.md §13::

    connect()  synchronize()  select_relevant_event()  execute(event)
    send_response()  checkpoint()  record_observation()
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from fam.common.dedup import Decision, ProcessedRegistry
from fam.common.message import parse
from fam.common.privilege import ADMIN_PROBE_PATH, environment_evidence, summarize
from fam.executors.base import Executor
from fam.instrumentation.streams import JsonlStream, agent_record, monotonic_ns
from fam.matrix.client import MatrixParticipant, TimelineEvent


@dataclass
class AgentConfig:
    homeserver_url: str
    user_id: str
    password: str
    experiment: str
    run_id: str
    room_id: str
    telemetry_path: Path
    state_dir: Path
    device_name: str = "fam-agent"


class TransportCheckpoint:
    """Agent transport state, kept strictly separate from conversation content.

    Holds only what is needed to resume communication correctly: credentials,
    device information and the ``/sync`` position. The sync cursor is a
    transport checkpoint, not conversational memory
    (testbed-architecture.md §9.2, §9.3).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, *, access_token: str, device_id: str, sync_token: str | None) -> None:
        payload = {
            "access_token": access_token,
            "device_id": device_id,
            "sync_token": sync_token,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)


class AgentRuntime:
    def __init__(self, config: AgentConfig, executor: Executor) -> None:
        self.config = config
        self.executor = executor
        self.checkpoint = TransportCheckpoint(
            config.state_dir / f"{_slug(config.user_id)}.checkpoint.json"
        )
        self.participant = MatrixParticipant(
            homeserver_url=config.homeserver_url,
            user_id=config.user_id,
            device_name=config.device_name,
        )
        self.telemetry = JsonlStream(config.telemetry_path, "agent")
        self._registry = ProcessedRegistry()
        self._resumed_from_checkpoint = False

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        stored = self.checkpoint.load()
        if stored.get("access_token") and stored.get("device_id"):
            await self.participant.restore(
                stored["access_token"], stored["device_id"]
            )
            observed = await self.participant.whoami()
            if observed != self.config.user_id:
                # The stored session no longer belongs to us; fall back to a
                # password login rather than silently acting as someone else.
                await self.participant.login(self.config.password)
            else:
                self._resumed_from_checkpoint = True
        else:
            await self.participant.login(self.config.password)

        # Joining is an ordinary client action and is idempotent, so a
        # restarted runtime re-affirms membership without needing to know
        # whether it was already joined.
        await self.participant.join(self.config.room_id)

        if stored.get("sync_token"):
            self.participant._sync_token = stored["sync_token"]  # noqa: SLF001
        else:
            await self.participant.prime_sync()

        self._record(
            action="connected",
            duplicate_decision="n/a",
            note=(
                "resumed from transport checkpoint"
                if self._resumed_from_checkpoint
                else "fresh login"
            ),
        )
        await self._record_privilege_evidence()
        self._save_checkpoint()

    async def _record_privilege_evidence(self) -> None:
        """Positive C2 evidence, asserted by the runtime about itself.

        The probe is read-only. A denial is supporting evidence, never proof
        on its own (fam.common.privilege).
        """
        status: int | None
        try:
            status = await self.participant.probe_status("GET", ADMIN_PROBE_PATH)
        except Exception:  # noqa: BLE001 - absence of a result is itself recorded
            status = None
        summary = summarize(environment_evidence(), status)
        self.telemetry.write(
            {
                "experiment": self.config.experiment,
                "run_id": self.config.run_id,
                "agent_mxid": self.config.user_id,
                "action": "privilege_evidence",
                "resumed_from_checkpoint": self._resumed_from_checkpoint,
                "c2_evidence": summary,
            }
        )

    async def run(self) -> None:
        self.participant.on_event(self._handle)
        self.participant.start_sync()

    async def shutdown(self) -> None:
        self._save_checkpoint()
        self._record(action="shutdown", duplicate_decision="n/a")
        await self.participant.close()
        self.telemetry.close()

    # ------------------------------------------------------------ processing

    async def _handle(self, event: TimelineEvent) -> None:
        if event.room_id != self.config.room_id:
            return
        if event.sender == self.config.user_id:
            self._record(
                action="ignored",
                duplicate_decision=Decision.SKIP_OWN_EVENT.value,
                sender=event.sender,
                request_event_id=event.event_id,
            )
            return

        received_ns = monotonic_ns()
        message = parse(event.body)
        if message is None or not message.is_request:
            self._record(
                action="ignored",
                duplicate_decision=Decision.SKIP_NOT_REQUEST.value,
                sender=event.sender,
                request_event_id=event.event_id,
                received_monotonic_ns=received_ns,
            )
            return

        key = message.correlation.key()
        decision = self._registry.decide(event_id=event.event_id, correlation_key=key)
        if decision is not Decision.PROCESS:
            self._record(
                action="ignored",
                duplicate_decision=decision.value,
                sender=event.sender,
                request_event_id=event.event_id,
                sequence_id=message.correlation.sequence_id,
                received_monotonic_ns=received_ns,
                note="logical request already processed exactly once",
            )
            return

        self._registry.commit(event_id=event.event_id, correlation_key=key)

        body = self.executor.decide(message)
        processed_ns = monotonic_ns()
        if body is None:
            self._record(
                action="no_response",
                duplicate_decision=Decision.PROCESS.value,
                sender=event.sender,
                request_event_id=event.event_id,
                sequence_id=message.correlation.sequence_id,
                received_monotonic_ns=received_ns,
                processed_monotonic_ns=processed_ns,
            )
            return

        txn_id = message.correlation.txn_id("response")
        response_event_id = None
        note = ""
        try:
            response_event_id = await self.participant.send_text(
                self.config.room_id, body, txn_id
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            note = f"send failed: {exc}"

        self._record(
            action="responded" if response_event_id else "send_failed",
            duplicate_decision=Decision.PROCESS.value,
            sender=event.sender,
            request_event_id=event.event_id,
            sequence_id=message.correlation.sequence_id,
            received_monotonic_ns=received_ns,
            processed_monotonic_ns=processed_ns,
            response_txn_id=txn_id,
            response_event_id=response_event_id,
            note=note,
        )
        self._save_checkpoint()

    # ------------------------------------------------------------- internals

    def _save_checkpoint(self) -> None:
        self.checkpoint.save(
            access_token=self.participant.access_token,
            device_id=self.participant.device_id,
            sync_token=self.participant.sync_token,
        )

    def _record(
        self,
        *,
        action: str,
        duplicate_decision: str,
        sender: str = "",
        request_event_id: str | None = None,
        sequence_id: int | None = None,
        received_monotonic_ns: int | None = None,
        processed_monotonic_ns: int | None = None,
        response_txn_id: str | None = None,
        response_event_id: str | None = None,
        note: str = "",
    ) -> None:
        self.telemetry.write(
            agent_record(
                experiment=self.config.experiment,
                run_id=self.config.run_id,
                sequence_id=sequence_id,
                room_id=self.config.room_id,
                agent_mxid=self.config.user_id,
                sender=sender,
                request_event_id=request_event_id,
                received_monotonic_ns=received_monotonic_ns,
                processed_monotonic_ns=processed_monotonic_ns,
                response_txn_id=response_txn_id,
                response_event_id=response_event_id,
                duplicate_decision=duplicate_decision,
                action=action,
                sync_token_present=self.participant.sync_token is not None,
                note=note,
            )
        )


def _slug(user_id: str) -> str:
    return user_id.lstrip("@").replace(":", "_").replace("/", "_")


async def serve(config: AgentConfig, executor: Executor) -> None:
    """Run until cancelled. The runner stops this by terminating the process."""
    runtime = AgentRuntime(config, executor)
    await runtime.connect()
    await runtime.run()
    ready = config.state_dir / f"{_slug(config.user_id)}.ready"
    ready.write_text(config.room_id, encoding="utf-8")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        ready.unlink(missing_ok=True)
        await runtime.shutdown()
