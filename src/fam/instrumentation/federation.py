"""E1 federation comparison.

Produces the derived artifact that answers C5: are the experimental event sets
observed independently through each federation domain equal to each other and
to what the run generated?

This is a processed artifact, not authoritative raw evidence, so it carries the
frozen processed-artifact provenance (experimental-protocol.md §40): the
analysis specification, the analysis-code implementation, the protocol commit,
and the digests of the raw streams it derives from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fam.common.domain_view import DomainView


@dataclass
class FederationComparison:
    run_id: str
    room_id: str
    quiet_interval_seconds: float

    expected_event_ids: set[str] = field(default_factory=set)
    expected_request_ids_a: set[str] = field(default_factory=set)
    expected_request_ids_b: set[str] = field(default_factory=set)
    expected_ack_ids: set[str] = field(default_factory=set)
    expected_membership: set[str] = field(default_factory=set)

    view_a: DomainView | None = None
    view_b: DomainView | None = None

    # ------------------------------------------------------------- equality

    @property
    def set_a(self) -> set[str]:
        return self.view_a.experimental_event_ids if self.view_a else set()

    @property
    def set_b(self) -> set[str]:
        return self.view_b.experimental_event_ids if self.view_b else set()

    @property
    def missing_on_a(self) -> set[str]:
        return self.expected_event_ids - self.set_a

    @property
    def missing_on_b(self) -> set[str]:
        return self.expected_event_ids - self.set_b

    @property
    def unexpected_on_a(self) -> set[str]:
        return self.set_a - self.expected_event_ids

    @property
    def unexpected_on_b(self) -> set[str]:
        return self.set_b - self.expected_event_ids

    @property
    def event_set_equal(self) -> bool:
        """Exact equality, in all three directions. Counts alone are not it."""
        return (
            self.set_a == self.expected_event_ids
            and self.set_b == self.expected_event_ids
            and self.set_a == self.set_b
        )

    # ------------------------------------------- directional federation flow

    @property
    def a_requests_visible_on_b(self) -> bool:
        """A→B propagation, witnessed by an ordinary Domain-B participant.

        Deliberately not taken from agent telemetry: the agent is on Domain B
        and its own report is not independent evidence of propagation there.
        """
        return self.expected_request_ids_a <= self.set_b

    @property
    def b_requests_visible_on_a(self) -> bool:
        """B→A propagation.

        Required even though Human B → Agent is a same-domain request path:
        the visibility of those events on Domain A is what evidences the
        reverse federation direction.
        """
        return self.expected_request_ids_b <= self.set_a

    @property
    def a_requests_missing_on_b(self) -> set[str]:
        return self.expected_request_ids_a - self.set_b

    @property
    def b_requests_missing_on_a(self) -> set[str]:
        return self.expected_request_ids_b - self.set_a

    # ------------------------------------------------------------ membership

    @property
    def membership_a(self) -> set[str]:
        return set(self.view_a.membership) if self.view_a else set()

    @property
    def membership_b(self) -> set[str]:
        return set(self.view_b.membership) if self.view_b else set()

    @property
    def membership_compatible(self) -> bool:
        """Exact expected membership on both sides, and the two agree.

        Not "all three appear somewhere": an unexpected member would mean the
        controlled topology is not what the run claims it is.
        """
        return (
            self.membership_a == self.expected_membership
            and self.membership_b == self.expected_membership
        )

    @property
    def unexpected_members_a(self) -> set[str]:
        return self.membership_a - self.expected_membership

    @property
    def unexpected_members_b(self) -> set[str]:
        return self.membership_b - self.expected_membership

    @property
    def overall_result(self) -> bool:
        return (
            self.event_set_equal
            and self.a_requests_visible_on_b
            and self.b_requests_visible_on_a
            and self.membership_compatible
        )

    # ---------------------------------------------------------------- output

    def to_dict(self, provenance: dict) -> dict:
        return {
            **provenance,
            "run_id": self.run_id,
            "room_id": self.room_id,
            "quiet_interval_seconds": self.quiet_interval_seconds,
            "expected_event_ids": sorted(self.expected_event_ids),
            "expected_request_ids_domain_a_sender": sorted(self.expected_request_ids_a),
            "expected_request_ids_domain_b_sender": sorted(self.expected_request_ids_b),
            "expected_ack_ids": sorted(self.expected_ack_ids),
            "domain_a_event_ids": sorted(self.set_a),
            "domain_b_event_ids": sorted(self.set_b),
            "missing_on_a": sorted(self.missing_on_a),
            "missing_on_b": sorted(self.missing_on_b),
            "unexpected_on_a": sorted(self.unexpected_on_a),
            "unexpected_on_b": sorted(self.unexpected_on_b),
            "a_requests_visible_on_b": self.a_requests_visible_on_b,
            "b_requests_visible_on_a": self.b_requests_visible_on_a,
            "a_requests_missing_on_b": sorted(self.a_requests_missing_on_b),
            "b_requests_missing_on_a": sorted(self.b_requests_missing_on_a),
            "expected_membership": sorted(self.expected_membership),
            "domain_a_membership": sorted(self.membership_a),
            "domain_b_membership": sorted(self.membership_b),
            "unexpected_members_a": sorted(self.unexpected_members_a),
            "unexpected_members_b": sorted(self.unexpected_members_b),
            "event_set_equal": self.event_set_equal,
            "membership_compatible": self.membership_compatible,
            "overall_result": "PASS" if self.overall_result else "FAIL",
            "domain_a_view": self.view_a.summary() if self.view_a else None,
            "domain_b_view": self.view_b.summary() if self.view_b else None,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": (
                "Derived comparison artifact, not authoritative raw evidence. "
                "The quiet interval is an observation boundary, not proof of "
                "convergence."
            ),
        }

    def write(self, directory: Path, provenance: dict) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.federation-comparison.json"
        path.write_text(
            json.dumps(self.to_dict(provenance), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def failure_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.missing_on_a:
            reasons.append(f"{len(self.missing_on_a)} expected events missing on Domain A")
        if self.missing_on_b:
            reasons.append(f"{len(self.missing_on_b)} expected events missing on Domain B")
        if self.unexpected_on_a:
            reasons.append(f"{len(self.unexpected_on_a)} unexpected experimental events on Domain A")
        if self.unexpected_on_b:
            reasons.append(f"{len(self.unexpected_on_b)} unexpected experimental events on Domain B")
        if not self.a_requests_visible_on_b:
            reasons.append(
                f"{len(self.a_requests_missing_on_b)} Human A request events not visible on Domain B"
            )
        if not self.b_requests_visible_on_a:
            reasons.append(
                f"{len(self.b_requests_missing_on_a)} Human B request events not visible on Domain A"
            )
        if self.membership_a != self.expected_membership:
            reasons.append(f"Domain A membership {sorted(self.membership_a)} != expected")
        if self.membership_b != self.expected_membership:
            reasons.append(f"Domain B membership {sorted(self.membership_b)} != expected")
        return reasons
