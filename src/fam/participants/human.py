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
from fam.common.message import Correlation, build_request, parse
from fam.instrumentation.streams import monotonic_ns
from fam.matrix.client import MatrixParticipant, TimelineEvent


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
    future: asyncio.Future = field(default_factory=asyncio.Future)


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

    async def start(self) -> None:
        await self.client.login(self.password)
        await self.client.prime_sync()
        self.client.on_event(self._handle)
        self.client.start_sync()

    async def close(self) -> None:
        await self.client.close()

    def bind_room(self, room_id: str) -> None:
        self._room_id = room_id

    async def _handle(self, event: TimelineEvent) -> None:
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
        if interaction.ack_count == 1:
            interaction.completed_monotonic_ns = monotonic_ns()
            interaction.response_event_id = event.event_id
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
            initiated_monotonic_ns=monotonic_ns(),
        )
        self._pending[correlation.key()] = interaction

        body = build_request(correlation, body_bytes=body_bytes)
        interaction.request_event_id = await self.client.send_text(
            self._room_id, body, txn_id
        )

        try:
            await asyncio.wait_for(asyncio.shield(interaction.future), timeout)
        except asyncio.TimeoutError:
            pass
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

    def duplicate_acks(self) -> int:
        return sum(
            max(0, item.ack_count - 1) for item in self._pending.values()
        )
