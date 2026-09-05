#!/usr/bin/env python3
"""Privileged environment setup, separated from experiment execution.

Bootstrap generates configuration, TLS material and signing keys, initializes
each homeserver and provisions research accounts. After it completes, no
experiment process holds a Synapse administrator credential, a database
credential, a signing key or server filesystem access — a separation that is
itself part of the C2 evidence (testbed-architecture.md §16).

Subcommands: tls | config | wait | provision
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, "/app/src")

from fam.common.env import DOMAIN_NAMES, FROZEN_ACCOUNTS, localpart  # noqa: E402

TLS_DIR = Path("/tls")
SECRETS_DIR = Path("/secrets")
#: Host port publishing the E4 HTTPS Client-Server listener. Must match
#: the published port in docker-compose.yml: clients are told this address
#: in the login response and will use it for every subsequent request.
CS_TLS_PORT = os.environ.get("FAM_E4_CS_TLS_PORT", "8449")

TEMPLATE = Path("/app/infrastructure/synapse/homeserver.yaml.template")
LOG_CONFIG = Path("/app/infrastructure/synapse/log.config")

DOMAINS = {
    "A": {
        "server_name": DOMAIN_NAMES["A"],
        "data": Path("/synapse/a"),
        "db_host": "postgres-a",
        "db_password_env": "POSTGRES_A_PASSWORD",
        "db_password_default": "fam-dev-password-a",
        "cs_url": "http://synapse-a:8008",
    },
    "B": {
        "server_name": DOMAIN_NAMES["B"],
        "data": Path("/synapse/b"),
        "db_host": "postgres-b",
        "db_password_env": "POSTGRES_B_PASSWORD",
        "db_password_default": "fam-dev-password-b",
        "cs_url": "http://synapse-b:8008",
    },
}

_ALPHABET = string.ascii_letters + string.digits

#: The Synapse image drops privileges to this uid/gid before reading files.
SYNAPSE_UID = int(os.environ.get("SYNAPSE_UID", "991"))
SYNAPSE_GID = int(os.environ.get("SYNAPSE_GID", "991"))


def token(length: int = 40) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def log(message: str) -> None:
    print(f"[bootstrap] {message}", flush=True)


# --------------------------------------------------------------------- TLS


def generate_tls() -> None:
    """Generate a private research CA and one certificate per domain.

    The CA exists solely to permit reproducible native federation inside the
    controlled environment. It is not part of the proposed architecture
    (testbed-architecture.md §7). Private keys live in a Docker volume and are
    never written into the repository.
    """
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    ca_key_path = TLS_DIR / "ca.key"
    ca_crt_path = TLS_DIR / "ca.crt"

    if ca_crt_path.exists() and ca_key_path.exists():
        log("CA already present, reusing")
        ca_key = serialization.load_pem_private_key(
            ca_key_path.read_bytes(), password=None
        )
        ca_cert = x509.load_pem_x509_certificate(ca_crt_path.read_bytes())
    else:
        log("generating private research CA")
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Federated Agent Messaging"),
                x509.NameAttribute(NameOID.COMMON_NAME, "FAM Research CA"),
            ]
        )
        now = datetime.now(timezone.utc)
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write_private(ca_key_path, ca_key)
        ca_crt_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    for spec in DOMAINS.values():
        name = spec["server_name"]
        crt_path = TLS_DIR / f"{name}.crt"
        key_path = TLS_DIR / f"{name}.key"
        if crt_path.exists() and key_path.exists():
            log(f"certificate for {name} already present")
            continue
        log(f"issuing certificate for {name}")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=825))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName(name), x509.DNSName(f"*.{name}")]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        _write_private(key_path, key)
        crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    # /tls is mounted read-only into the homeservers, which run as an
    # unprivileged user, so ownership has to be right at generation time.
    # Only the per-domain keys are handed over; the CA private key stays
    # root-owned because no homeserver needs to read it.
    for spec in DOMAINS.values():
        name = spec["server_name"]
        for path, mode in ((TLS_DIR / f"{name}.key", 0o640), (TLS_DIR / f"{name}.crt", 0o644)):
            os.chown(path, SYNAPSE_UID, SYNAPSE_GID)
            os.chmod(path, mode)
    os.chmod(TLS_DIR / "ca.crt", 0o644)

    log(f"TLS material ready in {TLS_DIR}")


def _write_private(path: Path, key) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(path, 0o600)


# ------------------------------------------------------------------ config


def _signing_key(path: Path, server_name: str) -> None:
    """Write a Synapse ed25519 signing key if one does not exist."""
    if path.exists():
        return
    key_id = "a_" + "".join(secrets.choice(string.ascii_letters) for _ in range(4))
    private = ed25519.Ed25519PrivateKey.generate()
    seed = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    encoded = base64.b64encode(seed).decode("ascii").rstrip("=")
    path.write_text(f"ed25519 {key_id} {encoded}\n", encoding="utf-8")
    os.chmod(path, 0o600)
    log(f"generated signing key for {server_name}")


def _chown_tree(root: Path) -> None:
    os.chown(root, SYNAPSE_UID, SYNAPSE_GID)
    for path in root.rglob("*"):
        os.chown(path, SYNAPSE_UID, SYNAPSE_GID)
        if path.name.endswith(".signing.key"):
            os.chmod(path, 0o640)


def render_config() -> None:
    template = Template(TEMPLATE.read_text(encoding="utf-8"))
    log_config_text = LOG_CONFIG.read_text(encoding="utf-8")
    secrets_payload: dict[str, dict] = {"domains": {}}

    for key, spec in DOMAINS.items():
        data: Path = spec["data"]
        data.mkdir(parents=True, exist_ok=True)
        (data / "media_store").mkdir(exist_ok=True)

        server_name = spec["server_name"]
        signing_key_path = data / f"{server_name}.signing.key"
        _signing_key(signing_key_path, server_name)

        existing = _load_domain_secrets(key)
        registration_secret = existing.get("registration_shared_secret") or token(48)
        macaroon = existing.get("macaroon_secret_key") or token(48)
        form_secret = existing.get("form_secret") or token(48)
        db_password = os.environ.get(
            spec["db_password_env"], spec["db_password_default"]
        )

        # The same volume is mounted at /synapse/{a,b} here and at /data
        # inside the homeserver, so the rendered path must be the one Synapse
        # will see, not the one bootstrap wrote to.
        rendered = template.substitute(
            SERVER_NAME=server_name,
            CS_TLS_PORT=CS_TLS_PORT,
            DB_HOST=spec["db_host"],
            DB_PASSWORD=db_password,
            SIGNING_KEY_PATH=f"/data/{server_name}.signing.key",
            REGISTRATION_SHARED_SECRET=registration_secret,
            MACAROON_SECRET=macaroon,
            FORM_SECRET=form_secret,
        )
        (data / "homeserver.yaml").write_text(rendered, encoding="utf-8")
        (data / "log.config").write_text(log_config_text, encoding="utf-8")

        # The Synapse image drops privileges to SYNAPSE_UID:SYNAPSE_GID before
        # reading its configuration, so bootstrap hands ownership of the data
        # directory over rather than leaving root-owned files it cannot read.
        _chown_tree(data)
        log(f"rendered configuration for {server_name}")

        secrets_payload["domains"][key] = {
            "server_name": server_name,
            "registration_shared_secret": registration_secret,
            "macaroon_secret_key": macaroon,
            "form_secret": form_secret,
        }

    _merge_secrets(secrets_payload)


def _load_domain_secrets(key: str) -> dict:
    path = SECRETS_DIR / "bootstrap.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("domains", {}).get(key, {})


def _merge_secrets(new: dict) -> None:
    """Bootstrap-only secrets. The toolbox mounts /secrets read-only and reads
    accounts.json; it never reads this file."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    path = SECRETS_DIR / "bootstrap.json"
    path.write_text(json.dumps(new, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


# -------------------------------------------------------------------- wait


def wait_healthy(timeout: float = 300.0) -> None:
    deadline = time.time() + timeout
    pending = {key: spec["cs_url"] for key, spec in DOMAINS.items()}
    while pending and time.time() < deadline:
        for key in list(pending):
            url = f"{pending[key]}/health"
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status == 200:
                        log(f"domain {key} healthy")
                        pending.pop(key)
            except (urllib.error.URLError, OSError, TimeoutError):
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit(
            f"homeservers not healthy within {timeout:.0f}s: {sorted(pending)}"
        )


# --------------------------------------------------------------- provision


def _register(cs_url: str, shared_secret: str, user: str, password: str) -> bool:
    """Shared-secret registration. Returns False if the user already exists."""
    endpoint = f"{cs_url}/_synapse/admin/v1/register"
    with urllib.request.urlopen(endpoint, timeout=15) as response:
        nonce = json.loads(response.read())["nonce"]

    # Fields are NUL-separated, with no trailing separator.
    mac = hmac.new(shared_secret.encode("utf-8"), digestmod=hashlib.sha1)
    mac.update(nonce.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(user.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(password.encode("utf-8"))
    mac.update(b"\x00")
    mac.update(b"notadmin")

    body = json.dumps(
        {
            "nonce": nonce,
            "username": user,
            "password": password,
            "admin": False,
            "mac": mac.hexdigest(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        if "User ID already taken" in detail or exc.code == 400 and "M_USER_IN_USE" in detail:
            return False
        raise SystemExit(f"registration failed for {user}: {exc.code} {detail}")


def provision() -> None:
    bootstrap_secrets = json.loads(
        (SECRETS_DIR / "bootstrap.json").read_text(encoding="utf-8")
    )
    accounts_path = SECRETS_DIR / "accounts.json"
    existing = (
        json.loads(accounts_path.read_text(encoding="utf-8"))
        if accounts_path.exists()
        else {"accounts": {}}
    )

    for user_id, domain_key in FROZEN_ACCOUNTS.items():
        spec = DOMAINS[domain_key]
        record = existing["accounts"].get(user_id)
        password = record["password"] if record else token(32)
        created = _register(
            spec["cs_url"],
            bootstrap_secrets["domains"][domain_key]["registration_shared_secret"],
            localpart(user_id),
            password,
        )
        existing["accounts"][user_id] = {
            "password": password,
            "domain": domain_key,
            "server_name": spec["server_name"],
        }
        log(f"{'registered' if created else 'already present'}: {user_id}")

    accounts_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    os.chmod(accounts_path, 0o644)
    log(f"{len(FROZEN_ACCOUNTS)} accounts provisioned")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    command = sys.argv[1]
    if command == "tls":
        generate_tls()
    elif command == "config":
        render_config()
    elif command == "wait":
        wait_healthy()
    elif command == "provision":
        provision()
    else:
        raise SystemExit(f"unknown subcommand {command!r}")


if __name__ == "__main__":
    main()
