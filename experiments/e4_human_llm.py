#!/usr/bin/env python3
"""E4 — Human + LLM Functional Validation. One development session.

experimental-protocol.md §41. A three-party federated room holds an actual
person using a standard Matrix client on Domain A, a programmatic human-role
participant on Domain B, and an LLM-backed agent on Domain B. The person sends
at least three natural-language requests and sees at least three corresponding
LLM-backed responses.

This is the one experiment that cannot be automated, and deliberately so: C4's
completion rests on a real human being present (research-scope.md §6 C4,
*Empirical support*). The runner creates the room, brings up the agent, waits,
and records what happened. It never sends a request on the human's behalf.

It also never acts on the human's account. The room is created by
``@human-role-b:hs-b.test`` — a participant this project legitimately drives —
which invites the other two. The human accepts the invitation in their own
client, so their join is itself a cross-domain federation operation and every
message they send is genuinely theirs.

The agent runs the same communication runtime as E0-E3. Only the executor and
the request protocol differ (testbed-architecture.md §14.2).

Task 06 sessions are development validation: publication_data is false.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")

from fam.agent.supervisor import AgentProcess  # noqa: E402
from fam.common.digests import file_sha256  # noqa: E402
from fam.common.env import (  # noqa: E402
    account,
    agent_state_dir,
    protocol_git_commit,
    publication_data,
)
from fam.common.frozen import MESSAGE_EVENT_TYPE  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    evidence_dir,
    manifests_dir,
    resolve_results_dir,
)
from fam.common.validity import (  # noqa: E402
    VALID,
    InvalidRun,
    InvalidRunClass,
    invalid,
)
from fam.executors.llm import (  # noqa: E402
    ENV_API_KEY,
    ENV_MODEL,
    api_key_from_environment,
    config_from_environment,
)
from fam.instrumentation.manifest import (  # noqa: E402
    EvidenceArtifact,
    HumanValidationManifest,
)
from fam.matrix.rooms import assert_frozen_room_configuration  # noqa: E402
from fam.participants.human import HumanParticipant  # noqa: E402

EXPERIMENT = "E4"

ACTUAL_HUMAN = "@actual-human:hs-a.test"
HUMAN_ROLE_B = "@human-role-b:hs-b.test"
LLM_AGENT = "@llm-agent:hs-b.test"
EXPECTED_MEMBERSHIP = {ACTUAL_HUMAN, HUMAN_ROLE_B, LLM_AGENT}

#: experimental-protocol.md §41 step 9.
MINIMUM_REQUESTS = 3

JOIN_TIMEOUT_SECONDS = float(os.environ.get("FAM_E4_JOIN_TIMEOUT", "900"))
INTERACTION_TIMEOUT_SECONDS = float(os.environ.get("FAM_E4_TIMEOUT", "1800"))
POLL_SECONDS = 3.0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


@dataclass
class Exchange:
    """One human request and the LLM-backed response to it."""

    request_event_id: str
    request_sender: str
    request_text: str
    response_event_id: str | None = None
    response_text: str = ""
    llm_model: str = ""
    llm_provider: str = ""
    execution: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return bool(self.response_event_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_event_id": self.request_event_id,
            "request_sender": self.request_sender,
            "response_event_id": self.response_event_id,
            "response_sender": LLM_AGENT,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "execution": self.execution,
        }


@dataclass
class SessionResult:
    session_id: str
    room_id: str = ""
    room_version: str = ""
    encryption_enabled: bool = True
    membership: list[str] = field(default_factory=list)
    exchanges: list[Exchange] = field(default_factory=list)
    agent_stream: Path | None = None
    transcript_path: Path | None = None
    screenshots: list[Path] = field(default_factory=list)
    human_confirmed_visible: bool = False
    validity = VALID
    reasons: list[str] = field(default_factory=list)
    passed: bool = False
    manifest_path: Path | None = None
    execution_failures: list[dict] = field(default_factory=list)

    @property
    def completed_exchanges(self) -> list[Exchange]:
        return [item for item in self.exchanges if item.complete]


def _correspondence_from_telemetry(
    telemetry: list[dict], timeline: dict[str, dict]
) -> tuple[list[Exchange], list[dict]]:
    """Pair requests with responses using the agent's own record.

    The agent records which request event it answered and which event id the
    homeserver assigned its reply. That is exact correspondence, not inference
    from ordering or timing, and it is what §17 asks for.
    """
    exchanges: list[Exchange] = []
    failures: list[dict] = []
    for record in telemetry:
        action = record.get("action")
        request_event_id = record.get("request_event_id")
        if action == "execution_failed":
            failures.append(
                {
                    "request_event_id": request_event_id,
                    "note": record.get("note", ""),
                    "execution": record.get("execution") or {},
                }
            )
            continue
        if action != "responded" or not request_event_id:
            continue
        request_event = timeline.get(request_event_id, {})
        response_event = timeline.get(record.get("response_event_id") or "", {})
        execution = record.get("execution") or {}
        exchanges.append(
            Exchange(
                request_event_id=request_event_id,
                request_sender=request_event.get("sender", record.get("sender", "")),
                request_text=(request_event.get("content") or {}).get("body", ""),
                response_event_id=record.get("response_event_id"),
                response_text=(response_event.get("content") or {}).get("body", ""),
                llm_provider=execution.get("provider", ""),
                llm_model=execution.get("model", ""),
                execution=execution,
            )
        )
    return exchanges, failures


async def _timeline(observer: HumanParticipant, room_id: str) -> dict[str, dict]:
    """Room messages keyed by event id, read through an ordinary account."""
    events = await observer.client.fetch_all_messages(room_id)
    return {
        event["event_id"]: event
        for event in events
        if event.get("type") == MESSAGE_EVENT_TYPE
    }


async def _await_membership(
    observer: HumanParticipant, room_id: str, deadline: float
) -> list[str]:
    loop = asyncio.get_running_loop()
    last: list[str] = []
    announced = False
    while loop.time() < deadline:
        last = await observer.client.joined_members(room_id)
        if set(last) >= EXPECTED_MEMBERSHIP:
            return last
        if not announced:
            missing = sorted(EXPECTED_MEMBERSHIP - set(last))
            print(f"    waiting for {missing} to join…")
            announced = True
        await asyncio.sleep(POLL_SECONDS)
    return last


async def _await_exchanges(
    observer: HumanParticipant,
    room_id: str,
    agent_stream: Path,
    deadline: float,
) -> tuple[list[Exchange], list[dict]]:
    """Wait until the human has been answered at least three times."""
    loop = asyncio.get_running_loop()
    exchanges: list[Exchange] = []
    failures: list[dict] = []
    reported = -1
    while loop.time() < deadline:
        timeline = await _timeline(observer, room_id)
        exchanges, failures = _correspondence_from_telemetry(
            read_jsonl(agent_stream), timeline
        )
        from_human = [e for e in exchanges if e.request_sender == ACTUAL_HUMAN]
        if len(from_human) != reported:
            reported = len(from_human)
            remaining = max(0, MINIMUM_REQUESTS - reported)
            print(
                f"    {reported} answered request(s) from the human"
                + (f"; {remaining} more needed" if remaining else "; complete")
            )
        if len(from_human) >= MINIMUM_REQUESTS:
            return exchanges, failures
        await asyncio.sleep(POLL_SECONDS)
    return exchanges, failures


def _write_transcript(result: SessionResult, root: Path, llm: Any) -> Path:
    """Sanitized machine-readable transcript. Primary evidence (§18)."""
    directory = evidence_dir(root) / result.session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "transcript.json"
    payload = {
        "artifact": "e4_session_transcript",
        "experiment": EXPERIMENT,
        "session_id": result.session_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publication_data": publication_data(),
        "room_id": result.room_id,
        "room_version": result.room_version,
        "participants": sorted(result.membership),
        "llm": llm.public(),
        "agent_configuration_hash": llm.config_hash(),
        "exchanges": [
            {
                **exchange.to_dict(),
                "request_text": exchange.request_text,
                "response_text": exchange.response_text,
            }
            for exchange in result.exchanges
        ],
        "execution_failures": result.execution_failures,
        "scope_note": (
            "Functional validation only. No latency, throughput or "
            "model-quality claim is derived from this session "
            "(experimental-protocol.md §41)."
        ),
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def _evaluate(result: SessionResult) -> None:
    """Acceptance criteria, experimental-protocol.md §41 and Task 06 §21."""
    reasons: list[str] = []

    if result.room_version != "12":
        reasons.append(f"room version {result.room_version!r}, frozen value is '12'")
    if result.encryption_enabled:
        reasons.append("room encryption enabled")

    missing = sorted(EXPECTED_MEMBERSHIP - set(result.membership))
    if missing:
        reasons.append(f"missing from the room: {missing}")
    unexpected = sorted(set(result.membership) - EXPECTED_MEMBERSHIP)
    if unexpected:
        reasons.append(f"unexpected participants: {unexpected}")

    from_human = [
        e for e in result.completed_exchanges if e.request_sender == ACTUAL_HUMAN
    ]
    if len(from_human) < MINIMUM_REQUESTS:
        reasons.append(
            f"{len(from_human)} answered natural-language requests from "
            f"{ACTUAL_HUMAN}, the frozen minimum is {MINIMUM_REQUESTS}"
        )
    for exchange in from_human:
        if not exchange.response_text.strip():
            reasons.append(
                f"response to {exchange.request_event_id} is empty"
            )
        if not exchange.llm_model:
            reasons.append(
                f"response to {exchange.request_event_id} records no model"
            )
    if not result.human_confirmed_visible:
        reasons.append("the human did not confirm the responses were visible")
    if result.transcript_path is None:
        reasons.append("no transcript evidence written")

    result.reasons = reasons
    result.passed = not reasons


async def run_session(args: argparse.Namespace) -> SessionResult:
    root = ensure_layout(resolve_results_dir())
    session_id = args.session_id or f"e4-{utc_stamp()}"
    result = SessionResult(session_id=session_id)

    # Fail before creating anything if the provider is not configured: a
    # session that cannot possibly execute should not consume a room or a
    # session identifier.
    llm = config_from_environment()
    api_key = api_key_from_environment()

    raw = root / "raw" / "e4"
    raw.mkdir(parents=True, exist_ok=True)
    agent_path = raw / f"{session_id}.agent.jsonl"
    result.agent_stream = agent_path

    state_dir = agent_state_dir() / session_id
    state_dir.mkdir(parents=True, exist_ok=True)

    account_b = account(HUMAN_ROLE_B)
    account_agent = account(LLM_AGENT)

    # The programmatic Domain-B participant. It creates the room and observes;
    # it does not drive the interaction (Task 06 §14).
    human_role_b = HumanParticipant(
        homeserver_url=account_b.homeserver_url,
        user_id=HUMAN_ROLE_B,
        password=account_b.password,
        device_name="fam-human-role-b",
    )

    agent_process = AgentProcess(
        user_id=LLM_AGENT,
        password=account_agent.password,
        homeserver=account_agent.homeserver_url,
        experiment=EXPERIMENT,
        run_id=session_id,
        room_id="",
        telemetry=agent_path,
        state_dir=state_dir,
        executor="llm",
        request_protocol="natural_language",
        # The credential reaches the agent process through the environment and
        # is never written to a file, a command line or an artifact.
        extra_env={ENV_API_KEY: api_key},
    )

    loop = asyncio.get_running_loop()
    try:
        await human_role_b.start(defer_sync=True)

        room_id = await human_role_b.client.create_room(
            name=f"FAM E4 {session_id}", invite=[ACTUAL_HUMAN, LLM_AGENT]
        )
        result.room_id = room_id
        human_role_b.bind_room(room_id)
        human_role_b.client.tracked_rooms = {room_id}
        human_role_b.begin_sync()
        agent_process.room_id = room_id

        version, encrypted = await assert_frozen_room_configuration(
            human_role_b.client, room_id
        )
        result.room_version = version
        result.encryption_enabled = encrypted

        await agent_process.start()

        print()
        print("=" * 68)
        print(f"  E4 session {session_id}")
        print("=" * 68)
        print(f"  room id      {room_id}   (version {version})")
        print(f"  provider     {llm.provider}   model {llm.model}")
        print()
        print(f"  In your Matrix client, signed in as {ACTUAL_HUMAN}:")
        print("    1. accept the invitation to this room")
        print(f"    2. send at least {MINIMUM_REQUESTS} ordinary "
              "natural-language messages")
        print("    3. watch for a reply to each one")
        print("=" * 68)
        print()

        deadline = loop.time() + JOIN_TIMEOUT_SECONDS
        result.membership = await _await_membership(human_role_b, room_id, deadline)
        if set(result.membership) < EXPECTED_MEMBERSHIP:
            missing = sorted(EXPECTED_MEMBERSHIP - set(result.membership))
            raise InvalidRun(
                InvalidRunClass.EXTERNAL_DEPENDENCY_OR_CLIENT_ENVIRONMENT_FAILURE,
                f"{missing} did not join within "
                f"{JOIN_TIMEOUT_SECONDS:.0f}s; the human client never "
                "reached the room",
            )
        print(f"    membership established: {sorted(result.membership)}")

        deadline = loop.time() + INTERACTION_TIMEOUT_SECONDS
        result.exchanges, result.execution_failures = await _await_exchanges(
            human_role_b, room_id, agent_path, deadline
        )

    finally:
        await agent_process.stop()
        # Re-read after shutdown so the agent's final records are included.
        timeline: dict[str, dict] = {}
        try:
            timeline = await _timeline(human_role_b, result.room_id)
        except Exception:  # noqa: BLE001 - evidence collection is best effort
            pass
        if result.room_id:
            result.exchanges, result.execution_failures = (
                _correspondence_from_telemetry(read_jsonl(agent_path), timeline)
            )
            result.membership = result.membership or []
        await human_role_b.close()

    result.human_confirmed_visible = _confirm_visibility(args, result)
    result.screenshots = _collect_screenshots(args, root, result)
    result.transcript_path = _write_transcript(result, root, llm)
    _evaluate(result)
    _write_manifest(result, root, llm, args)
    return result


def _confirm_visibility(args: argparse.Namespace, result: SessionResult) -> bool:
    """The human is the authority on whether they saw the responses.

    No account other than the human's own can answer this, and this runner
    deliberately never signs in as the human. So it is asked, and the answer
    is recorded as evidence alongside the event ids.
    """
    if args.confirm_visible:
        return True
    answered = len(
        [e for e in result.completed_exchanges if e.request_sender == ACTUAL_HUMAN]
    )
    print()
    print(f"  {answered} response(s) were sent to your requests.")
    # Printed with an explicit flush rather than passed to input(): input()
    # writes its prompt without a newline, and on a block-buffered stream that
    # leaves the person staring at a session that looks hung while it waits
    # for an answer to a question they were never shown.
    print("  Did you see them in your Matrix client? [y/N]: ", end="", flush=True)
    try:
        reply = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        print("  (no answer given — set FAM_E4_CONFIRM_VISIBLE=true to record "
              "confirmation without a prompt)")
        return False
    return reply.lower() in ("y", "yes", "д", "да")


def _collect_screenshots(
    args: argparse.Namespace, root: Path, result: SessionResult
) -> list[Path]:
    """Optional. Screenshots support the transcript; they never replace it."""
    directory = evidence_dir(root) / result.session_id
    directory.mkdir(parents=True, exist_ok=True)
    found: list[Path] = []
    for raw in args.screenshot or []:
        source = Path(raw).expanduser()
        if not source.exists():
            print(f"  ! screenshot {source} not found; skipped")
            continue
        target = directory / source.name
        target.write_bytes(source.read_bytes())
        found.append(target)
    found.extend(
        path
        for path in sorted(directory.glob("*"))
        if path.suffix.lower() in (".png", ".jpg", ".jpeg") and path not in found
    )
    return found


def _write_manifest(
    result: SessionResult, root: Path, llm: Any, args: argparse.Namespace
) -> None:
    evidence: list[EvidenceArtifact] = []
    if result.transcript_path and result.transcript_path.exists():
        evidence.append(
            EvidenceArtifact("transcript", result.transcript_path, "machine-readable")
        )
    for shot in result.screenshots:
        evidence.append(
            EvidenceArtifact("screenshot", shot, "standard client, human view")
        )

    validity = result.validity
    if result.execution_failures and all(
        item.get("execution", {}).get("external_dependency_failure")
        for item in result.execution_failures
    ):
        validity = invalid(
            InvalidRunClass.EXTERNAL_DEPENDENCY_OR_CLIENT_ENVIRONMENT_FAILURE,
            "every executor failure in this session was a provider-side "
            "condition outside the tested integration",
        )

    manifest = HumanValidationManifest(
        session_id=result.session_id,
        room_id=result.room_id,
        participants={
            "actual_human": ACTUAL_HUMAN,
            "human_role_b": HUMAN_ROLE_B,
            "llm_agent": LLM_AGENT,
        },
        publication_data=publication_data(),
        protocol_git_commit=protocol_git_commit(),
        human_client_name=args.client_name,
        human_client_version=args.client_version,
        human_client_host=args.client_host,
        llm_provider=llm.provider,
        llm_model=llm.model,
        agent_config_hash=llm.config_hash(),
        executor_identifier="llm",
        interaction_event_ids=[e.to_dict() for e in result.exchanges],
        evidence=evidence,
        environment_manifest="environment/environment-latest.json",
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        completion_status="pass" if result.passed else "fail",
        validity=validity,
        three_party_topology_confirmed=set(result.membership) == EXPECTED_MEMBERSHIP,
        functional_result="pass" if result.passed else "fail",
        results_root=root,
        extra={
            # The configured identifier may be an alias. Recording what the
            # provider said it actually used is what keeps the session
            # reproducible: an alias can point somewhere else tomorrow.
            "llm_model_configured": llm.model,
            "llm_models_reported_by_provider": sorted(
                {e.llm_model for e in result.completed_exchanges if e.llm_model}
            ),
            "request_protocol": "natural_language",
            "communication_runtime": "identical to E0-E3; only the executor "
            "and the request protocol differ",
            "minimum_requests": MINIMUM_REQUESTS,
            "answered_requests_from_human": len(
                [
                    e
                    for e in result.completed_exchanges
                    if e.request_sender == ACTUAL_HUMAN
                ]
            ),
            "human_confirmed_responses_visible": result.human_confirmed_visible,
            "execution_failures": result.execution_failures,
            "acceptance_failures": result.reasons,
            "agent_telemetry": (
                {
                    "path": result.agent_stream.relative_to(root).as_posix(),
                    "sha256": file_sha256(result.agent_stream),
                }
                if result.agent_stream and result.agent_stream.exists()
                else None
            ),
            "scope_note": (
                "Development validation. publication_data is false; C4 is not "
                "marked collected and no evidence counter is updated."
            ),
        },
    )
    result.manifest_path = manifest.write(manifests_dir(root))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="e4-session")
    parser.add_argument("--session-id", default=os.environ.get("FAM_E4_SESSION_ID", ""))
    parser.add_argument(
        "--client-name", default=os.environ.get("FAM_E4_CLIENT_NAME", "unrecorded")
    )
    parser.add_argument(
        "--client-version",
        default=os.environ.get("FAM_E4_CLIENT_VERSION", "unrecorded"),
    )
    parser.add_argument(
        "--client-host", default=os.environ.get("FAM_E4_CLIENT_HOST", "unrecorded")
    )
    parser.add_argument(
        "--screenshot",
        action="append",
        default=[],
        help="Path to a screenshot to preserve as supporting evidence.",
    )
    parser.add_argument(
        "--confirm-visible",
        action="store_true",
        default=os.environ.get("FAM_E4_CONFIRM_VISIBLE", "").lower()
        in ("1", "true", "yes"),
        help="Record that the human saw the responses without prompting. "
             "Only for a session where the human has already confirmed.",
    )
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    print("E4 — Human + LLM Functional Validation (one development session)")
    print(f"publication_data: {publication_data()}")
    if not publication_data():
        print("development session — not publication evidence\n")

    try:
        result = await run_session(args)
    except InvalidRun as exc:
        print(f"\nINVALID SESSION: {exc}")
        print("classified under the §35 taxonomy; a new session id may be used")
        return 2

    print()
    print(f"  session      {result.session_id}")
    print(f"  room         {result.room_id} (v{result.room_version})")
    print(f"  membership   {sorted(result.membership)}")
    print(f"  exchanges    {len(result.completed_exchanges)} answered")
    for exchange in result.completed_exchanges:
        print(
            f"    {exchange.request_event_id} -> {exchange.response_event_id} "
            f"({exchange.llm_model or 'model unrecorded'})"
        )
    for failure in result.execution_failures:
        print(f"    ! executor failure on {failure['request_event_id']}: "
              f"{failure['note'][:120]}")
    print(f"  transcript   {result.transcript_path}")
    if result.screenshots:
        print(f"  screenshots  {[p.name for p in result.screenshots]}")
    print(f"  manifest     {result.manifest_path}")
    print(f"  {'PASS' if result.passed else 'FAIL'}")
    for reason in result.reasons:
        print(f"    ! {reason}")
    return 0 if result.passed else 1


def main() -> int:
    try:
        return asyncio.run(main_async())
    except ValueError as exc:
        # A missing provider configuration is a setup problem, reported plainly
        # rather than as an experiment failure.
        print(f"configuration error: {exc}")
        print(f"set {ENV_MODEL} and {ENV_API_KEY} before running `make e4`")
        return 2
    except InvalidRun as exc:
        print(f"INVALID SESSION: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
