"""Constants frozen by the research specifications.

Every value here traces to a frozen document. Nothing in this module may be
changed without the corresponding version increment described in
experimental-protocol.md §3 Phase 4.
"""

from __future__ import annotations

# --- testbed-architecture.md §4, §8 -----------------------------------------

ROOM_VERSION = "12"
ROOM_ENCRYPTION_ENABLED = False
MESSAGE_EVENT_TYPE = "m.room.message"

# --- testbed-architecture.md §17, experimental-protocol.md §7 ---------------

PROTOCOL_TOKEN = "FAM/1"
REQUEST_KEYWORD = "REQUEST"
ACK_KEYWORD = "ACK"

#: E3 only. Request and response bodies are exactly this many UTF-8 bytes.
#: E0-E2 may use the unpadded envelope (experimental-protocol.md §7).
E3_BODY_BYTES = 256

#: Deterministic filler appended after the correlation envelope.
PADDING_CHARACTER = "x"

# --- experimental-protocol.md §11 -------------------------------------------

DEFAULT_INTERACTION_TIMEOUT_SECONDS = 10.0

# --- experimental-protocol.md §14 -------------------------------------------

E0_RUNS = 3
E0_REQUESTS_PER_PHASE = 20
E0_REQUESTS_PER_RUN = E0_REQUESTS_PER_PHASE * 2

# --- experimental-protocol.md §15 -------------------------------------------

E1_RUNS = 3
E1_REQUESTS_PER_CLASS = 20
E1_REQUESTS_PER_RUN = E1_REQUESTS_PER_CLASS * 2

#: Applied after every expected event has been observed. A practical
#: observation boundary only: it is not evidence of Matrix convergence, of any
#: eventual-consistency bound, or of the absence of later state changes.
E1_QUIET_INTERVAL_SECONDS = 2.0

# --- versioning -------------------------------------------------------------
#
# Schema versions are frozen at protocol lock (experimental-protocol.md §46).
# Task 01 is pre-lock development, so these carry a development marker.

RAW_SCHEMA_VERSION = "1"
MANIFEST_SCHEMA_VERSION = "1"

#: Set at protocol lock. Development runs record the value that was current
#: when they executed; a later analysis-spec revision does not invalidate them
#: (experimental-protocol.md §35, §38).
EXECUTION_PROTOCOL_VERSION = "1.1-dev"
EXECUTION_ANALYSIS_SPEC_VERSION = "1.1-dev"
