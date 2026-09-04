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
from urllib.parse import quote

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


@dataclass
class RoomSyncSlice:
    """One room's timeline as a single sync returned it.

    ``limited`` and ``prev_batch`` are carried out of the client abstraction
    deliberately: a limited timeline is the Matrix signal that history is
    missing, and losing it here would make the E2 gap invisible to the runtime
    (testbed-architecture.md §19).
    """

    room_id: str
    events: list["TimelineEvent"]
    limited: bool
    prev_batch: str | None


@dataclass
class SyncSnapshot:
    next_batch: str
    rooms: list[RoomSyncSlice]

    def room(self, room_id: str) -> RoomSyncSlice | None:
        for item in self.rooms:
            if item.room_id == room_id:
                return item
        return None


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
        timeline_limit: int | None = None,
    ) -> None:
        self.homeserver_url = homeserver_url.rstrip("/")
        self.user_id = user_id
        self.device_name = device_name
        self.sync_timeout_ms = sync_timeout_ms
        #: Constrains the per-room timeline returned by /sync. E2 sets this
        #: below the offline request count so the recovery path is genuinely
        #: exercised rather than incidentally skipped.
        self.timeline_limit = timeline_limit
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

    async def join(self, room_id: str, attempts: int = 12, delay: float = 2.0) -> None:
        """Join a room, retrying while a federated invite is still in flight.

        An invited user's homeserver learns the room from the invite, so no
        `via` hint is needed. Across a federation boundary the invite may not
        have arrived yet, which is a timing condition rather than a failure,
        so this retries before giving up.
        """
        last = None
        for attempt in range(attempts):
            response = await self._client.join(room_id)
            if isinstance(response, JoinResponse):
                return
            last = response
            if attempt < attempts - 1:
                await asyncio.sleep(delay)
        raise MatrixError(f"join failed for {room_id} after {attempts} attempts: {last}")

    async def invite(self, room_id: str, user_id: str) -> None:
        await self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/invite",
            body={"user_id": user_id},
        )

    async def fetch_all_messages(
        self, room_id: str, page_limit: int = 200, max_pages: int = 50
    ) -> list[dict]:
        """Page the room timeline backwards through the ordinary history API.

        This is how a domain view is collected: what this participant's own
        homeserver will serve to an ordinary client, with no server-side or
        database access.
        """
        events: list[dict] = []
        token: str | None = None
        encoded = quote(room_id, safe="")
        for _ in range(max_pages):
            query = f"?dir=b&limit={page_limit}"
            if token:
                query += f"&from={quote(token, safe='')}"
            payload = await self._request(
                "GET", f"/_matrix/client/v3/rooms/{encoded}/messages{query}"
            )
            chunk = payload.get("chunk", [])
            events.extend(chunk)
            token = payload.get("end")
            if not chunk or not token:
                break
        return events

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
            "GET", f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/joined_members"
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

    def _sync_filter(self) -> dict | None:
        if self.timeline_limit is None:
            return None
        return {"room": {"timeline": {"limit": self.timeline_limit}}}

    @staticmethod
    def _to_timeline_event(room_id: str, event) -> "TimelineEvent | None":
        body = getattr(event, "body", None)
        if body is None:
            source = getattr(event, "source", {}) or {}
            body = (source.get("content") or {}).get("body")
        if not isinstance(body, str):
            return None
        return TimelineEvent(
            room_id=room_id,
            event_id=event.event_id,
            sender=event.sender,
            body=body,
            origin_server_ts=getattr(event, "server_timestamp", 0),
        )

    async def sync_once(
        self, *, since: str | None = None, timeout: int = 0
    ) -> SyncSnapshot:
        """One sync, with the limited-timeline signal preserved."""
        response = await self._client.sync(
            timeout=timeout,
            since=since,
            full_state=False,
            sync_filter=self._sync_filter(),
        )
        if not isinstance(response, SyncResponse):
            raise MatrixError(f"sync failed: {response}")
        self._sync_token = response.next_batch

        slices = []
        for room_id, room in response.rooms.join.items():
            events = []
            for event in room.timeline.events:
                parsed = self._to_timeline_event(room_id, event)
                if parsed is not None:
                    events.append(parsed)
            slices.append(
                RoomSyncSlice(
                    room_id=room_id,
                    events=events,
                    limited=bool(room.timeline.limited),
                    prev_batch=room.timeline.prev_batch,
                )
            )
        return SyncSnapshot(next_batch=response.next_batch, rooms=slices)

    async def paginate_backwards(
        self,
        room_id: str,
        *,
        start: str,
        to: str | None = None,
        page_limit: int = 100,
        max_pages: int = 50,
    ) -> tuple[list[dict], int]:
        """Ordinary Matrix history pagination between two stream tokens.

        ``start`` is normally the ``prev_batch`` of a limited timeline and
        ``to`` the saved transport checkpoint, so pagination stops at the known
        boundary rather than walking the whole room
        (testbed-architecture.md §20).

        Returns the raw events and the number of pages fetched. No server-side
        or database access: this is the same endpoint any client would use.
        """
        events: list[dict] = []
        token: str | None = start
        pages = 0
        encoded = quote(room_id, safe="")
        for _ in range(max_pages):
            query = f"?dir=b&limit={page_limit}&from={quote(token, safe='')}"
            if to:
                query += f"&to={quote(to, safe='')}"
            payload = await self._request(
                "GET", f"/_matrix/client/v3/rooms/{encoded}/messages{query}"
            )
            pages += 1
            chunk = payload.get("chunk", [])
            events.extend(chunk)
            token = payload.get("end")
            if not chunk or not token:
                break
            if to and token == to:
                break
        return events, pages

    async def prime_sync(self) -> None:
        """One initial sync to establish a checkpoint without replaying history."""
        await self.sync_once(since=None, timeout=0)

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
            try:
                snapshot = await self.sync_once(
                    since=self._sync_token, timeout=self.sync_timeout_ms
                )
            except MatrixError:
                await asyncio.sleep(0.5)
                continue
            for item in snapshot.rooms:
                for parsed in item.events:
                    for handler in self._handlers:
                        await handler(parsed)

    # ------------------------------------------------------------- lifecycle

    async def close(self) -> None:
        await self.stop_sync()
        await self._client.close()

    # ------------------------------------------------------------------ raw

    async def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> dict[str, Any]:
        url = f"{self.homeserver_url}{path}"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, json=body
            ) as response:
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
