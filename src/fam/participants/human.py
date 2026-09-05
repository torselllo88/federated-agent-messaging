"""Programmatic human-role participant.

Represents a human-controlled communication identity in the architecture while
its experimental behaviour is automated, which is what makes workload
generation reproducible (testbed-architecture.md §11.1).

The distinction matters for claims: this is a *role*, not a person. C4's
"at least one human participant" is completed by E4 with an actual human, not
by this class (research-scope.md §6 C4, Empirical support).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fam.common.frozen import DEFAULT_INTERACTION_TIMEOUT_SECONDS
from fam.common.message import (
    Correlation,
    assert_body_length,
    build_request,
    parse,
)
from fam.instrumentation.streams import monotonic_ns
from fam.matrix.client import (
    MatrixParticipant,
    MatrixRequestRejected,
    TimelineEvent,
)


@dataclass
class Interaction:
    """One logical interaction awaiting its correlated ACK."""

    correlation: Correlation
    request_txn_id: str
    initiated_monotonic_ns: int
    request_event_id: str | None = None
    response_event_id: str | None = None
    completed_monotonic_ns: int | None = None
    ack_count: int = 0
    #: Set when the homeserver refused the send. ``M_LIMIT_EXCEEDED`` here is
    #: an experimental observation under the frozen configuration.
    send_errcode: str = ""
    send_error: str = ""
    #: Set the moment the frozen per-interaction timeout expires. Once true,
    #: the interaction has terminated as a timeout and nothing may reclassify
    #: it (experimental-protocol.md §11).
    timed_out: bool = False
    #: An ACK that arrived after the timeout. Observed and preserved, but it
    #: does not resurrect the interaction: the deadline already decided the
    #: outcome, and a completion time past the deadline is not a success.
    late_ack_monotonic_ns: int | None = None
    #: Set when the ACK reached the sender through a live gap-recovery
    #: episode rather than directly from a sync timeline. Such an interaction
    #: carries pagination round trips inside its measured RTT (Task 05 §42).
    recovery_episode: int | None = None
    future: asyncio.Future = field(default_factory=asyncio.Future)

    @property
    def rate_limited(self) -> bool:
        return self.send_errcode == "M_LIMIT_EXCEEDED"


class HumanParticipant:
    """An ordinary Matrix client that issues requests and awaits ACKs."""

    def __init__(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        password: str,
        device_name: str = "fam-human",
    ) -> None:
        self.user_id = user_id
        self.password = password
        self.client = MatrixParticipant(
            homeserver_url=homeserver_url,
            user_id=user_id,
            device_name=device_name,
        )
        self._pending: dict[tuple[str, str, int], Interaction] = {}
        self._room_id: str | None = None
        self._in_flight = 0

    async def start(self, *, defer_sync: bool = False) -> None:
        """Log in, establish a sync position, and begin synchronizing.

        ``defer_sync`` holds the live loop back until :meth:`begin_sync` is
        called. E3 uses it so the loop never runs before the benchmark room
        exists: an account that has accumulated rooms from earlier runs would
        otherwise have the loop reconciling unrelated history, which costs
        time and shows up in the transport diagnostics as workload activity.
        """
        await self.client.login(self.password)
        await self.client.prime_sync()
        self.client.on_event(self._handle)
        if not defer_sync:
            self.client.start_sync()

    def begin_sync(self) -> None:
        self.client.start_sync()

    async def close(self) -> None:
        await self.client.close()

    def bind_room(self, room_id: str) -> None:
        self._room_id = room_id

    async def _handle(self, event: TimelineEvent) -> None:
        # ------------------------------------------------------------------
        # T3. experimental-protocol.md §10: stamped at the very start of the
        # runner callback for the matching ACK — after the /sync response is
        # parsed, before ANY application-level processing of the event.
        #
        # Nothing may be inserted above this line. Room filtering, body
        # parsing, correlation lookup, bookkeeping, telemetry and logging all
        # belong below it; each is application-level work whose cost would
        # otherwise be charged to the measured RTT. The read is a single
        # monotonic clock access, and it is identical on both topologies.
        # ------------------------------------------------------------------
        t3 = monotonic_ns()

        if self._room_id and event.room_id != self._room_id:
            return
        message = parse(event.body)
        if message is None or not message.is_ack:
            return
        key = message.correlation.key()
        interaction = self._pending.get(key)
        if interaction is None:
            return
        interaction.ack_count += 1
        if interaction.timed_out:
            # The deadline already ended this interaction. Record that the ACK
            # eventually arrived — it is real, and losing it would hide a
            # slow path — but leave the outcome alone.
            if interaction.late_ack_monotonic_ns is None:
                interaction.late_ack_monotonic_ns = t3
                interaction.response_event_id = event.event_id
            return
        if interaction.ack_count == 1:
            interaction.completed_monotonic_ns = t3
            interaction.response_event_id = event.event_id
            interaction.recovery_episode = self.client.current_recovery_episode
            if not interaction.future.done():
                interaction.future.set_result(interaction)
        # A second distinct ACK for one logical request is a duplicate and is
        # counted, not discarded (experimental-protocol.md §12).

    async def request(
        self,
        correlation: Correlation,
        *,
        body_bytes: int | None = None,
        timeout: float = DEFAULT_INTERACTION_TIMEOUT_SECONDS,
    ) -> Interaction:
        """Send one request and await its correlated ACK.

        T0 is stamped immediately before the send begins; T3 is stamped at the
        start of the callback for the matching ACK, after the sync response is
        parsed and before application processing (experimental-protocol.md §10).
        """
        if self._room_id is None:
            raise RuntimeError("bind_room() must be called before request()")

        txn_id = correlation.txn_id("request")
        interaction = Interaction(
            correlation=correlation,
            request_txn_id=txn_id,
            # Replaced immediately before the send itself; see _request_inner.
            initiated_monotonic_ns=monotonic_ns(),
        )
        self._pending[correlation.key()] = interaction
        self._in_flight += 1
        try:
            return await self._request_inner(interaction, correlation, body_bytes, timeout)
        finally:
            self._in_flight -= 1

    async def _request_inner(
        self,
        interaction: "Interaction",
        correlation: Correlation,
        body_bytes: int | None,
        timeout: float,
    ) -> "Interaction":
        assert self._room_id is not None
        txn_id = interaction.request_txn_id
        body = build_request(correlation, body_bytes=body_bytes)
        if body_bytes is not None:
            # A body of the wrong encoded length is a defect, not a
            # tolerance. Asserted before every E3 send (§5).
            assert_body_length(body, body_bytes)
        try:
            # ------------------------------------------------------------------
            # T0. experimental-protocol.md §10: immediately before the request
            # send is initiated. Body construction and the size assertion above
            # are runner-side preparation, not part of the send, and charging
            # them to the RTT would inflate both topologies by an amount that
            # has nothing to do with the federation boundary.
            # ------------------------------------------------------------------
            interaction.initiated_monotonic_ns = monotonic_ns()
            interaction.request_event_id = await self.client.send_text(
                self._room_id, body, txn_id
            )
        except MatrixRequestRejected as exc:
            # Recorded, not retried: a silent retry would hide exactly the
            # constraint the experiment exists to observe
            # (experimental-protocol.md §28).
            interaction.send_errcode = exc.errcode or "unknown"
            interaction.send_error = str(exc)
            return interaction

        # The frozen bound is on the logical interaction (§11), which began at
        # T0 — not on the ACK wait alone. Budgeting from T0 is what keeps
        # RTT = T3 - T0 within the timeout that is supposed to bound it; arming
        # a full timeout after the send lets a slow send push a "success" past
        # the deadline, and it does so most often under load.
        elapsed_ns = monotonic_ns() - interaction.initiated_monotonic_ns
        remaining = timeout - elapsed_ns / 1e9
        if remaining <= 0:
            # The send alone consumed the interaction budget.
            interaction.timed_out = True
            return interaction
        try:
            await asyncio.wait_for(asyncio.shield(interaction.future), remaining)
        except asyncio.TimeoutError:
            # Stamped here, not inferred later: the interaction terminates now.
            interaction.timed_out = True
        return interaction

    async def send_offline(self, correlation: Correlation) -> Interaction:
        """Send a request without arming a live-interaction deadline.

        E2 deliberately sends while no runtime exists, so the ordinary
        response timeout must not be armed at send time and the deliberate
        offline interval must not enter the failure rate
        (experimental-protocol.md §11 offline sends, §12).

        The interaction is registered so its ACK can still be correlated once
        the runtime returns; the deadline begins with the restart and recovery
        phase, not here.
        """
        if self._room_id is None:
            raise RuntimeError("bind_room() must be called before send_offline()")

        txn_id = correlation.txn_id("request")
        interaction = Interaction(
            correlation=correlation,
            request_txn_id=txn_id,
            initiated_monotonic_ns=monotonic_ns(),
        )
        self._pending[correlation.key()] = interaction
        interaction.request_event_id = await self.client.send_text(
            self._room_id, build_request(correlation), txn_id
        )
        return interaction

    async def await_acks(
        self, correlations: list[Correlation], timeout: float
    ) -> int:
        """Wait for correlated ACKs. The deadline starts when this is called.

        Used after the runtime restarts, which is where the E2 response
        deadline begins.
        """
        futures = [
            self._pending[c.key()].future
            for c in correlations
            if c.key() in self._pending
        ]
        if not futures:
            return 0
        try:
            await asyncio.wait_for(
                asyncio.gather(*[asyncio.shield(f) for f in futures]), timeout
            )
        except asyncio.TimeoutError:
            pass
        return sum(1 for f in futures if f.done())

    def interaction(self, correlation: Correlation) -> Interaction | None:
        return self._pending.get(correlation.key())

    def outstanding_count(self) -> int:
        """Interactions initiated whose send/await has not yet returned.

        experimental-protocol.md §21 and §25 both require the runner to be
        able to confirm that nothing is outstanding at a phase boundary.
        """
        return self._in_flight

    def duplicate_acks(self) -> int:
        return sum(
            max(0, item.ack_count - 1) for item in self._pending.values()
        )
