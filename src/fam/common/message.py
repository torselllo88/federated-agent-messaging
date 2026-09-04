"""The controlled message format and correlation identifiers.

Body format, testbed-architecture.md §17 / experimental-protocol.md §7::

    FAM/1 REQUEST <experiment> <run> <sequence>
    FAM/1 ACK     <experiment> <run> <sequence>

Transaction identifiers, testbed-architecture.md §18::

    fam-<experiment>-<run>-<direction>-<sequence>

Case of the experiment token inside the body is ambiguous between two frozen
examples: testbed-architecture.md §14.1 shows lowercase ``e3`` in an executor
illustration, §17 shows uppercase ``E3`` where the format is defined. This
implementation follows §17 (uppercase in the body) and §18 (lowercase in the
transaction id), so both frozen examples are honoured, and parsing accepts
either case so the choice cannot silently break correlation. Reported as an
ambiguity rather than resolved in the frozen document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fam.common.frozen import (
    ACK_KEYWORD,
    E3_BODY_BYTES,
    PADDING_CHARACTER,
    PROTOCOL_TOKEN,
    REQUEST_KEYWORD,
)

SEQUENCE_DIGITS = 5

#: Run and experiment identifiers appear in a space-delimited body, so they
#: may not contain whitespace. Enforced rather than assumed.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]+$")

_BODY_RE = re.compile(
    r"^(?P<proto>FAM/1)\s+"
    r"(?P<kind>REQUEST|ACK)\s+"
    r"(?P<experiment>[A-Za-z0-9._:-]+)\s+"
    r"(?P<run>[A-Za-z0-9._:-]+)\s+"
    r"(?P<sequence>\d+)"
    r"(?P<padding>\s+x*)?$"
)


class MessageFormatError(ValueError):
    """The body is not a well-formed FAM/1 message."""


def _check_token(name: str, value: str) -> str:
    if not _TOKEN_RE.match(value):
        raise MessageFormatError(
            f"{name} must be a whitespace-free token matching "
            f"[A-Za-z0-9._:-]+, got {value!r}"
        )
    return value


@dataclass(frozen=True)
class Correlation:
    """Scientific identity of one logical interaction.

    experimental-protocol.md §9, §13: application-level identity is
    experiment + run + sequence. Matrix ``event_id`` provides event identity;
    ``txn_id`` provides client-send idempotency and replaces neither.
    """

    experiment: str
    run_id: str
    sequence_id: int

    def __post_init__(self) -> None:
        _check_token("experiment", self.experiment)
        _check_token("run_id", self.run_id)
        if self.sequence_id < 0:
            raise MessageFormatError("sequence_id must be non-negative")

    @property
    def sequence_token(self) -> str:
        return str(self.sequence_id).zfill(SEQUENCE_DIGITS)

    def key(self) -> tuple[str, str, int]:
        return (self.experiment.upper(), self.run_id, self.sequence_id)

    def txn_id(self, direction: str) -> str:
        """Deterministic Matrix transaction identifier.

        A retry of the same logical send reuses this value, which is what
        makes client retransmission idempotent at the protocol level
        (testbed-architecture.md §18).
        """
        if direction not in ("request", "response"):
            raise MessageFormatError("direction must be 'request' or 'response'")
        return (
            f"fam-{self.experiment.lower()}-{self.run_id.lower()}"
            f"-{direction}-{self.sequence_token}"
        )


def _envelope(kind: str, correlation: Correlation) -> str:
    return (
        f"{PROTOCOL_TOKEN} {kind} {correlation.experiment.upper()} "
        f"{correlation.run_id} {correlation.sequence_token}"
    )


def _pad(envelope: str, body_bytes: int | None) -> str:
    if body_bytes is None:
        return envelope
    encoded = len(envelope.encode("utf-8"))
    if encoded > body_bytes - 1:
        raise MessageFormatError(
            f"envelope is {encoded} bytes; cannot pad to {body_bytes} "
            "with a separator"
        )
    filler = PADDING_CHARACTER * (body_bytes - encoded - 1)
    return f"{envelope} {filler}"


def build_request(correlation: Correlation, body_bytes: int | None = None) -> str:
    """Build a REQUEST body.

    ``body_bytes`` pads to an exact UTF-8 length. E0-E2 pass ``None``; E3
    passes :data:`fam.common.frozen.E3_BODY_BYTES`.
    """
    return _pad(_envelope(REQUEST_KEYWORD, correlation), body_bytes)


def build_ack(correlation: Correlation, body_bytes: int | None = None) -> str:
    return _pad(_envelope(ACK_KEYWORD, correlation), body_bytes)


def assert_body_length(body: str, expected_bytes: int = E3_BODY_BYTES) -> None:
    """Fail loudly on a body of the wrong encoded length.

    A body that is not exactly the frozen size is a defect, not a tolerance
    (testbed-architecture.md §17).
    """
    actual = len(body.encode("utf-8"))
    if actual != expected_bytes:
        raise MessageFormatError(
            f"body is {actual} encoded bytes, frozen size is {expected_bytes}"
        )


@dataclass(frozen=True)
class ParsedMessage:
    kind: str
    correlation: Correlation
    padded: bool

    @property
    def is_request(self) -> bool:
        return self.kind == REQUEST_KEYWORD

    @property
    def is_ack(self) -> bool:
        return self.kind == ACK_KEYWORD


def parse(body: str) -> ParsedMessage | None:
    """Parse a FAM/1 body, or return ``None`` if it is not one.

    Returning ``None`` rather than raising is deliberate: the agent shares
    rooms with ordinary chat traffic and must ignore anything that is not a
    valid experimental message (experimental-protocol.md §7).
    """
    if not isinstance(body, str):
        return None
    match = _BODY_RE.match(body.strip())
    if not match:
        return None
    try:
        correlation = Correlation(
            experiment=match.group("experiment"),
            run_id=match.group("run"),
            sequence_id=int(match.group("sequence")),
        )
    except MessageFormatError:
        return None
    return ParsedMessage(
        kind=match.group("kind"),
        correlation=correlation,
        padded=bool(match.group("padding")),
    )
