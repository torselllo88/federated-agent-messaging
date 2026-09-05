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
import time
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


class MatrixRequestRejected(MatrixError):
    """The homeserver rejected an ordinary Client-Server request.

    Carries the Matrix ``errcode`` so that a rate-limit rejection is
    distinguishable from any other failure. ``M_LIMIT_EXCEEDED`` is an
    experimental observation under the frozen configuration and is recorded
    explicitly, never retried away (experimental-protocol.md §28).
    """

    def __init__(self, message: str, errcode: str = "") -> None:
        super().__init__(message)
        self.errcode = errcode

    @property
    def rate_limited(self) -> bool:
        return self.errcode == "M_LIMIT_EXCEEDED"


class RecoveryBoundExceeded(MatrixError):
    """History pagination hit its safety bound before reaching the boundary.

    Raised rather than returning what was collected: a partial recovery that
    looks successful is how a transport layer silently loses persisted events.
    """


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
    #: Timeline events the server returned before this client filtered them
    #: down to messages. The E3 sync-limit pilot selects a limit from observed
    #: occupancy, and occupancy is a property of the whole timeline, not just
    #: the experimental subset (§11 of the Task 05 pilot rule).
    raw_event_count: int = 0


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

#: Distinguishes "use the participant default" from an explicit ``None``,
#: which means "send no timeline filter at all".
_UNSET: Any = object()


