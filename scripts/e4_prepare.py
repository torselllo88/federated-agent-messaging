#!/usr/bin/env python3
"""E4 preparation: confirm the testbed can host a human-driven session.

Checks what a standard Matrix client on an external workstation needs, and
prints the connection details the human requires. Creates no room, no session
and no evidence.

testbed-architecture.md §7: for E4 the Client-Server endpoint used by the
standard client must be reachable over HTTPS with the private CA trusted in
that client, and the human's workstation must resolve the server name to the
testbed host. A standard client will otherwise simply refuse to connect, and
the session cannot begin.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from pathlib import Path

sys.path.insert(0, "/app/src")

from dataclasses import replace  # noqa: E402

from fam.common.env import (  # noqa: E402
    CS_URLS,
    DOMAIN_NAMES,
    load_accounts,
    publication_data,
)
from fam.common.results import (  # noqa: E402
    ensure_layout,
    environment_dir,
    resolve_results_dir,
)
from fam.executors.llm import (  # noqa: E402
    ENV_API_KEY,
    ENV_MODEL,
    ENV_PROVIDER,
    api_key_from_environment,
    config_from_environment,
)

ACTUAL_HUMAN = "@actual-human:hs-a.test"
HUMAN_ROLE_B = "@human-role-b:hs-b.test"
LLM_AGENT = "@llm-agent:hs-b.test"

#: The E4-only HTTPS Client-Server listener (testbed-architecture.md §7).
CS_TLS_PORT = int(os.environ.get("FAM_E4_CS_TLS_PORT", "8449"))
CA_PATH = Path("/tls/ca.crt")


class Check:
    def __init__(self) -> None:
        self.results: list[dict] = []

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.results)


def check_accounts(check: Check) -> None:
    print("\nProvisioned E4 identities")
    try:
        accounts = load_accounts()
    except FileNotFoundError as exc:
        check.record("accounts file present", False, str(exc))
        return
    for user_id in (ACTUAL_HUMAN, HUMAN_ROLE_B, LLM_AGENT):
        check.record(f"{user_id} provisioned", user_id in accounts)


def check_https_endpoint(check: Check) -> dict:
    """Confirm the HTTPS Client-Server listener answers as the right server.

    Verified the way a standard client verifies it: full CA verification with
    hostname checking, against the server name the human will type. A
    handshake completing under those rules *is* the identity evidence — an
    unverified handshake plus a separate certificate inspection proves less,
    and ``getpeercert()`` returns nothing at all under CERT_NONE.
    """
    print("\nHTTPS Client-Server endpoint (E4 only)")
    server_name = DOMAIN_NAMES["A"]
    if not CA_PATH.exists():
        check.record("research CA present", False, str(CA_PATH))
        return {"reachable": False}

    subject_alt: list[str] = []
    try:
        context = ssl.create_default_context(cafile=str(CA_PATH))
        with socket.create_connection((server_name, CS_TLS_PORT), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=server_name) as tls:
                certificate = tls.getpeercert() or {}
                subject_alt = [
                    value
                    for kind, value in certificate.get("subjectAltName", ())
                    if kind == "DNS"
                ]
        check.record(
            f"TLS handshake on {CS_TLS_PORT} verified against the research CA",
            True,
            f"server name {server_name} accepted; SAN {subject_alt}",
        )
    except (OSError, ssl.SSLError) as exc:
        check.record(
            f"TLS handshake on {CS_TLS_PORT} verified against the research CA",
            False,
            f"{type(exc).__name__}: {exc}",
        )
        return {"reachable": False, "subject_alt_names": []}

    served, status = _probe(f"https://{server_name}:{CS_TLS_PORT}/_matrix/client/versions")
    check.record(
        "Client-Server API served over HTTPS and trusted by the research CA",
        served,
        f"/_matrix/client/versions -> {status}",
    )

    federation_ok, federation_status = _probe(
        f"https://{server_name}:8448/_matrix/key/v2/server"
    )
    check.record(
        "Server-Server federation endpoint still answers on 8448",
        federation_ok,
        f"the E4 client listener is separate and must not disturb it "
        f"(/_matrix/key/v2/server -> {federation_status})",
    )

    advertised = _check_well_known(check, server_name)

    return {
        "reachable": True,
        "subject_alt_names": subject_alt,
        "client_api_served": served,
        "federation_endpoint_ok": federation_ok,
        "advertised_base_url": advertised,
    }


def _check_well_known(check: Check, server_name: str) -> str:
    """Verify the address the server tells clients to use.

    Synapse derives this from ``public_baseurl`` and returns it both at
    ``/.well-known/matrix/client`` and as ``well_known`` in the login
    response. A standard client switches to it immediately after signing in,
    so an address that omits the Client-Server port leaves the client stuck
    on a port nothing listens on — after a *successful* login, which is what
    makes it so easy to misread as a hang.
    """
    import json as _json

    document = _fetch(f"https://{server_name}:{CS_TLS_PORT}/.well-known/matrix/client")
    if document is None:
        check.record(
            "server advertises a client base URL", False, "well-known unreachable"
        )
        return ""
    try:
        advertised = (
            _json.loads(document).get("m.homeserver", {}).get("base_url", "")
        ).rstrip("/")
    except ValueError:
        check.record("server advertises a client base URL", False, "not JSON")
        return ""

    expected = f"https://{server_name}:{CS_TLS_PORT}"
    check.record(
        "advertised client base URL names the Client-Server port",
        advertised == expected,
        f"{advertised or '<empty>'} (expected {expected})",
    )

    # And it must actually work when followed, not merely look right.
    followed, status = _probe(f"{advertised}/_matrix/client/versions")
    check.record(
        "the advertised base URL serves the Client-Server API",
        followed,
        f"a standard client switches to this address after login "
        f"(/_matrix/client/versions -> {status})",
    )
    return advertised


def _fetch(url: str) -> str | None:
    """GET a URL and return its body, or None. Never raises."""
    import asyncio

    async def get() -> str:
        import aiohttp

        ssl_context = ssl.create_default_context(cafile=str(CA_PATH))
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url) as response:
                return await response.text()

    try:
        return asyncio.run(get())
    except Exception:  # noqa: BLE001
        return None


def _probe(url: str) -> tuple[bool, object]:
    """GET a URL with the research CA trusted. Never raises."""
    import asyncio

    async def get() -> int:
        import aiohttp

        ssl_context = ssl.create_default_context(cafile=str(CA_PATH))
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url) as response:
                return response.status

    try:
        status = asyncio.run(get())
        return status == 200, status
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def check_llm(check: Check) -> dict:
    print("\nLLM provider configuration")
    try:
        config = config_from_environment()
    except ValueError as exc:
        check.record("provider configured", False, str(exc))
        return {}
    check.record("provider configured", True, f"{config.provider} / {config.model}")
    check.record(
        f"{ENV_API_KEY} present",
        bool(os.environ.get(ENV_API_KEY, "").strip()),
        "value not shown and never written to an artifact",
    )
    return config.public() | {"agent_configuration_hash": config.config_hash()}


def check_provider_call(check: Check, config) -> dict:
    """One real, minimal provider call.

    Confirms the credential, the model identifier and the network path in a
    single exchange, and exercises the same failure classification the agent
    will use during a session (experimental-protocol.md §35).
    """
    print("\nProvider preflight")
    if os.environ.get("FAM_E4_SKIP_PROVIDER_CHECK", "").lower() in ("1", "true", "yes"):
        check.record("provider preflight", True, "skipped by request")
        return {"skipped": True}

    import asyncio

    from fam.executors.base import ExecutionRequest
    from fam.executors.llm import LLMExecutor, LLMProviderError

    # A tiny budget: this proves reachability, not capability.
    probe_config = replace(config, max_tokens=16)
    executor = LLMExecutor(config=probe_config, api_key=api_key_from_environment())
    try:
        answer = asyncio.run(
            executor.decide(ExecutionRequest(text="Reply with the single word: ready"))
        )
    except LLMProviderError as exc:
        kind = (
            "external dependency condition"
            if exc.external
            else "integration failure"
        )
        check.record(
            "provider answered a minimal request",
            False,
            f"{kind}: {exc}",
        )
        return {
            "ok": False,
            "external_dependency_failure": exc.external,
            "status": exc.status,
        }
    except Exception as exc:  # noqa: BLE001
        check.record(
            "provider answered a minimal request", False, f"{type(exc).__name__}: {exc}"
        )
        return {"ok": False}

    check.record(
        "provider answered a minimal request",
        bool(answer),
        f"{executor.last_call.get('model')} responded with "
        f"{executor.last_call.get('response_characters')} characters",
    )
    return {
        "ok": True,
        "model_reported_by_provider": executor.last_call.get("model"),
        "http_status": executor.last_call.get("http_status"),
        "input_tokens": executor.last_call.get("input_tokens"),
        "output_tokens": executor.last_call.get("output_tokens"),
    }


def _human_password() -> str:
    """The provisioned password for the human account.

    Printed to the console for the operator, never written to the report:
    this command runs in the privileged bootstrap container, which is the
    only place that holds credentials, and the report is an artifact.
    """
    try:
        return load_accounts()[ACTUAL_HUMAN].password
    except Exception:  # noqa: BLE001
        return "<run `make setup` to provision>"


def print_human_instructions(endpoint: dict) -> None:
    server_name = DOMAIN_NAMES["A"]
    print()
    print("=" * 70)
    print("  Connection details for the human participant")
    print("=" * 70)
    print(f"  Matrix user      {ACTUAL_HUMAN}")
    print(f"  Password         {_human_password()}")
    print(f"  Homeserver URL   https://{server_name}:{CS_TLS_PORT}")
    print()
    print("  On the workstation running the standard Matrix client:")
    print()
    print(f"    1. Resolve {server_name} to the machine running this testbed.")
    print("       On the testbed host itself, add to the hosts file:")
    print(f"           127.0.0.1  {server_name}")
    print("       Windows: C:\\Windows\\System32\\drivers\\etc\\hosts (as admin)")
    print("       Linux/macOS: /etc/hosts (as root)")
    print()
    print("    2. Trust the research CA in that client's trust store.")
    print("       Export it from the testbed with:")
    print("           make e4-ca > research-ca.crt")
    print("       then import it. This is a manual step by design: nothing")
    print("       here modifies a system trust store on your behalf.")
    print()
    print("    3. Sign in to the client with the homeserver URL above.")
    print()
    print("  Full instructions: docs/e4-human-client-setup.md")
    print()
    print("  To remove the research CA afterwards, delete the imported")
    print("  certificate from the same trust store, and remove the hosts entry.")
    print("=" * 70)


def main() -> int:
    root = ensure_layout(resolve_results_dir())
    check = Check()

    print("E4 preparation")
    print(f"results: {root}")
    print(f"publication_data: {publication_data()}")
    print("no room, session or evidence is created by this command")

    check_accounts(check)
    endpoint = check_https_endpoint(check)
    llm = check_llm(check)

    preflight: dict = {}
    if llm:
        preflight = check_provider_call(check, config_from_environment())

    report = {
        "artifact": "e4_preparation_report",
        "note": "Credentials are printed to the console only, never recorded here.",
        "publication_data": publication_data(),
        "cs_tls_port": CS_TLS_PORT,
        "cs_https_endpoint": f"https://{DOMAIN_NAMES['A']}:{CS_TLS_PORT}",
        "cs_http_internal": CS_URLS["A"],
        "endpoint": endpoint,
        "llm": llm,
        "provider_preflight": preflight,
        "checks": check.results,
        "ready": check.ok,
    }
    path = environment_dir(root) / "e4-preparation.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print_human_instructions(endpoint)
    print(f"\nreport: {path}")
    print(f"\nE4 PREPARE: {'READY' if check.ok else 'NOT READY'}")
    if not check.ok:
        print("resolve the failing checks before running `make e4`")
    return 0 if check.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
