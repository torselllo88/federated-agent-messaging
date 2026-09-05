"""Lightweight host and environment diagnostics captured before each run.

experimental-protocol.md §26 and §39, Task 05 §29: enough to identify an
obviously invalidating condition, and no more. This is deliberately not a
system profiler — a profiler would itself become unrelated workload on the
host being characterised.

Nothing here kills, throttles or tidies anything. If the development host has
background load, that fact is recorded and preserved; deciding what it means
is the analysis step, and controlling it is Task 07.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any


def _run(command: list[str], timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _load_average() -> list[float] | None:
    getter = getattr(os, "getloadavg", None)
    if getter is None:
        return None
    try:
        return [round(value, 3) for value in getter()]
    except OSError:
        return None


def _memory() -> dict[str, int]:
    """Container-visible memory, read from /proc where available."""
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                if name in ("MemTotal", "MemAvailable"):
                    parts = rest.split()
                    if parts and parts[0].isdigit():
                        out[name.lower() + "_bytes"] = int(parts[0]) * 1024
    except OSError:
        pass
    return out


def snapshot(*, note: str = "") -> dict[str, Any]:
    """One pre-run diagnostic record.

    Captured from inside the toolbox container, which is where the runner
    actually executes, so the CPU and memory figures describe the environment
    the benchmark really sees rather than the Windows or Linux host beneath it.
    """
    docker = shutil.which("docker")
    return {
        "host_identifier": os.environ.get("FAM_EXECUTION_HOST", "") or platform.node(),
        "runner_container": platform.node(),
        "os": platform.system(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "cpu_affinity": (
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else None
        ),
        "load_average_1_5_15": _load_average(),
        **_memory(),
        "docker_cli_present": bool(docker),
        "synapse_image": os.environ.get("SYNAPSE_IMAGE", "unset"),
        "postgres_image": os.environ.get("POSTGRES_IMAGE", "unset"),
        "note": note,
    }


def describe(diagnostics: dict[str, Any]) -> str:
    load = diagnostics.get("load_average_1_5_15")
    load_text = f" load={load}" if load else ""
    return (
        f"{diagnostics.get('os')}/{diagnostics.get('machine')} "
        f"cpus={diagnostics.get('logical_cpus')}{load_text}"
    )
