"""Minimal reusable Matrix client over matrix-nio.

Deliberately not a general Matrix framework. It exposes only what the
participants, the agent and the runner need, and it keeps transport concerns
separate from executor logic (testbed-architecture.md §12, §13).

Everything here uses ordinary Client-Server APIs. Nothing in this module may
reach for an administrative endpoint, a database, or the server filesystem
(testbed-architecture.md §2.3).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp
from nio import (
    AsyncClient,
    AsyncClientConfig,
    JoinResponse,
    LoginResponse,
    RoomCreateResponse,
    RoomGetStateEventResponse,
    RoomSendResponse,
    SyncResponse,
)

from fam.common.frozen import MESSAGE_EVENT_TYPE, ROOM_VERSION


class MatrixError(RuntimeError):
    """An ordinary Client-Server operation failed."""


@dataclass
class TimelineEvent:
    """The subset of a timeline event this study cares about."""

    room_id: str
    event_id: str
    sender: str
    body: str
    origin_server_ts: int
    #: Never used as a latency clock (experimental-protocol.md §10). Carried
    #: only so that records can be cross-referenced after the fact.


EventHandler = Callable[[TimelineEvent], Awaitable[None]]


class MatrixParticipant:
    """One ordinary Matrix participant: human-role, agent, or runner-driven."""

    def __init__(
        self,
        *,
        homeserver_url: str,
        user_id: str,
        device_name: str = "fam",
        sync_timeout_ms: int = 30_000,
    ) -> None:
        self.homeserver_url = homeserver_url.rstrip("/")
        self.user_id = user_id
        self.device_name = device_name
        self.sync_timeout_ms = sync_timeout_ms
        self._client = AsyncClient(
            self.homeserver_url,
            user_id,
            config=AsyncClientConfig(
                # E2EE is intentionally disabled for the whole study
                # (testbed-architecture.md §36).
                encryption_enabled=False,
                request_timeout=60,
                max_timeout_retry_wait_time=5,
            ),
        )
        self._sync_token: str | None = None
        self._sync_task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._handlers: list[EventHandler] = []

    # ------------------------------------------------------------- identity

    @property
    def access_token(self) -> str:
        return self._client.access_token or ""

    @property
    def device_id(self) -> str:
        return self._client.device_id or ""

    @property
    def sync_token(self) -> str | None:
        """The transport checkpoint. Not conversational memory."""
        return self._sync_token

    async def login(self, password: str) -> None:
        response = await self._client.login(password, device_name=self.device_name)
        if not isinstance(response, LoginResponse):
            raise MatrixError(f"login failed for {self.user_id}: {response}")

    async def restore(self, access_token: str, device_id: str) -> None:
        """Resume an existing session without a fresh password login.

        C1: runtime restart must not create a new communication identity.
        """
        self._client.access_token = access_token
        self._client.device_id = device_id
        self._client.user_id = self.user_id

    async def whoami(self) -> str:
        payload = await self._request("GET", "/_matrix/client/v3/account/whoami")
        return payload.get("user_id", "")

    # ----------------------------------------------------------------- rooms

    async def create_room(
        self,
        *,
        name: str,
        invite: list[str] | None = None,
        room_version: str = ROOM_VERSION,
    ) -> str:
        response = await self._client.room_create(
            name=name,
            room_version=room_version,
            invite=invite or [],
            federate=True,
        )
        if not isinstance(response, RoomCreateResponse):
            raise MatrixError(f"room_create failed: {response}")
        return response.room_id

    async def join(self, room_id: str) -> None:
        response = await self._client.join(room_id)
        if not isinstance(response, JoinResponse):
            raise MatrixError(f"join failed for {room_id}: {response}")

    async def room_version_of(self, room_id: str) -> str:
        response = await self._client.room_get_state_event(
            room_id, "m.room.create", ""
        )
        if not isinstance(response, RoomGetStateEventResponse):
            raise MatrixError(f"cannot read m.room.create for {room_id}: {response}")
        # Room version 11+ moved several fields, but room_version has been in
        # the create content since v1 and remains authoritative.
        return str(response.content.get("room_version", ""))

    async def room_encryption_enabled(self, room_id: str) -> bool:
        response = await self._client.room_get_state_event(
            room_id, "m.room.encryption", ""
        )
        if isinstance(response, RoomGetStateEventResponse):
            return bool(response.content)
        # Absent state event means the room is unencrypted, which is what a
        # 404 from this endpoint indicates.
        return False

    async def joined_members(self, room_id: str) -> list[str]:
        payload = await self._request(
            "GET", f"/_matrix/client/v3/rooms/{room_id}/joined_members"
        )
        return sorted(payload.get("joined", {}).keys())

    # -------------------------------------------------------------- messages

    async def send_text(self, room_id: str, body: str, txn_id: str) -> str:
        """Send ``m.room.message``/``m.text`` with a deterministic txnId.

        A retry of the same logical send reuses ``txn_id`` so that client
        retransmission is idempotent at the protocol level
        (testbed-architecture.md §18).
        """
        response = await self._client.room_send(
            room_id=room_id,
            message_type=MESSAGE_EVENT_TYPE,
            content={"msgtype": "m.text", "body": body},
            tx_id=txn_id,
        )
        if not isinstance(response, RoomSendResponse):
            raise MatrixError(f"room_send failed: {response}")
        return response.event_id

    # ---------------------------------------------------------- sync control

    def on_event(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    async def prime_sync(self) -> None:
        """One initial sync to establish a checkpoint without replaying history."""
        response = await self._client.sync(timeout=0, full_state=False)
        if not isinstance(response, SyncResponse):
            raise MatrixError(f"initial sync failed: {response}")
        self._sync_token = response.next_batch

    def start_sync(self) -> None:
        """Continuous long-poll sync, no artificial delay between requests."""
        self._stopping.clear()
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop_sync(self) -> None:
        self._stopping.set()
        if self._sync_task is not None:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sync_task = None

    async def _sync_loop(self) -> None:
        while not self._stopping.is_set():
            response = await self._client.sync(
                timeout=self.sync_timeout_ms,
                since=self._sync_token,
                full_state=False,
            )
            if not isinstance(response, SyncResponse):
                await asyncio.sleep(0.5)
                continue
            self._sync_token = response.next_batch
            for room_id, room in response.rooms.join.items():
                for event in room.timeline.events:
                    body = getattr(event, "body", None)
                    if body is None:
                        source = getattr(event, "source", {}) or {}
                        body = (source.get("content") or {}).get("body")
                    if not isinstance(body, str):
                        continue
                    parsed = TimelineEvent(
                        room_id=room_id,
                        event_id=event.event_id,
                        sender=event.sender,
                        body=body,
                        origin_server_ts=getattr(event, "server_timestamp", 0),
                    )
                    for handler in self._handlers:
                        await handler(parsed)

    # ------------------------------------------------------------- lifecycle

    async def close(self) -> None:
        await self.stop_sync()
        await self._client.close()

    # ------------------------------------------------------------------ raw

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        url = f"{self.homeserver_url}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers) as response:
                text = await response.text()
                if response.status >= 400:
                    raise MatrixError(f"{method} {path} -> {response.status} {text}")
                return json.loads(text) if text else {}

    async def probe_status(self, method: str, path: str) -> int:
        """Return the HTTP status of an arbitrary request. No exceptions.

        Used for the privilege-negative probe that supports C2. Callers must
        keep this to read-only endpoints.
        """
        url = f"{self.homeserver_url}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=headers) as response:
                await response.read()
                return response.status
