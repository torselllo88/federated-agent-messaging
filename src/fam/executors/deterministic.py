"""The deterministic executor used by E0-E3.

For a given valid input the response is deterministic. It calls no LLM, no
external API, no random generator and no external tool, and its processing
cost is small and stable relative to communication latency
(testbed-architecture.md §14.1).
"""

from __future__ import annotations

from fam.common.frozen import E3_BODY_BYTES, REQUEST_KEYWORD
from fam.common.message import ParsedMessage, assert_body_length, build_ack


class DeterministicExecutor:
    """One deterministic ACK for every valid REQUEST. Nothing else."""

    name = "deterministic"

    def __init__(self, body_bytes: int | None = None) -> None:
        """``body_bytes`` pads responses to an exact size.

        E0-E2 leave it ``None``. E3 passes
        :data:`fam.common.frozen.E3_BODY_BYTES`; the parameter exists now so
        that introducing the frozen fixed-size body later is a configuration
        change rather than a rewrite.
        """
        if body_bytes is not None and body_bytes != E3_BODY_BYTES:
            raise ValueError(
                f"only the frozen body size {E3_BODY_BYTES} is permitted"
            )
        self.body_bytes = body_bytes

    def decide(self, message: ParsedMessage) -> str | None:
        if message.kind != REQUEST_KEYWORD:
            return None
        body = build_ack(message.correlation, body_bytes=self.body_bytes)
        if self.body_bytes is not None:
            # Asserted before send, on the ACK as well as the request: E3
            # symmetry is a property of both directions (§5).
            assert_body_length(body, self.body_bytes)
        return body
