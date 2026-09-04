"""Runtime environment: domains, endpoints and provisioned credentials.

The experiment runner and the agent read credentials from a bootstrap-produced
file mounted read-only. Neither reads Synapse configuration: they hold no
administrator credential and no server filesystem access, and the verifier
supplies frozen configuration values as manifest data instead
(testbed-architecture.md §15, experimental-protocol.md §5, §28).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SECRETS_DIR = Path(os.environ.get("FAM_SECRETS_DIR", "/secrets"))
ACCOUNTS_FILE = SECRETS_DIR / "accounts.json"

#: The complete frozen account set. Provisioning is written once, so the
#: identities for E1-E4 are created now even though only E0 uses them
#: (testbed-architecture.md §10, §24; experimental-protocol.md §6, §41).
FROZEN_ACCOUNTS: dict[str, str] = {
    "@human-a:hs-a.test": "A",
    "@human-b:hs-b.test": "B",
    "@agent-local:hs-a.test": "A",
    "@agent:hs-b.test": "B",
    "@benchmark-human:hs-a.test": "A",
    "@benchmark-agent-local:hs-a.test": "A",
    "@benchmark-agent-fed:hs-b.test": "B",
    "@actual-human:hs-a.test": "A",
    "@human-role-b:hs-b.test": "B",
    "@llm-agent:hs-b.test": "B",
}

DOMAIN_NAMES = {
    "A": os.environ.get("FAM_HS_A_NAME", "hs-a.test"),
    "B": os.environ.get("FAM_HS_B_NAME", "hs-b.test"),
}

CS_URLS = {
    "A": os.environ.get("FAM_HS_A_CS_URL", "http://synapse-a:8008"),
    "B": os.environ.get("FAM_HS_B_CS_URL", "http://synapse-b:8008"),
}

#: Internal container names, used only for transport-level reachability
#: checks. Federation itself always addresses the server name.
CONTAINER_NAMES = {"A": "synapse-a", "B": "synapse-b"}


@dataclass(frozen=True)
class Account:
    user_id: str
    password: str
    domain_key: str

    @property
    def homeserver_url(self) -> str:
        return CS_URLS[self.domain_key]

    @property
    def server_name(self) -> str:
        return DOMAIN_NAMES[self.domain_key]


def localpart(user_id: str) -> str:
    return user_id.lstrip("@").split(":", 1)[0]


def load_accounts(path: Path = ACCOUNTS_FILE) -> dict[str, Account]:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `make setup` before running experiments."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    accounts: dict[str, Account] = {}
    for user_id, record in payload.get("accounts", {}).items():
        accounts[user_id] = Account(
            user_id=user_id,
            password=record["password"],
            domain_key=record["domain"],
        )
    return accounts


def account(user_id: str) -> Account:
    accounts = load_accounts()
    if user_id not in accounts:
        raise KeyError(f"{user_id} was not provisioned; re-run `make setup`")
    return accounts[user_id]


def publication_data() -> bool:
    """Task 01 runs are development runs and never publication evidence."""
    return os.environ.get("FAM_PUBLICATION_DATA", "false").strip().lower() == "true"


def protocol_git_commit() -> str:
    return os.environ.get("FAM_PROTOCOL_GIT_COMMIT", "unknown")


def agent_state_dir() -> Path:
    path = Path(os.environ.get("FAM_AGENT_STATE_DIR", "/tmp/fam-agent-state"))
    path.mkdir(parents=True, exist_ok=True)
    return path
