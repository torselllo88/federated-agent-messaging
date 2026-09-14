"""External results directory: guard and layout.

Every run-generated artifact is written outside the tracked working tree for
the whole formal campaign (testbed-architecture.md §22,
experimental-protocol.md §37). Nothing is written into the repository while
data collection is in progress, which is what keeps the clean-worktree and
protocol-lock preconditions satisfiable across a run series.
"""

from __future__ import annotations

import os
from pathlib import Path

from fam.common.validity import InvalidRun, InvalidRunClass

#: Frozen layout, experimental-protocol.md §37.
RAW_SUBDIRS = (
    "raw/e0",
    "raw/e1",
    "raw/e2",
    "raw/e3/latency",
    "raw/e3/throughput",
    "raw/e4",
    "manifests",
    "environment",
    "evidence",
)


def repository_root() -> Path | None:
    """Best-effort location of the tracked worktree.

    Inside the toolbox container only read-only source is mounted, so this
    resolves to the mount point rather than a git checkout. Both are treated
    the same way: results must not live underneath either.

    Two callers, for opposite reasons. This module uses it to refuse a results
    directory that lies inside the repository. Entry points use it to find a
    tracked input they must read -- the JSON Schemas. It resolves from this
    file's own location rather than from the working directory, so a script
    invoked by absolute path from somewhere else still finds them.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "src").is_dir() and (candidate / "scripts").is_dir():
            return candidate
    return None


def schema_dir() -> Path:
    """The tracked JSON Schema directory.

    One definition, because three entry points each carried their own. Two
    named the container path and nothing else, so off-container they resolved
    to a directory that does not exist and validated nothing; the third fell
    back to a path relative to the working directory, which held only while it
    was run from the repository root.
    """
    root = repository_root()
    if root is None:
        raise RuntimeError(
            "cannot locate the repository root from "
            f"{Path(__file__).resolve()}, so the tracked JSON Schemas under "
            "results/schemas cannot be found"
        )
    return root / "results" / "schemas"


def resolve_results_dir(create: bool = True) -> Path:
    """Return the validated external results directory.

    Raises :class:`InvalidRun` with ``execution_precondition_violation`` when
    the directory is undefined, inside the tracked repository, or unwritable
    (experimental-protocol.md §35).
    """
    raw = os.environ.get("FAM_RESULTS_DIR", "").strip()
    if not raw:
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            "FAM_RESULTS_DIR is not set",
        )

    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError as exc:  # pragma: no cover - platform dependent
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"FAM_RESULTS_DIR cannot be resolved: {exc}",
        ) from exc

    repo = repository_root()
    if repo is not None and _is_within(path, repo):
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"FAM_RESULTS_DIR ({path}) resolves inside the tracked repository ({repo})",
        )

    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InvalidRun(
                InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
                f"FAM_RESULTS_DIR cannot be created: {exc}",
            ) from exc

    if not path.is_dir():
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"FAM_RESULTS_DIR ({path}) is not a directory",
        )
    if not os.access(path, os.W_OK):
        raise InvalidRun(
            InvalidRunClass.EXECUTION_PRECONDITION_VIOLATION,
            f"FAM_RESULTS_DIR ({path}) is not writable",
        )
    return path


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def ensure_layout(root: Path) -> Path:
    """Create the frozen subdirectories that this slice needs."""
    for sub in RAW_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def raw_dir(root: Path, experiment: str) -> Path:
    """Raw stream directory for an experiment id such as ``e0``."""
    return root / "raw" / experiment.lower()


def manifests_dir(root: Path) -> Path:
    return root / "manifests"


def environment_dir(root: Path) -> Path:
    return root / "environment"


def evidence_dir(root: Path) -> Path:
    """Reserved for evidence artifacts such as E4 transcripts."""
    return root / "evidence"
