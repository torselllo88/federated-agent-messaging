"""Room configuration assertions and ordinary-client domain views.

Everything here uses ordinary Matrix Client-Server APIs. No Synapse admin
endpoint, no database, no server filesystem — a domain's view is obtained
through an ordinary participant account belonging to that domain, and one
view is never derived from the other.
"""

from __future__ import annotations

from fam.common.domain_view import DomainView
from fam.common.frozen import MESSAGE_EVENT_TYPE, ROOM_VERSION
from fam.common.message import parse
from fam.common.validity import InvalidRun, InvalidRunClass
from fam.matrix.client import MatrixParticipant


async def assert_frozen_room_configuration(
    observer: MatrixParticipant, room_id: str
) -> tuple[str, bool]:
    """experimental-protocol.md §4.2, enumerated in each experiment procedure.

    A newly created room that does not match the frozen room configuration
    because of a setup or configuration error makes the run invalid under
    ``frozen_configuration_error``.
    """
    version = await observer.room_version_of(room_id)
    encrypted = await observer.room_encryption_enabled(room_id)
    if version != ROOM_VERSION:
        raise InvalidRun(
            InvalidRunClass.FROZEN_CONFIGURATION_ERROR,
            f"room {room_id} has version {version!r}, frozen value is {ROOM_VERSION!r}",
        )
    if encrypted:
        raise InvalidRun(
            InvalidRunClass.FROZEN_CONFIGURATION_ERROR,
            f"room {room_id} has encryption enabled; the frozen configuration disables it",
        )
    return version, encrypted


async def collect_domain_view(
    participant: MatrixParticipant,
    *,
    domain: str,
    room_id: str,
    experiment: str,
    run_id: str,
) -> DomainView:
    """Read the room independently through this participant's homeserver."""
    view = DomainView(domain=domain, observer=participant.user_id)

    events = await participant.fetch_all_messages(room_id)
    view.total_events_seen = len(events)

    for event in events:
        if event.get("type") != MESSAGE_EVENT_TYPE:
            view.non_experimental_events += 1
            continue
        body = (event.get("content") or {}).get("body")
        message = parse(body) if isinstance(body, str) else None
        if message is None:
            view.non_experimental_events += 1
            continue
        correlation = message.correlation
        if (
            correlation.experiment.upper() != experiment.upper()
            or correlation.run_id != run_id
        ):
            # A FAM/1 message from a different run sharing the room would be
            # a contamination problem, not a housekeeping event. Fresh rooms
            # make it impossible, but it is counted rather than ignored.
            view.non_experimental_events += 1
            continue

        event_id = event["event_id"]
        view.experimental_event_ids.add(event_id)
        kind = "request" if message.is_request else "ack"
        if message.is_request:
            view.request_event_ids.add(event_id)
        else:
            view.ack_event_ids.add(event_id)
        view.detail[event_id] = (kind, event.get("sender", ""), correlation.sequence_id)

    view.membership = await participant.joined_members(room_id)
    return view
