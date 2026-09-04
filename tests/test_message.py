"""Deterministic message parsing, construction and correlation."""

from __future__ import annotations

import pytest

from fam.common.frozen import E3_BODY_BYTES
from fam.common.message import (
    Correlation,
    MessageFormatError,
    assert_body_length,
    build_ack,
    build_request,
    parse,
)


def test_request_round_trip():
    correlation = Correlation("E0", "e0-run-01", 421)
    body = build_request(correlation)
    assert body == "FAM/1 REQUEST E0 e0-run-01 00421"

    parsed = parse(body)
    assert parsed is not None
    assert parsed.is_request
    assert parsed.correlation.key() == correlation.key()


def test_ack_round_trip():
    correlation = Correlation("E0", "e0-run-01", 1)
    parsed = parse(build_ack(correlation))
    assert parsed is not None
    assert parsed.is_ack
    assert parsed.correlation.sequence_id == 1


def test_sequence_is_zero_padded_to_five_digits():
    assert build_request(Correlation("E0", "r", 7)).endswith(" 00007")


def test_transaction_ids_are_deterministic_and_direction_specific():
    correlation = Correlation("E3", "run017", 421)
    assert correlation.txn_id("request") == "fam-e3-run017-request-00421"
    assert correlation.txn_id("response") == "fam-e3-run017-response-00421"
    # Stable across calls: a retry of the same logical send reuses it.
    assert correlation.txn_id("request") == correlation.txn_id("request")


def test_unknown_direction_rejected():
    with pytest.raises(MessageFormatError):
        Correlation("E0", "r", 1).txn_id("sideways")


def test_experiment_case_is_accepted_either_way():
    """The frozen examples disagree on case; parsing must not care."""
    lower = parse("FAM/1 REQUEST e3 run-017 00421")
    upper = parse("FAM/1 REQUEST E3 run-017 00421")
    assert lower is not None and upper is not None
    assert lower.correlation.key() == upper.correlation.key()


@pytest.mark.parametrize(
    "body",
    [
        "hello there",
        "FAM/2 REQUEST E0 r 00001",
        "FAM/1 SHOUT E0 r 00001",
        "FAM/1 REQUEST E0 r",
        "FAM/1 REQUEST E0 r abc",
        "",
    ],
)
def test_non_experimental_bodies_are_ignored_not_raised(body):
    """The agent shares rooms with ordinary traffic and must ignore it."""
    assert parse(body) is None


def test_padding_produces_exactly_the_frozen_size():
    correlation = Correlation("E3", "run017", 421)
    for builder in (build_request, build_ack):
        body = builder(correlation, body_bytes=E3_BODY_BYTES)
        assert len(body.encode("utf-8")) == E3_BODY_BYTES
        assert_body_length(body)


def test_padded_body_still_parses_to_the_same_correlation():
    correlation = Correlation("E3", "run017", 421)
    padded = build_request(correlation, body_bytes=E3_BODY_BYTES)
    parsed = parse(padded)
    assert parsed is not None
    assert parsed.padded is True
    assert parsed.correlation.key() == correlation.key()


def test_padding_is_ascii_only_so_bytes_equal_characters():
    body = build_request(Correlation("E3", "run017", 1), body_bytes=E3_BODY_BYTES)
    assert body.isascii()
    assert len(body) == len(body.encode("utf-8")) == E3_BODY_BYTES


def test_wrong_length_body_is_a_defect_not_a_tolerance():
    with pytest.raises(MessageFormatError):
        assert_body_length("FAM/1 REQUEST E3 r 00001")


def test_identifiers_with_whitespace_are_rejected():
    with pytest.raises(MessageFormatError):
        Correlation("E0", "run 17", 1)
