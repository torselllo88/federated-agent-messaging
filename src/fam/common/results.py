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


def _repository_root() -> Path | None:
    """Best-effort location of the tracked worktree.

    Inside the toolbox container only read-only source is mounted, so this
    resolves to the mount point rather than a git checkout. Both are treated
    the same way: results must not live underneath either.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "src").is_dir() and (candidate / "scripts").is_dir():
            return candidate
    return None


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

    repo = _repository_root()
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
