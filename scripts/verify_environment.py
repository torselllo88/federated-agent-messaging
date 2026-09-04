#!/usr/bin/env python3
"""Environment verification.

Runs in the privileged verifier container, not the experiment runner: the
runner holds no Synapse configuration access, and that restriction is part of
the C2 evidence (experimental-protocol.md §5, §28, testbed-architecture.md §33).

Scope is bounded by experimental-protocol.md §4.1 and testbed-architecture.md
§16. Verification establishes federation *transport and bootstrap* readiness
only:

    reachable homeservers | name resolution | TCP federation path
    TLS federation handshake | server identity and signing-key discovery

It MUST NOT touch federated room join, cross-domain membership propagation,
persistent event propagation or federated history visibility. Those are what
E1 evaluates for C5; prevalidating them would let a genuine C5 failure surface
as an environment problem instead of a finding.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, "/app/src")

import yaml  # noqa: E402

from fam.common.digests import config_hash  # noqa: E402
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)
from fam.common.validity import InvalidRun  # noqa: E402

CA_PATH = Path("/tls/ca.crt")
FEDERATION_PORT = 8448

DOMAINS = {
    "A": {"server_name": "hs-a.test", "cs_url": "http://synapse-a:8008", "config": Path("/synapse/a/homeserver.yaml")},
    "B": {"server_name": "hs-b.test", "cs_url": "http://synapse-b:8008", "config": Path("/synapse/b/homeserver.yaml")},
}

#: Maximum offered load any frozen experiment places on one homeserver.
#: E3 Workload B at C=32 is the binding case (experimental-protocol.md §22).
PLANNED_PEAK_MESSAGES_PER_SECOND = 200


class Check:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.results)


def check_reachability(check: Check) -> None:
    print("\nHomeserver reachability")
    for key, spec in DOMAINS.items():
        try:
            with urllib.request.urlopen(f"{spec['cs_url']}/health", timeout=10) as r:
                check.record(f"domain {key} /health", r.status == 200, f"HTTP {r.status}")
        except Exception as exc:  # noqa: BLE001
            check.record(f"domain {key} /health", False, str(exc))


def check_name_resolution(check: Check) -> None:
    print("\nName resolution")
    for key, spec in DOMAINS.items():
        name = spec["server_name"]
        try:
            addr = socket.gethostbyname(name)
            check.record(f"{name} resolves", True, addr)
        except OSError as exc:
            check.record(f"{name} resolves", False, str(exc))


def check_tcp_path(check: Check) -> None:
    print("\nFederation TCP path")
    for key, spec in DOMAINS.items():
        name = spec["server_name"]
        try:
            with socket.create_connection((name, FEDERATION_PORT), timeout=10):
                check.record(f"{name}:{FEDERATION_PORT} accepts TCP", True)
        except OSError as exc:
            check.record(f"{name}:{FEDERATION_PORT} accepts TCP", False, str(exc))


def _tls_context() -> ssl.SSLContext:
    if not CA_PATH.exists():
        raise FileNotFoundError(
            f"{CA_PATH} missing. Trust in the private research CA is a "
            "functional precondition, not an option "
            "(testbed-architecture.md §7)."
        )
    return ssl.create_default_context(cafile=str(CA_PATH))


def check_tls_and_identity(check: Check) -> None:
    print("\nFederation TLS and server identity")
    try:
        context = _tls_context()
    except FileNotFoundError as exc:
        check.record("private CA available", False, str(exc))
        return
    check.record("private CA available", True, str(CA_PATH))

    for key, spec in DOMAINS.items():
        name = spec["server_name"]
        try:
            with socket.create_connection((name, FEDERATION_PORT), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=name) as tls:
                    peer = tls.getpeercert()
            subject = dict(x[0] for x in peer.get("subject", ()))
            check.record(
                f"{name} TLS handshake verified against private CA",
                True,
                f"CN={subject.get('commonName', '?')}",
            )
        except Exception as exc:  # noqa: BLE001
            check.record(f"{name} TLS handshake verified against private CA", False, str(exc))
            continue

        # Signing-key discovery over the federation port. This is server
        # identity material, not room behaviour, and is inside §4.1's bound.
        url = f"https://{name}:{FEDERATION_PORT}/_matrix/key/v2/server"
        try:
            with urllib.request.urlopen(url, timeout=15, context=context) as response:
                payload = json.loads(response.read())
            verify_keys = payload.get("verify_keys", {})
            ok = payload.get("server_name") == name and bool(verify_keys)
            check.record(
                f"{name} signing-key discovery",
                ok,
                f"server_name={payload.get('server_name')} keys={list(verify_keys)}",
            )
        except Exception as exc:  # noqa: BLE001
            check.record(f"{name} signing-key discovery", False, str(exc))


def _load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def check_config_hashes(check: Check, env_dir: Path) -> dict[str, str]:
    print("\nFrozen configuration hashes")
    baseline_path = env_dir / "frozen-config.json"
    baseline = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else {}
    )
    current: dict[str, str] = {}
    for key, spec in DOMAINS.items():
        path: Path = spec["config"]
        if not path.exists():
            check.record(f"domain {key} configuration present", False, str(path))
            continue
        digest = config_hash(_load_config(path))
        current[key] = digest
        known = baseline.get(key)
        if known is None:
            check.record(
                f"domain {key} config hash", True, f"{digest[:16]}… (baseline established)"
            )
        else:
            check.record(
                f"domain {key} config hash matches frozen baseline",
                known == digest,
                f"{digest[:16]}…",
            )
    if not baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def check_rate_limits(check: Check) -> dict:
    """Confirm client-side limits are non-binding for the planned envelope.

    The runner never performs this check: it holds no Synapse configuration
    access. The verifier does it here and publishes sanitized values that
    experiment manifests reference (experimental-protocol.md §28).
    """
    print("\nRate-limit envelope")
    summary: dict[str, dict] = {}
    for key, spec in DOMAINS.items():
        path: Path = spec["config"]
        if not path.exists():
            continue
        config = _load_config(path)
        limits = {name: value for name, value in config.items() if name.startswith("rc_")}
        summary[key] = limits

        message = limits.get("rc_message", {})
        per_second = message.get("per_second", 0)
        check.record(
            f"domain {key} rc_message non-binding",
            per_second >= PLANNED_PEAK_MESSAGES_PER_SECOND,
            f"per_second={per_second} burst={message.get('burst_count')} "
            f"planned peak={PLANNED_PEAK_MESSAGES_PER_SECOND}",
        )
        for required in ("rc_room_creation", "rc_joins", "rc_invites", "rc_login", "rc_federation"):
            check.record(f"domain {key} {required} documented", required in limits)

    if len(summary) == 2:
        check.record(
            "rate-limit configuration identical on both domains",
            summary.get("A") == summary.get("B"),
        )
    return summary


def main() -> int:
    print("Environment verification")
    print("Scope: transport and bootstrap readiness only (protocol §4.1).")
    print("Room-level federation behaviour is NOT tested here; that is E1/C5.")

    check = Check()

    try:
        results_root = ensure_layout(resolve_results_dir())
        check.record("FAM_RESULTS_DIR usable and outside the repository", True, str(results_root))
    except InvalidRun as exc:
        check.record("FAM_RESULTS_DIR usable and outside the repository", False, str(exc))
        print("\nVERIFY: FAIL")
        return 1

    env_dir = environment_dir(results_root)
    check_reachability(check)
    check_name_resolution(check)
    check_tcp_path(check)
    check_tls_and_identity(check)
    hashes = check_config_hashes(check, env_dir)
    limits = check_rate_limits(check)

    report = {
        "checks": [
            {"name": name, "ok": ok, "detail": detail} for name, ok, detail in check.results
        ],
        "config_hashes": hashes,
        "rate_limits": limits,
        "scope_note": (
            "transport and bootstrap readiness only; no room-level federation "
            "behaviour was exercised (experimental-protocol.md §4.1)"
        ),
    }
    (env_dir / "verify-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(f"\nVERIFY: {'PASS' if check.ok else 'FAIL'}")
    print(f"report: {env_dir / 'verify-report.json'}")
    return 0 if check.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
