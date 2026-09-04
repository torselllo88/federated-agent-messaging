"""E1 federation comparison: exact event-set equality and membership.

These exercise the comparison logic directly. Real federation behaviour is
covered by the E1 integration run itself, not by mocking a homeserver.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fam.instrumentation.federation import FederationComparison
from fam.common.domain_view import DomainView

HUMAN_A = "@human-a:hs-a.test"
HUMAN_B = "@human-b:hs-b.test"
AGENT = "@agent:hs-b.test"
EXPECTED_MEMBERSHIP = {HUMAN_A, HUMAN_B, AGENT}

A_REQUESTS = {f"$a{i}" for i in range(1, 21)}
B_REQUESTS = {f"$b{i}" for i in range(1, 21)}
ACKS = {f"$k{i}" for i in range(1, 41)}
ALL_EXPECTED = A_REQUESTS | B_REQUESTS | ACKS


def view(domain: str, observer: str, event_ids: set[str], membership=None) -> DomainView:
    return DomainView(
        domain=domain,
        observer=observer,
        experimental_event_ids=set(event_ids),
        membership=sorted(membership if membership is not None else EXPECTED_MEMBERSHIP),
    )


def comparison(set_a: set[str], set_b: set[str], mem_a=None, mem_b=None):
    item = FederationComparison(
        run_id="e1-test-01",
        room_id="!room",
        quiet_interval_seconds=2.0,
        expected_request_ids_a=set(A_REQUESTS),
        expected_request_ids_b=set(B_REQUESTS),
        expected_ack_ids=set(ACKS),
        expected_membership=set(EXPECTED_MEMBERSHIP),
    )
    item.expected_event_ids = set(ALL_EXPECTED)
    item.view_a = view("A", HUMAN_A, set_a, mem_a)
    item.view_b = view("B", HUMAN_B, set_b, mem_b)
    return item


# ------------------------------------------------------------ set equality


def test_identical_views_pass():
    item = comparison(ALL_EXPECTED, ALL_EXPECTED)
    assert item.event_set_equal
    assert item.overall_result
    assert item.failure_reasons() == []
    for empty in (item.missing_on_a, item.missing_on_b, item.unexpected_on_a, item.unexpected_on_b):
        assert empty == set()


def test_missing_event_on_domain_b_is_detected():
    lost = next(iter(A_REQUESTS))
    item = comparison(ALL_EXPECTED, ALL_EXPECTED - {lost})
    assert item.missing_on_b == {lost}
    assert not item.event_set_equal
    assert not item.a_requests_visible_on_b
    assert item.a_requests_missing_on_b == {lost}
    assert not item.overall_result


def test_missing_event_on_domain_a_is_detected():
    lost = next(iter(B_REQUESTS))
    item = comparison(ALL_EXPECTED - {lost}, ALL_EXPECTED)
    assert item.missing_on_a == {lost}
    assert not item.b_requests_visible_on_a
    assert item.b_requests_missing_on_a == {lost}
    assert not item.overall_result


def test_unexpected_event_is_detected_not_ignored():
    """An extra experimental event must fail, not be quietly tolerated."""
    item = comparison(ALL_EXPECTED | {"$rogue"}, ALL_EXPECTED)
    assert item.unexpected_on_a == {"$rogue"}
    assert not item.event_set_equal
    assert any("unexpected" in reason for reason in item.failure_reasons())


def test_equal_counts_with_different_members_still_fail():
    """Counts alone are not equality."""
    swapped = (ALL_EXPECTED - {"$a1"}) | {"$other"}
    item = comparison(ALL_EXPECTED, swapped)
    assert len(item.set_a) == len(item.set_b)
    assert not item.event_set_equal
    assert item.missing_on_b == {"$a1"}
    assert item.unexpected_on_b == {"$other"}


def test_both_domains_missing_the_same_event_still_fails():
    lost = next(iter(ACKS))
    item = comparison(ALL_EXPECTED - {lost}, ALL_EXPECTED - {lost})
    assert item.set_a == item.set_b
    assert not item.event_set_equal, "A == B is not sufficient; both must equal expected"


# ------------------------------------------------------------- directional


def test_directional_visibility_is_reported_separately():
    item = comparison(ALL_EXPECTED, ALL_EXPECTED)
    assert item.a_requests_visible_on_b
    assert item.b_requests_visible_on_a


def test_same_domain_requests_must_still_reach_the_other_domain():
    """Human B -> agent is same-domain, but its events evidence B -> A."""
    lost = next(iter(B_REQUESTS))
    item = comparison(ALL_EXPECTED - {lost}, ALL_EXPECTED)
    assert not item.b_requests_visible_on_a


# -------------------------------------------------------------- membership


def test_membership_must_match_exactly_on_both_domains():
    item = comparison(ALL_EXPECTED, ALL_EXPECTED)
    assert item.membership_compatible


def test_missing_member_fails():
    item = comparison(ALL_EXPECTED, ALL_EXPECTED, mem_a={HUMAN_A, AGENT})
    assert not item.membership_compatible
    assert not item.overall_result


def test_unexpected_member_fails_even_though_all_three_are_present():
    intruder = EXPECTED_MEMBERSHIP | {"@stranger:hs-a.test"}
    item = comparison(ALL_EXPECTED, ALL_EXPECTED, mem_b=intruder)
    assert not item.membership_compatible
    assert item.unexpected_members_b == {"@stranger:hs-a.test"}


# ------------------------------------------------------ artifact provenance


def test_comparison_artifact_carries_frozen_provenance(tmp_path: Path):
    provenance = {
        "analysis_spec_version": "1.1-dev",
        "analysis_code_commit": "task-02-working-tree",
        "protocol_git_commit": "cafe1234",
        "source_run_id": "e1-test-01",
        "source_raw_digests": {"runner_interaction_stream": "0" * 64},
    }
    path = comparison(ALL_EXPECTED, ALL_EXPECTED).write(tmp_path, provenance)
    payload = json.loads(path.read_text(encoding="utf-8"))

    for required in (
        "analysis_spec_version",
        "analysis_code_commit",
        "protocol_git_commit",
        "source_run_id",
        "source_raw_digests",
        "run_id",
        "room_id",
        "expected_event_ids",
        "domain_a_event_ids",
        "domain_b_event_ids",
        "missing_on_a",
        "missing_on_b",
        "unexpected_on_a",
        "unexpected_on_b",
        "expected_membership",
        "domain_a_membership",
        "domain_b_membership",
        "event_set_equal",
        "membership_compatible",
        "quiet_interval_seconds",
        "overall_result",
    ):
        assert required in payload, required

    assert payload["overall_result"] == "PASS"
    assert payload["quiet_interval_seconds"] == 2.0


def test_artifact_lists_are_sorted_and_json_serializable(tmp_path: Path):
    item = comparison(ALL_EXPECTED - {"$a1"}, ALL_EXPECTED)
    payload = json.loads(item.write(tmp_path, {}).read_text(encoding="utf-8"))
    assert payload["missing_on_a"] == ["$a1"]
    assert payload["domain_a_event_ids"] == sorted(payload["domain_a_event_ids"])
    assert payload["overall_result"] == "FAIL"


# ------------------------------------------------------------ domain views


def test_domain_view_summary_labels_its_domain_and_observer():
    item = DomainView(domain="B", observer=HUMAN_B)
    item.experimental_event_ids = {"$x"}
    item.request_event_ids = {"$x"}
    item.non_experimental_events = 7
    item.total_events_seen = 8
    summary = item.summary()
    assert summary["domain"] == "B"
    assert summary["observer"] == HUMAN_B
    assert summary["experimental_events"] == 1
    assert summary["non_experimental_events"] == 7
