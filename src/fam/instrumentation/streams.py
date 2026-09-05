"""Append-only JSONL observation streams.

Two streams per run, joined by ``run_id`` (testbed-architecture.md §22):

* the runner interaction stream — one record per logical interaction;
* the agent telemetry stream — what no external observer can see.

Raw records carry execution facts and the frozen metadata needed to
reconstruct a classification. They never carry the classification itself:
``counted_in_window`` and similar derived values are computed during analysis
under the frozen analysis specification, so a later estimator revision cannot
make already-written raw data wrong.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fam.common.frozen import RAW_SCHEMA_VERSION


def monotonic_ns() -> int:
    """The single clock used for every primary timing observation.

    experimental-protocol.md §10: T0 and T3 come from one monotonic clock in
    one process. No wall-clock and no ``origin_server_ts`` anywhere near a
    latency measurement.
    """
    return time.monotonic_ns()


class JsonlStream:
    """Append-only writer. Flushed and fsynced per record.

    Durability per record matters more than throughput here: a run that dies
    mid-way should leave usable evidence of how far it got, and raw outputs
    are immutable once written (experimental-protocol.md §34).
    """

    def __init__(self, path: Path, stream_kind: str) -> None:
        self.path = path
        self.stream_kind = stream_kind
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8", newline="\n")
        self._count = 0

    @property
    def record_count(self) -> int:
        return self._count

    def write(self, record: dict[str, Any]) -> None:
        payload = {"schema_version": RAW_SCHEMA_VERSION, "stream": self.stream_kind}
        payload.update(record)
        self._handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        self._handle.write("\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._count += 1

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()

    def __enter__(self) -> "JsonlStream":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def runner_record(
    *,
    experiment: str,
    topology: str,
    run_id: str,
    sequence_id: int,
    run_phase: str,
    room_id: str,
    sender: str,
    request_class: str = "",
    receiver_role: str,
    request_txn_id: str,
    request_event_id: str | None,
    response_txn_id: str | None,
    response_event_id: str | None,
    initiated_monotonic_ns: int,
    completed_monotonic_ns: int | None,
    outcome: str,
    note: str = "",
) -> dict[str, Any]:
    """One logical interaction as seen by the runner.

    ``initiated_monotonic_ns`` is T0 and ``completed_monotonic_ns`` is T3
    (experimental-protocol.md §10). ``run_phase`` distinguishes E0's
    pre-restart and post-restart phases; it is not the E3 window ``phase``,
    which does not apply to E0.
    """
    return {
        "record_type": "interaction",
        "experiment": experiment,
        "topology": topology,
        "run_id": run_id,
        "sequence_id": sequence_id,
        "run_phase": run_phase,
        "request_class": request_class,
        "room_id": room_id,
        "sender": sender,
        "receiver_role": receiver_role,
        "request_txn_id": request_txn_id,
        "request_event_id": request_event_id,
        "response_txn_id": response_txn_id,
        "response_event_id": response_event_id,
        "initiated_monotonic_ns": initiated_monotonic_ns,
        "completed_monotonic_ns": completed_monotonic_ns,
        "outcome": outcome,
        "note": note,
    }


def runner_marker(
    *, experiment: str, run_id: str, marker: str, **fields
) -> dict[str, Any]:
    """A phase boundary, not an interaction.

    E2 needs the agent-stop and restart boundaries in the raw record so the
    offline window and the start of the response deadline are reconstructable
    (experimental-protocol.md §11).
    """
    return {
        "record_type": "marker",
        "experiment": experiment,
        "run_id": run_id,
        "marker": marker,
        **fields,
    }


def agent_record(
    *,
    experiment: str,
    run_id: str,
    sequence_id: int | None,
    room_id: str,
    agent_mxid: str,
    sender: str = "",
    request_event_id: str | None = None,
    received_monotonic_ns: int | None,
    processed_monotonic_ns: int | None,
    response_txn_id: str | None,
    response_event_id: str | None,
    duplicate_decision: str,
    action: str,
    sync_token_present: bool,
    history_pagination_invoked: bool = False,
    note: str = "",
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One agent-side observation.

    E2's acceptance criteria are agent-side facts, so this stream is
    mandatory rather than diagnostic. It uses no privileged interface — it is
    the runtime's own record of its own behaviour — and therefore does not
    affect C2.

    ``duplicate_decision`` is one of ``processed``, ``skipped_duplicate``,
    ``skipped_not_request``, ``skipped_own_event``.
    """
    return {
        "experiment": experiment,
        "run_id": run_id,
        "sequence_id": sequence_id,
        "room_id": room_id,
        "agent_mxid": agent_mxid,
        "sender": sender,
        "request_event_id": request_event_id,
        "received_monotonic_ns": received_monotonic_ns,
        "processed_monotonic_ns": processed_monotonic_ns,
        "response_txn_id": response_txn_id,
        "response_event_id": response_event_id,
        "duplicate_decision": duplicate_decision,
        "action": action,
        "sync_token_present": sync_token_present,
        "history_pagination_invoked": history_pagination_invoked,
        "note": note,
        # E4 only: provider-side facts about the executor call. Counts and
        # identifiers, never the payload — the Matrix transcript already holds
        # the request and the response (Task 06 §27).
        "execution": execution,
    }


def benchmark_record(
    *,
    workload: str,
    block_id: str,
    within_block_order: int,
    concurrency: int,
    message_body_bytes: int,
    phase: str,
    window_start_ns: int | None = None,
    window_end_ns: int | None = None,
    live_recovery_episode: int | None = None,
    send_errcode: str = "",
    late_ack_monotonic_ns: int | None = None,
    **runner_fields,
) -> dict[str, Any]:
    """One E3 benchmark interaction: a runner record plus the E3 fields.

    This extends the existing runner stream rather than opening a second one.
    The record is the same ``record_type: interaction`` shape, validated by
    the same schema, with the fields §22 requires for the throughput
    estimator added alongside.

    ``phase`` says which period of the run this interaction belongs to. It is
    a convenience label, **not** the estimator: analysis derives window
    membership from ``completed_monotonic_ns`` against ``window_start_ns`` and
    ``window_end_ns``, exactly as §22 specifies, and never reads ``phase``.
    That is what keeps a later estimator revision from making already-written
    raw data wrong.
    """
    record = runner_record(**runner_fields)
    record.update(
        {
            "workload": workload,
            "block_id": block_id,
            "within_block_order": within_block_order,
            "concurrency": concurrency,
            "message_body_bytes": message_body_bytes,
            "phase": phase,
            "window_start_ns": window_start_ns,
            "window_end_ns": window_end_ns,
            "live_recovery_episode": live_recovery_episode,
            "send_errcode": send_errcode,
            "late_ack_monotonic_ns": late_ack_monotonic_ns,
        }
    )
    return record