def _errcode_of(response: object) -> str:
    """Extract the Matrix ``errcode`` from a matrix-nio error response.

    nio exposes it as ``status_code`` on ``ErrorResponse``, which is the
    Matrix errcode string rather than an HTTP status.
    """
    value = getattr(response, "status_code", None)
    return value if isinstance(value, str) else ""


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
        #: Live limited timelines are reconciled by default. Synapse truncates
        #: at 10 events even with no filter, so any dense workload can produce
        #: one; not handling it would silently drop persisted events.
        self.gap_recovery_enabled = True
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
        #: Rooms whose gaps are worth closing. Empty means every joined room.
        self.tracked_rooms: set[str] = set()
        #: Called with the new token once a batch has been fully reconciled
        #: and dispatched. This is where a durable checkpoint is written.
        self.on_commit: Callable[[str], Awaitable[None]] | None = None
        #: Called once per live gap-recovery episode, for telemetry.
        self.on_recovery_episode: Callable[[dict], Awaitable[None]] | None = None
        self._episode_counter = 0
        self._live_recovery_failures = 0
        #: Live transport diagnostics. Every E3 benchmark run reports these,
        #: for the sender as well as the agent: a recovery episode adds
        #: pagination round trips inside the sync loop, so a run where one
        #: occurred is a run whose timings carry a caveat (§42).
        self.live_recovery_episodes = 0
        self.live_history_pages = 0
        self.limited_syncs_observed = 0
        self.sync_slices_observed = 0
        self.max_timeline_events_observed = 0
        self.max_message_events_observed = 0
        #: Set while events reconciled by a recovery episode are being
        #: dispatched, so a handler can mark exactly the interactions whose
        #: event arrived through pagination rather than directly from sync.
        self.current_recovery_episode: int | None = None
        #: One entry per live episode, with monotonic bounds, for the run
        #: manifest. Diagnostics; never a measurement.
        self.recovery_episode_log: list[dict] = []

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

    async def joined_room_ids(self) -> list[str]:
        payload = await self._request("GET", "/_matrix/client/v3/joined_rooms")
        return list(payload.get("joined_rooms", []))

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
            raise MatrixRequestRejected(
                f"room_send failed: {response}", errcode=_errcode_of(response)
            )
        return response.event_id

    # ---------------------------------------------------------- sync control

    def on_event(self, handler: EventHandler) -> None:
        self._handlers.append(handler)

    def _sync_filter(self, timeline_limit: int | None = _UNSET) -> dict | None:
        limit = self.timeline_limit if timeline_limit is _UNSET else timeline_limit
        if limit is None:
            return None
        return {"room": {"timeline": {"limit": limit}}}

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
        self,
        *,
        since: str | None = None,
        timeout: int = 0,
        timeline_limit: int | None = _UNSET,
    ) -> SyncSnapshot:
        """One sync, with the limited-timeline signal preserved.

        ``timeline_limit`` overrides the participant default for this one
        request. Used by :meth:`prime_sync`, which wants a position and
        nothing else.
        """
        response = await self._client.sync(
            timeout=timeout,
            since=since,
            full_state=False,
            sync_filter=self._sync_filter(timeline_limit),
        )
        if not isinstance(response, SyncResponse):
            raise MatrixError(f"sync failed: {response}")

        slices = []
        for room_id, room in response.rooms.join.items():
            events = []
            for event in room.timeline.events:
                parsed = self._to_timeline_event(room_id, event)
                if parsed is not None:
                    events.append(parsed)
            raw_count = len(room.timeline.events)
            self.max_timeline_events_observed = max(
                self.max_timeline_events_observed, raw_count
            )
            self.max_message_events_observed = max(
                self.max_message_events_observed, len(events)
            )
            self.sync_slices_observed += 1
            slices.append(
                RoomSyncSlice(
                    room_id=room_id,
                    events=events,
                    limited=bool(room.timeline.limited),
                    prev_batch=room.timeline.prev_batch,
                    raw_event_count=raw_count,
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
        reached_boundary = False
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
                reached_boundary = True
                break
            if to and token == to:
                reached_boundary = True
                break
        if not reached_boundary:
            # The bound exists to stop runaway pagination, not to cap
            # recovery. Hitting it means the gap was not fully closed.
            raise RecoveryBoundExceeded(
                f"pagination reached the {max_pages}-page safety bound in "
                f"{room_id} without reaching the recovery boundary; "
                f"{len(events)} events collected but the gap is unresolved"
            )
        return events, pages

    async def prime_sync(self) -> None:
        """One initial sync to establish a checkpoint without replaying history.

        The timeline limit is zero for this request, which is what "without
        replaying history" actually requires. An initial sync carries every
        joined room, so at the participant default the response would grow
        with every room the account has ever been in — across a 120-run
        benchmark campaign that becomes a large, steadily increasing setup
        cost for something whose only output is a stream token.
        """
        snapshot = await self.sync_once(since=None, timeout=0, timeline_limit=0)
        self._sync_token = snapshot.next_batch

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

    def _is_tracked(self, room_id: str) -> bool:
        return not self.tracked_rooms or room_id in self.tracked_rooms

    async def reconcile_slice(
        self, item: RoomSyncSlice, *, since: str | None, trigger: str
    ) -> tuple[list["TimelineEvent"], dict]:
        """Close a limited timeline by paginating back to ``since``.

        The same mechanism serves startup recovery and live recovery; only the
        surrounding lifecycle differs (``trigger`` records which).

        Returns the reconciled events, deduplicated by ``event_id``, and an
        episode summary. Raises on an unresolved gap rather than returning a
        partial set.
        """
        self._episode_counter += 1
        episode = {
            "recovery_episode": self._episode_counter,
            "recovery_trigger": trigger,
            "room_id": item.room_id,
            "previous_checkpoint_present": since is not None,
            "sync_limited": item.limited,
            "direct_from_sync": len(item.events),
        }

        merged: dict[str, TimelineEvent] = {}
        duplicates = 0
        for event in item.events:
            if event.event_id in merged:
                duplicates += 1
            else:
                merged[event.event_id] = event

        pages = 0
        from_history = 0
        if item.limited and item.prev_batch:
            raw_events, pages = await self.paginate_backwards(
                item.room_id, start=item.prev_batch, to=since
            )
            for raw in raw_events:
                event = self._from_raw_event(item.room_id, raw)
                if event is None:
                    continue
                if event.event_id in merged:
                    duplicates += 1
                    continue
                merged[event.event_id] = event
                from_history += 1

        episode.update(
            {
                "history_pages_fetched": pages,
                "recovered_from_history": from_history,
                "duplicate_observations": duplicates,
                "reconciled_unique_events": len(merged),
            }
        )
        return list(merged.values()), episode

    @staticmethod
    def _from_raw_event(room_id: str, raw: dict) -> "TimelineEvent | None":
        if raw.get("type") != MESSAGE_EVENT_TYPE:
            return None
        body = (raw.get("content") or {}).get("body")
        if not isinstance(body, str):
            return None
        return TimelineEvent(
            room_id=raw.get("room_id") or room_id,
            event_id=raw["event_id"],
            sender=raw.get("sender", ""),
            body=body,
            origin_server_ts=raw.get("origin_server_ts", 0),
        )

    async def _sync_loop(self) -> None:
        """Gap-aware live synchronization.

        The durable checkpoint is advanced only after a batch has been fully
        reconciled and dispatched. If reconciliation fails, the committed
        token is left alone so the same gap is reachable on the next attempt:
        advancing past an unresolved gap is what makes persisted events
        permanently invisible.
        """
        while not self._stopping.is_set():
            since = self._sync_token
            try:
                snapshot = await self.sync_once(
                    since=since, timeout=self.sync_timeout_ms
                )
            except MatrixError:
                await asyncio.sleep(0.5)
                continue

            try:
                for item in snapshot.rooms:
                    if not self._is_tracked(item.room_id):
                        continue
                    episode_id: int | None = None
                    if item.limited and self.gap_recovery_enabled:
                        started_ns = time.monotonic_ns()
                        events, episode = await self.reconcile_slice(
                            item, since=since, trigger="live_sync"
                        )
                        episode_id = episode.get("recovery_episode")
                        self.limited_syncs_observed += 1
                        self.live_recovery_episodes += 1
                        self.live_history_pages += episode.get(
                            "history_pages_fetched", 0
                        )
                        self.recovery_episode_log.append(
                            {
                                "recovery_episode": episode_id,
                                "room_id": item.room_id,
                                "started_monotonic_ns": started_ns,
                                "reconciled_monotonic_ns": time.monotonic_ns(),
                                "history_pages_fetched": episode.get(
                                    "history_pages_fetched", 0
                                ),
                                "recovered_from_history": episode.get(
                                    "recovered_from_history", 0
                                ),
                            }
                        )
                        if self.on_recovery_episode is not None:
                            await self.on_recovery_episode(episode)
                    else:
                        events = item.events
                    # Marked for the duration of dispatch so a handler can
                    # attribute an event to the episode that recovered it.
                    self.current_recovery_episode = episode_id
                    try:
                        for parsed in events:
                            for handler in self._handlers:
                                await handler(parsed)
                    finally:
                        self.current_recovery_episode = None
            except Exception as exc:  # noqa: BLE001
                self._live_recovery_failures += 1
                if self.on_recovery_episode is not None:
                    await self.on_recovery_episode(
                        {
                            "recovery_trigger": "live_sync",
                            "recovery_failed": True,
                            "error": f"{type(exc).__name__}: {exc}",
                            "checkpoint_retained": since,
                        }
                    )
                # Do not commit. Retry the same window on the next iteration.
                self._sync_token = since
                await asyncio.sleep(0.5)
                continue

            self._sync_token = snapshot.next_batch
            if self.on_commit is not None:
                await self.on_commit(snapshot.next_batch)

    # ------------------------------------------------------------- lifecycle

    def reset_transport_diagnostics(self) -> dict[str, Any]:
        """Zero the live counters and return what they held.

        Called at the workload boundary so that what the counters report
        afterwards describes the measurement period. Room creation, joins and
        the initial sync all produce ordinary transport activity that has
        nothing to do with the workload, and folding the two together would
        make a clean sync configuration look like a truncating one.
        """
        previous = self.transport_diagnostics()
        self.live_recovery_episodes = 0
        self.live_history_pages = 0
        self.limited_syncs_observed = 0
        self.sync_slices_observed = 0
        self.max_timeline_events_observed = 0
        self.max_message_events_observed = 0
        self._live_recovery_failures = 0
        self.recovery_episode_log = []
        return previous

    def transport_diagnostics(self) -> dict[str, int]:
        """Live sync facts this participant observed. Never a measurement."""
        return {
            "live_recovery_episodes": self.live_recovery_episodes,
            "live_recovery_pages": self.live_history_pages,
            "live_recovery_failures": self._live_recovery_failures,
            "limited_syncs_observed": self.limited_syncs_observed,
            "recovery_episode_log": list(self.recovery_episode_log),
            "sync_slices_observed": self.sync_slices_observed,
            "max_timeline_events_observed": self.max_timeline_events_observed,
            "max_message_events_observed": self.max_message_events_observed,
            "sync_timeline_limit": self.timeline_limit,
            "sync_timeout_ms": self.sync_timeout_ms,
        }

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
