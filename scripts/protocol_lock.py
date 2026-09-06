#!/usr/bin/env python3
"""Generate and validate the formal protocol lock (experimental-protocol.md §46).

    protocol_lock.py generate    build the lock from the live environment
    protocol_lock.py validate    check a committed lock against this environment

`generate` writes into ``$FAM_RESULTS_DIR/environment/``, never into the Git
worktree: no container may write into the tree (Task 07 §66). The operator
copies the artifact to ``results/protocol-lock.json``, commits it, and tags the
commit — which is what binds the lock to an exact implementation.

The lock is generated once, after the final configuration freeze and before the
first formal run. Regenerating it once data exists produces a different
artifact and is a protocol event, not a routine step.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app/src")
sys.path.insert(0, "src")

from fam.benchmark.schedule import (  # noqa: E402
    campaign_parameters,
    fingerprint,
    generate_workload_schedule,
)
from fam.common.digests import bytes_sha256, file_sha256  # noqa: E402
from fam.common.frozen import (  # noqa: E402
    E3_CONCURRENCY_LEVELS,
    E3_PAIRED_BLOCKS,
    E3_SCHEDULE_SEED,
    E3_SYNC_TIMELINE_LIMIT,
    E3_SYNC_TIMEOUT_MS,
    E3_WORKLOAD_LATENCY,
    E3_WORKLOAD_THROUGHPUT,
    SUPPORTED_RAW_SCHEMA_VERSIONS,
)
from fam.common.lock import (  # noqa: E402
    LOCK_ARTIFACT,
    LOCK_SCHEMA_VERSION,
    ProtocolLockError,
    compare,
    frozen_parameters,
    load,
    lock_path,
)
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)
from fam.common.validity import InteractionOutcome, InvalidRunClass  # noqa: E402

SCHEMA_DIR = Path("/app/results/schemas")
if not SCHEMA_DIR.exists():
    SCHEMA_DIR = Path("results/schemas")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=15, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _environment(root: Path) -> dict[str, Any]:
    path = environment_dir(root) / "environment-latest.json"
    if not path.exists():
        raise ProtocolLockError(
            f"no environment manifest at {path}. Run `make hashes` on the "
            f"formal host after the final configuration freeze (Task 07 §14)."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _rate_limits(root: Path) -> dict[str, Any]:
    """§46 requires the rate-limit configuration in the lock.

    The environment manifest never carried it: rate limits are confirmed by the
    environment verifier, which writes them to its own report. Reading them from
    the manifest left the locked field empty while the data sat one file away.
    """
    path = environment_dir(root) / "verify-report.json"
    if not path.exists():
        raise ProtocolLockError(
            f"no verification report at {path}. Run `make verify` on the formal "
            f"host before generating the lock; §46 requires the rate-limit "
            f"configuration and this is where it is confirmed."
        )
    limits = json.loads(path.read_text(encoding="utf-8")).get("rate_limits") or {}
    if not limits:
        raise ProtocolLockError(
            f"{path} records no rate limits, so the lock cannot carry the "
            f"configuration §46 requires"
        )
    return limits


def _llm_configuration() -> dict[str, Any]:
    """The E4 executor configuration, as one comparable value plus its parts.

    The hash covers provider, model, base URL, max tokens, system prompt and
    history depth and excludes the API key, so it identifies what reaches the
    provider without publishing a credential. The readable parts sit beside it
    so the lock is legible without running code.
    """
    from fam.executors.llm import config_from_environment

    config = config_from_environment()
    public = config.public()
    return {
        "agent_config_hash": config.config_hash(),
        "base_url": public["base_url"],
        "max_tokens": public["max_tokens"],
        "conversation_history_turns": public["conversation_history_turns"],
        "system_prompt_sha256": bytes_sha256(
            public["system_prompt"].encode("utf-8")
        ),
        "system_prompt": public["system_prompt"],
    }


def _inventory(root: Path) -> dict[str, Any]:
    path = environment_dir(root) / "testbed-inventory.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _plan(runs) -> list[dict[str, Any]]:
    return [
        {
            "block_id": run.block_id,
            "block_index": run.block_index,
            "within_block_order": run.within_block_order,
            "topology": run.topology,
            "concurrency": run.concurrency,
            "key": run.key,
        }
        for run in runs
    ]


def _schedule() -> dict[str, Any]:
    """The complete paired run order, fixed before any formal data exists.

    Persisted in full rather than as a seed alone: §16 requires the schedule
    itself, so a later reader can check the executed order against the lock
    without re-running the generator that produced it.
    """
    plans: dict[str, Any] = {
        "latency": _plan(
            generate_workload_schedule(
                workload=E3_WORKLOAD_LATENCY, concurrency=1, seed=E3_SCHEDULE_SEED
            )
        )
    }
    for level in E3_CONCURRENCY_LEVELS:
        plans[f"throughput_c{level}"] = _plan(
            generate_workload_schedule(
                workload=E3_WORKLOAD_THROUGHPUT,
                concurrency=level,
                seed=E3_SCHEDULE_SEED,
            )
        )
    return plans


def _validate_against_schema(document: dict[str, Any]) -> list[str]:
    """Structural check against the lock's own schema.

    Run at generation as well as validation: a lock that does not satisfy its
    schema must never reach the worktree, because everything downstream trusts
    it without re-deriving it.
    """
    schema_path = SCHEMA_DIR / "protocol-lock.schema.json"
    if not schema_path.exists():
        return [f"{schema_path} is missing"]
    try:
        import jsonschema
    except ImportError as error:  # pragma: no cover - container always has it
        # Never a silent skip. A lock that was not actually checked against its
        # schema, but reports as checked, is worse than no check at all.
        raise ProtocolLockError(
            "jsonschema is unavailable, so the lock cannot be validated. Run "
            "this inside the toolbox container (`make lock`), not on a bare "
            f"host interpreter: {error}"
        ) from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(document), key=lambda e: list(e.path))
    ]


def _schema_digests() -> dict[str, str]:
    return {
        path.name: file_sha256(path)
        for path in sorted(SCHEMA_DIR.glob("*.schema.json"))
    }


#: Set by the Makefile from the host's own `git status --porcelain`. Empty
#: means clean; unset means nobody looked.
WORKTREE_STATUS_ENV = "FAM_WORKTREE_STATUS"


def _worktree_status() -> str:
    """The porcelain status of the implementation worktree.

    Inside the toolbox container only `src/`, `scripts/` and `results/` are
    mounted, there is no `.git`, and `git status` therefore returns nothing —
    which is indistinguishable from a clean tree. Reporting `worktree_clean:
    true` on that basis would put an unverified claim into the lock, so the
    status has to come from somewhere that can actually see the repository.
    """
    if Path(".git").exists() or Path("/app/.git").exists():
        return _git("status", "--porcelain")
    if WORKTREE_STATUS_ENV in os.environ:
        return os.environ[WORKTREE_STATUS_ENV].strip()
    raise ProtocolLockError(
        "cannot determine whether the worktree is clean: no .git here and "
        f"{WORKTREE_STATUS_ENV} is unset. Use `make lock`, which reads the "
        "status on the host and passes it in; a lock must not claim a "
        "cleanliness it never checked (Task 07 §21)."
    )


# ------------------------------------------------------------------ generate


def _supersedes(args: argparse.Namespace) -> dict[str, Any] | None:
    """The earlier lock this one replaces, if any.

    A published tag is never moved. An earlier lock that produced no evidence
    still happened, and saying so in the artifact is cheaper than leaving a
    reader to work out why two tags describe the same protocol version.
    """
    if not args.supersedes_tag:
        return None
    # Resolved on the host for the usual reason: the container has no .git, and
    # "unresolved" in a provenance field defeats the field.
    commit = _git("rev-list", "-n", "1", args.supersedes_tag) or args.supersedes_commit
    if not commit:
        raise ProtocolLockError(
            f"cannot resolve the commit for superseded tag "
            f"{args.supersedes_tag!r}. Pass --supersedes-commit, which "
            f"`make lock` fills in from the host."
        )
    return {
        "tag": args.supersedes_tag,
        "commit": commit,
        "reason": args.supersedes_reason,
        "formal_artifacts_produced": 0,
        "note": (
            "Superseded before any formal collection. No manifest, raw stream "
            "or evidence artifact carries publication_data true under that tag, "
            "and it is retained rather than moved because a published tag that "
            "changes what it points at is worse for provenance than a tag that "
            "is recorded as replaced."
        ),
    }


def generate(args: argparse.Namespace) -> int:
    root = ensure_layout(resolve_results_dir())
    environment = _environment(root)
    inventory = _inventory(root)
    rate_limits = _rate_limits(root)
    llm = _llm_configuration()

    commit = _git("rev-parse", "HEAD") or os.environ.get("FAM_PROTOCOL_GIT_COMMIT", "")
    if not commit:
        raise ProtocolLockError(
            "cannot determine the implementation commit. Run this on the "
            "formal host inside the checked-out repository, or set "
            "FAM_PROTOCOL_GIT_COMMIT."
        )
    dirty = _worktree_status()
    if dirty:
        raise ProtocolLockError(
            "the worktree is not clean; a lock must identify an exact "
            f"committed implementation (Task 07 §21):\n{dirty}"
        )

    config_hashes = environment.get("config_hashes", {})
    if not config_hashes:
        raise ProtocolLockError(
            "the environment manifest carries no configuration hashes"
        )

    parameters = campaign_parameters(
        seed=E3_SCHEDULE_SEED,
        sync_timeline_limit=E3_SYNC_TIMELINE_LIMIT,
        sync_timeout_ms=E3_SYNC_TIMEOUT_MS,
        blocks=E3_PAIRED_BLOCKS,
        concurrency_levels=tuple(E3_CONCURRENCY_LEVELS),
        protocol_git_commit=commit,
        config_hashes=config_hashes,
        rate_limits=rate_limits,
    )
    campaign_fingerprint = fingerprint(parameters)
    campaign = args.campaign_id or f"fam-formal-{campaign_fingerprint[:16]}"

    lock: dict[str, Any] = {
        "artifact": LOCK_ARTIFACT,
        "lock_schema_version": LOCK_SCHEMA_VERSION,
        "generated_at": _utc(),
        "purpose": (
            "Binds the formal campaign to one implementation, one environment "
            "and one set of frozen parameters. Once this artifact is committed "
            "and tagged, no execution-affecting change may occur during "
            "collection (experimental-protocol.md §46, Task 07 §1)."
        ),
        "implementation": {
            "git_commit": commit,
            "git_tag": args.tag,
            "worktree_clean": dirty == "",
            "raw_schema_versions_supported": list(SUPPORTED_RAW_SCHEMA_VERSIONS),
            "schema_digests": _schema_digests(),
        },
        "frozen_parameters": frozen_parameters(),
        "environment": {
            "formal_run_host_identifier": environment.get(
                "formal_run_host_identifier", platform.node()
            ),
            "host": environment.get("host", {}),
            "software": environment.get("software", {}),
            "config_hashes": config_hashes,
            "rate_limits": rate_limits,
            "environment_manifest_generated_at": environment.get("generated_at"),
            "image_digests": inventory.get("image_digests", {}),
        },
        "campaign": {
            "campaign_id": campaign,
            "campaign_fingerprint": campaign_fingerprint,
            "campaign_parameters": parameters,
            "result_root": os.environ.get("FAM_RESULTS_DIR", "unset"),
            "experiment_order": ["E0", "E1", "E2", "E3", "E4"],
        },
        "e3_schedule": _schedule(),
        "e4": {
            "llm_provider": os.environ.get("FAM_LLM_PROVIDER", "unset"),
            "llm_model": os.environ.get("FAM_LLM_MODEL", "unset"),
            # The whole executor configuration as one comparable value. E4
            # refuses to open a session whose effective configuration differs,
            # so §36's "same frozen configuration" is checked against the lock
            # and not merely across the three sessions.
            **llm,
            "sessions": 3,
            "human_requests_per_session": 3,
            # Operational ceilings on waiting for a person, recorded so the
            # artifact is self-contained. Not compared: they bound how long the
            # runner waits, not what the tested system does.
            "session_interaction_timeout_seconds": float(
                os.environ.get("FAM_E4_TIMEOUT", "1800")
            ),
            "session_join_timeout_seconds": float(
                os.environ.get("FAM_E4_JOIN_TIMEOUT", "900")
            ),
            "human_client_name": os.environ.get("FAM_E4_CLIENT_NAME", "unset"),
            "human_client_version": os.environ.get("FAM_E4_CLIENT_VERSION", "unset"),
            "human_client_host": os.environ.get("FAM_E4_CLIENT_HOST", "unset"),
            "note": (
                "The actual human's workstation is external to the formal host "
                "by design (experimental-protocol.md §39, Task 07 §38)."
            ),
        },
        "validity_taxonomy": {
            "terminal_outcomes": [o.value for o in InteractionOutcome],
            "post_terminal_integrity_observations": ["duplicate_ack"],
            "invalid_run_classes": [c.value for c in InvalidRunClass],
            "note": (
                "Closed at lock. Adding a class afterwards increments "
                "protocol_version and is disclosed (§35, §46)."
            ),
        },
        "supersedes": _supersedes(args),
        "accepted_limitations": [
            {
                "id": "L10",
                "summary": (
                    "Sync delivers events in batches and the runner dispatches "
                    "their callbacks sequentially, so the k-th ACK in a batch "
                    "has T3 stamped after the preceding callbacks have run. The "
                    "bias can only delay T3, never advance it."
                ),
                "correction_applied": False,
                "development_magnitude": (
                    "~2.9 us/event, ~92 us at batch 32, ~0.077% of a 120 ms RTT, "
                    "measured under development conditions and not generalised "
                    "to the formal host"
                ),
                "reference": "experimental-protocol.md §10",
            },
            {
                "id": "sequential-agent",
                "summary": (
                    "E3 measures the observed end-to-end throughput of the "
                    "tested closed-loop system using the frozen sequential "
                    "deterministic agent. It is not maximum Matrix throughput, "
                    "maximum federation throughput, or messaging-layer capacity."
                ),
                "correction_applied": False,
                "reference": "experimental-protocol.md §17",
            },
        ],
    }

    problems = compare(lock)
    if problems:
        raise ProtocolLockError(
            "the generated lock does not match the running code: "
            + "; ".join(problems)
        )
    schema_problems = _validate_against_schema(lock)
    if schema_problems:
        raise ProtocolLockError(
            "the generated lock does not satisfy its own schema: "
            + "; ".join(schema_problems)
        )

    path = environment_dir(root) / "protocol-lock.json"
    path.write_text(
        json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    digest = file_sha256(path)
    total = sum(len(v) for v in lock["e3_schedule"].values())

    frozen = lock["frozen_parameters"]
    print(f"protocol lock written: {path}")
    print(f"  sha256                {digest}")
    print(f"  protocol_version      {frozen['protocol_version']}")
    print(f"  analysis_spec_version {frozen['analysis_spec_version']}")
    print(f"  raw_schema_version    {frozen['raw_schema_version']}")
    print(f"  git commit            {commit}")
    print(f"  git tag               {args.tag}")
    print(f"  host                  {lock['environment']['formal_run_host_identifier']}")
    for key, value in sorted(config_hashes.items()):
        print(f"  synapse {key} config     {value[:16]}...")
    print(f"  campaign id           {campaign}")
    print(f"  campaign fingerprint  {campaign_fingerprint[:16]}...")
    print(f"  E3 scheduled runs     {total}")
    print(f"  E4 model              {lock['e4']['llm_model']}")
    print()
    print("Not yet in effect. Copy it into the worktree, commit, and tag:")
    print(f"  cp {path} results/protocol-lock.json")
    print("  git add results/protocol-lock.json")
    print("  git commit -m ...")
    print(f"  git tag {args.tag}")
    return 0


# ------------------------------------------------------------------ validate

#: The one path the tagging commit is allowed to add on top of the
#: implementation it locks.
LOCK_ARTIFACT_PATH = "results/protocol-lock.json"


def _implementation_drift(locked_commit: str, head: str) -> list[str]:
    """Paths that differ between the locked implementation and HEAD.

    Empty means the two are identical, which cannot happen here because HEAD is
    the commit that added the lock. Exactly the lock artifact means the tag
    reproduces the locked implementation. Anything else is drift: code changed
    between the lock being generated and the lock being committed.
    """
    diff = _git("diff", "--name-only", locked_commit, head)
    if not diff:
        raw = os.environ.get("FAM_IMPLEMENTATION_DIFF")
        if raw is None:
            return [
                f"HEAD {head[:12]} != locked commit {locked_commit[:12]} and "
                f"FAM_IMPLEMENTATION_DIFF is unset, so the difference between "
                f"them was never examined. Use `make lock-check`."
            ]
        diff = raw
    changed = sorted(p for p in diff.split("\n") if p.strip())
    unexpected = [p for p in changed if p != LOCK_ARTIFACT_PATH]
    if unexpected:
        return [
            f"HEAD {head[:12]} changes more than the lock artifact relative to "
            f"the locked implementation {locked_commit[:12]}: {unexpected}"
        ]
    if LOCK_ARTIFACT_PATH not in changed:
        return [
            f"HEAD {head[:12]} differs from the locked implementation "
            f"{locked_commit[:12]} but not by the lock artifact"
        ]
    return []


def validate(args: argparse.Namespace) -> int:
    target = Path(args.path) if args.path else None
    document = load(target)
    if document is None:
        print(f"FAIL: no protocol lock at {target or lock_path()}", file=sys.stderr)
        return 1

    failures: list[str] = []
    implementation = document.get("implementation", {})

    failures.extend(f"schema: {p}" for p in _validate_against_schema(document))
    failures.extend(f"frozen parameter: {p}" for p in compare(document))

    commit = _git("rev-parse", "HEAD") or os.environ.get("FAM_PROTOCOL_GIT_COMMIT", "")
    locked_commit = implementation.get("git_commit")
    if not commit:
        failures.append(
            "cannot determine HEAD, so the lock cannot be tied to a commit"
        )
    elif locked_commit and commit != locked_commit:
        # A lock cannot name the commit that carries it: the artifact must exist
        # before it can be committed. So it names the implementation commit, and
        # HEAD is the commit that adds the lock on top. That is only acceptable
        # while the lock file is the *entire* difference between them -- one
        # other changed path and the tag no longer reproduces the implementation
        # the lock describes.
        failures.extend(_implementation_drift(locked_commit, commit))

    try:
        if _worktree_status():
            failures.append("worktree is not clean")
    except ProtocolLockError as error:
        failures.append(str(error))

    tag = implementation.get("git_tag")
    tag_applied = False
    if tag:
        # Same reasoning as the worktree status: the container cannot run git,
        # so an empty result there means "nobody looked", not "no tags".
        raw = _git("tag", "--points-at", "HEAD") or os.environ.get(
            "FAM_GIT_TAGS_AT_HEAD", ""
        )
        pointed = raw.split()
        tag_applied = tag in pointed
        if pointed and not tag_applied:
            failures.append(f"tag {tag} does not point at HEAD (found {pointed})")
        elif not pointed and args.require_tag:
            # Validation before the tagging commit is legitimate; validation
            # before a formal run is not, so the caller says which it is.
            failures.append(f"tag {tag} has not been applied to HEAD")

    for name, digest in implementation.get("schema_digests", {}).items():
        path = SCHEMA_DIR / name
        if not path.exists():
            failures.append(f"schema {name} is missing")
        elif file_sha256(path) != digest:
            failures.append(f"schema {name} digest differs from the lock")

    try:
        environment = _environment(ensure_layout(resolve_results_dir()))
    except (ProtocolLockError, OSError, ValueError):
        environment = {}
    if environment:
        locked_hashes = document.get("environment", {}).get("config_hashes", {})
        live = environment.get("config_hashes", {})
        for key in sorted(set(locked_hashes) | set(live)):
            if locked_hashes.get(key) != live.get(key):
                failures.append(
                    f"synapse {key} config hash differs: locked "
                    f"{str(locked_hashes.get(key))[:16]}, live "
                    f"{str(live.get(key))[:16]}"
                )

    schedule = document.get("e3_schedule", {})
    total = sum(len(v) for v in schedule.values())
    expected = 2 * E3_PAIRED_BLOCKS * (1 + len(E3_CONCURRENCY_LEVELS))
    if total != expected:
        failures.append(f"schedule holds {total} runs, expected {expected}")

    print(f"protocol lock: {target or lock_path()}")
    print(f"  commit    {locked_commit}")
    print(f"  tag       {tag}")
    print(f"  campaign  {document.get('campaign', {}).get('campaign_id')}")
    print(f"  schedule  {total} runs")
    print(f"  tagged    {'yes' if tag_applied else 'not yet'}")
    if failures:
        print("\nFAIL")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS: lock matches the implementation and the environment")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="build the lock from the live environment")
    gen.add_argument("--tag", default="protocol-v1.2")
    gen.add_argument("--campaign-id", default="")
    gen.add_argument("--supersedes-tag", default="")
    gen.add_argument("--supersedes-reason", default="")
    gen.add_argument("--supersedes-commit", default="")
    gen.set_defaults(func=generate)

    val = sub.add_parser("validate", help="check a lock against this environment")
    val.add_argument("--path", default="")
    val.add_argument(
        "--require-tag",
        action="store_true",
        help="fail unless the lock's tag points at HEAD; use before a formal run",
    )
    val.set_defaults(func=validate)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ProtocolLockError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
