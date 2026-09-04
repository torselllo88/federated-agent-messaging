"""SHA-256 digests and canonical configuration hashing.

Every raw result stream carries a digest in its run manifest so the external
archive is verifiable file by file rather than only as one blob
(testbed-architecture.md §22, experimental-protocol.md §38).

Configuration hashes are the SHA-256 of the canonicalized, secret-stripped
configuration document (testbed-architecture.md §33).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024

#: Keys whose values are stripped before a configuration document is hashed
#: or published. Matching is case-insensitive on the key name.
SECRET_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "key",
    "credential",
    "salt",
    "pepper",
)

#: Keys that contain the substring "key" but are structural, not secret.
SECRET_KEY_EXCEPTIONS = frozenset(
    {
        "signing_key_path",
        "macaroon_secret_key_path",
        "trusted_key_servers",
        "key_server",
        "suppress_key_server_warning",
        "form_secret_path",
    }
)


def file_sha256(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_KEY_EXCEPTIONS:
        return False
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def sanitize(document: Any) -> Any:
    """Replace secret-bearing values with a stable redaction marker.

    The marker is constant so that redaction does not itself vary between
    runs: two deployments differing only in their passwords hash identically,
    which is the intended behaviour for a configuration fingerprint.
    """
    if isinstance(document, dict):
        out = {}
        for key, value in document.items():
            if isinstance(key, str) and _is_secret_key(key):
                out[key] = "<redacted>"
            else:
                out[key] = sanitize(value)
        return out
    if isinstance(document, list):
        return [sanitize(item) for item in document]
    return document


def canonical_json(document: Any) -> bytes:
    """Deterministic serialization: sorted keys, no insignificant whitespace."""
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def config_hash(document: Any) -> str:
    """SHA-256 of the canonicalized, secret-stripped configuration document."""
    return bytes_sha256(canonical_json(sanitize(document)))
