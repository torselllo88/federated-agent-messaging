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

# --- experimental-protocol.md §16 -------------------------------------------

E2_RUNS = 3
E2_OFFLINE_REQUESTS = 100

#: Selected by the development pilot (scripts/e2_pilot.py). Must stay below
#: E2_OFFLINE_REQUESTS, or the post-restart sync returns everything and the
#: recovery branch under test is never entered.
E2_SYNC_TIMELINE_LIMIT = 10

# --- experimental-protocol.md §18, §29 --------------------------------------

#: Task 05 pilot: peak workload timeline occupancy was 21-25 events, so this
#: leaves roughly twenty times headroom and no interaction reached the agent
#: through gap recovery.
E3_SYNC_TIMELINE_LIMIT = 500
E3_SYNC_TIMEOUT_MS = 30_000

# --- versioning -------------------------------------------------------------
#
# Schema versions are frozen at protocol lock (experimental-protocol.md §46).
# The development marker was dropped for the Task 07 lock: these are the
# values the formal campaign executes under.

#: v2 adds the duplicate-ACK integrity evidence required by §11.1. Records
#: written under v1 remain valid under the v1 schema: raw data is immutable,
#: so a schema revision versions forward rather than invalidating history.
RAW_SCHEMA_VERSION = "2"
SUPPORTED_RAW_SCHEMA_VERSIONS = ("1", "2")
MANIFEST_SCHEMA_VERSION = "1"

#: Set at protocol lock. Development runs record the value that was current
#: when they executed; a later analysis-spec revision does not invalidate them
#: (experimental-protocol.md §35, §38).
#: Two counters, moved independently and for different reasons. They happen to
#: carry the same number here; that is coincidence, not coupling (§3 Phase 4).
#:
#: protocol_version 1.2      H2: the terminal outcome taxonomy lost
#:                           `duplicate_response`, and duplicate ACKs became
#:                           post-terminal integrity observations with new raw
#:                           evidence fields. Execution semantics changed.
#: analysis_spec_version 1.2 the failure rate no longer counts duplicate ACKs,
#:                           and `median` received one statistical definition.
#:                           Transformation of raw observations changed; the
#:                           observations themselves did not.
EXECUTION_PROTOCOL_VERSION = "1.2"
EXECUTION_ANALYSIS_SPEC_VERSION = "1.2"

# --- E3, experimental-protocol.md §18, §21, §22, §24, §25 -------------------
#
# Frozen methodology. Task 05 does not choose these; it executes them.

#: §18. Benchmark topology identifiers, used verbatim in raw records,
#: manifests, the schedule and analysis output.
E3_TOPOLOGY_LOCAL = "local"
E3_TOPOLOGY_FEDERATED = "federated"
E3_TOPOLOGIES = (E3_TOPOLOGY_LOCAL, E3_TOPOLOGY_FEDERATED)

#: §21. Workload A — low-load latency.
E3_LATENCY_MAX_IN_FLIGHT = 1
E3_LATENCY_WARMUP_INTERACTIONS = 50
E3_LATENCY_MEASURED_INTERACTIONS = 500

#: §22. Workload B — concurrent throughput. C = 1 is deliberately absent and
#: is recovered as a derived consistency check in analysis (§31).
E3_CONCURRENCY_LEVELS = (8, 32)
E3_WARMUP_SECONDS = 10.0
E3_MEASUREMENT_SECONDS = 60.0
E3_DRAIN_SECONDS = 10.0

#: §21, §22. Twenty paired blocks per workload.
E3_PAIRED_BLOCKS = 20

#: §25. Inter-run quiescence.
E3_INTER_RUN_IDLE_SECONDS = 5.0

#: §32. Paired-block bootstrap.
#: The protocol (§32) fixes the resampling method and the confidence level but
#: not the replicate count. Two thousand is comfortably enough for a 95%
#: percentile interval over twenty clusters and keeps the analysis interactive.
#: `fam.analysis.e3` imports this rather than keeping a second number: two
#: constants disagreeing about one frozen parameter is how a lock records a
#: value the code does not use.
E3_BOOTSTRAP_REPLICATES = 2000
E3_BOOTSTRAP_SEED = 20260905
E3_SCHEDULE_SEED = 20260905
E3_BOOTSTRAP_CONFIDENCE = 0.95

#: Workload identifiers.
E3_WORKLOAD_LATENCY = "latency"
E3_WORKLOAD_THROUGHPUT = "throughput"

#: §22. The three named periods of a throughput run. No parallel names exist.
E3_PHASES = ("warmup", "window", "drain")
