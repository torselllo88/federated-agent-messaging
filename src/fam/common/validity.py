"""Formal-run validity classification.

experimental-protocol.md §35 is the single authoritative definition. This
module mirrors its closed taxonomy as a machine-readable enum; the document
remains the source of truth. The list is closed at protocol lock — adding a
member here is an execution-affecting change that increments
``protocol_version`` (experimental-protocol.md §3 Phase 4).

Free-text classification is not acceptable. A human-readable note may
accompany a class but never replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InvalidRunClass(str, Enum):
    """The nine classes of experimental-protocol.md §35, verbatim."""

    PROTOCOL_LOCK_MISMATCH = "protocol_lock_mismatch"
    EXECUTION_PRECONDITION_VIOLATION = "execution_precondition_violation"
    FROZEN_CONFIGURATION_ERROR = "frozen_configuration_error"
    INSTRUMENTATION_OR_OUTPUT_FAILURE = "instrumentation_or_output_failure"
    RUNNER_IMPLEMENTATION_FAILURE = "runner_implementation_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    ENVIRONMENT_CORRUPTION = "environment_corruption"
    EXTERNAL_INTERFERENCE = "external_interference"
    EXTERNAL_DEPENDENCY_OR_CLIENT_ENVIRONMENT_FAILURE = (
        "external_dependency_or_client_environment_failure"
    )


class InteractionOutcome(str, Enum):
    """Per-interaction outcomes, experimental-protocol.md §11.

    This taxonomy classifies raw observations during execution and is
    governed by ``protocol_version``. It is distinct from run-level validity.
    """

    SUCCESS = "success"
    TIMEOUT = "timeout"
    SEND_ERROR = "send_error"
    MALFORMED_RESPONSE = "malformed_response"
    DUPLICATE_RESPONSE = "duplicate_response"
    UNEXPECTED_RESPONSE = "unexpected_response"
    RUNNER_ERROR = "runner_error"

    #: E2 only: sent while the agent runtime is stopped. Not a logical
    #: interaction under §9 and excluded from the failure-rate denominator
    #: until its deadline begins.
    OFFLINE_SEND = "offline_send"


@dataclass(frozen=True)
class RunValidity:
    """A run is valid, or invalid under exactly one class."""

    valid: bool
    invalid_class: InvalidRunClass | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.valid and self.invalid_class is not None:
            raise ValueError("a valid run carries no invalidity class")
        if not self.valid and self.invalid_class is None:
            raise ValueError(
                "an invalid run must name exactly one §35 class; free-text "
                "classification is not acceptable"
            )

    def to_manifest(self) -> dict:
        return {
            "valid": self.valid,
            "invalid_class": self.invalid_class.value if self.invalid_class else None,
            "note": self.note,
        }


VALID = RunValidity(valid=True)


def invalid(cls: InvalidRunClass, note: str = "") -> RunValidity:
    return RunValidity(valid=False, invalid_class=cls, note=note)


class InvalidRun(Exception):
    """Raised when a run cannot proceed for a §35 reason.

    An experimental failure is NOT raised as this exception. A failure
    produced by the correctly configured testbed under the tested workload is
    an experimental outcome and is recorded, not raised away.
    """

    def __init__(self, cls: InvalidRunClass, note: str) -> None:
        super().__init__(f"{cls.value}: {note}")
        self.validity = invalid(cls, note)
