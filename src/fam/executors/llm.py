"""LLM-backed executor. E4 only.

testbed-architecture.md §14.2: the LLM executor replaces the decision function
and nothing else. The Matrix adapter, identity, room membership,
synchronization, federation and response sending are the same code E0-E3 ran.

Deliberately not an agent framework. No tools, no retrieval, no memory, no
routing, no fallback chain — one provider, one model, one call per request
(Task 06 §9, §34). E4 validates that the architecture can host an LLM-backed
execution layer; it evaluates nothing about the model
(experimental-protocol.md §41).

**No conversational memory.** Each request is answered on its own. The agent
holds a transport checkpoint, never conversation state
(testbed-architecture.md §9.2, §9.3), and passing room history here would
quietly turn E4 into a memory experiment. What is sent to the provider is
exactly: the system instruction below, and the text of the one request.

Secrets come from the environment and never leave it. The API key is not in
the config hash, not in telemetry, not in manifests, and not in any error this
module raises.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fam.common.digests import bytes_sha256
from fam.executors.base import ExecutionRequest

ANTHROPIC = "anthropic"
OPENAI_COMPATIBLE = "openai_compatible"

#: Minimal instruction: enough to produce an ordinary useful reply, and
#: nothing that could be read as prompt engineering for a quality result.
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant participating in a Matrix chat room. "
    "Answer the user's message directly and concisely in plain text."
)

DEFAULT_MAX_TOKENS = 512
#: Providers reject bodies far larger than any reasonable chat turn, and an
#: unbounded request would let one pasted message become an unbounded call.
MAX_REQUEST_CHARS = 4000

#: Environment keys. Values are read at construction; the key is read but
#: never stored anywhere that gets serialized.
ENV_PROVIDER = "FAM_LLM_PROVIDER"
ENV_MODEL = "FAM_LLM_MODEL"
ENV_API_KEY = "FAM_LLM_API_KEY"
ENV_BASE_URL = "FAM_LLM_BASE_URL"
ENV_MAX_TOKENS = "FAM_LLM_MAX_TOKENS"
ENV_SYSTEM_PROMPT = "FAM_LLM_SYSTEM_PROMPT"

_SECRET_MARKER = "<redacted>"


class LLMProviderError(RuntimeError):
    """The provider call failed.

    ``external`` distinguishes the two cases the frozen taxonomy separates
    (experimental-protocol.md §35, *Where the boundary falls*):

    * the provider was unavailable *before or instead of* execution — an
      external dependency failure, which may invalidate the session;
    * the provider answered and the integration failed — an experimental
      outcome, which stays in the record.

    The runtime records the classification; it never decides to retry on it.
    """

    def __init__(self, message: str, *, external: bool, status: int | None = None) -> None:
        super().__init__(message)
        self.external = external
        self.status = status


@dataclass(frozen=True)
class LLMConfig:
    """Provider configuration. Contains no secret."""

    provider: str
    model: str
    base_url: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    #: Recorded so a reader can confirm what reached the provider.
    conversation_history_turns: int = 0

    def public(self) -> dict[str, Any]:
        """Everything about this configuration that may be published."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "system_prompt": self.system_prompt,
            "conversation_history_turns": self.conversation_history_turns,
        }

    def config_hash(self) -> str:
        """Hash of the published configuration. The API key is not an input.

        Two deployments differing only in credential hash identically, which
        is the intended behaviour for a configuration fingerprint.
        """
        payload = json.dumps(self.public(), sort_keys=True, separators=(",", ":"))
        return bytes_sha256(payload.encode("utf-8"))


DEFAULT_BASE_URLS = {
    ANTHROPIC: "https://api.anthropic.com",
    OPENAI_COMPATIBLE: "https://api.openai.com",
}


def config_from_environment() -> LLMConfig:
    provider = os.environ.get(ENV_PROVIDER, ANTHROPIC).strip().lower()
    if provider not in DEFAULT_BASE_URLS:
        raise ValueError(
            f"{ENV_PROVIDER}={provider!r} is not supported; expected one of "
            f"{sorted(DEFAULT_BASE_URLS)}"
        )
    model = os.environ.get(ENV_MODEL, "").strip()
    if not model:
        raise ValueError(f"{ENV_MODEL} is not set; E4 requires an explicit model")
    base_url = (
        os.environ.get(ENV_BASE_URL, "").strip() or DEFAULT_BASE_URLS[provider]
    ).rstrip("/")
    max_tokens = int(os.environ.get(ENV_MAX_TOKENS, "").strip() or DEFAULT_MAX_TOKENS)
    system_prompt = (
        os.environ.get(ENV_SYSTEM_PROMPT, "").strip() or DEFAULT_SYSTEM_PROMPT
    )
    return LLMConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        max_tokens=max_tokens,
        system_prompt=system_prompt,
    )


def api_key_from_environment() -> str:
    key = os.environ.get(ENV_API_KEY, "").strip()
    if not key:
        raise ValueError(
            f"{ENV_API_KEY} is not set. E4 requires a real provider "
            "credential; it is read from the environment and never written to "
            "any artifact."
        )
    return key


def redact(text: str, secrets: tuple[str, ...]) -> str:
    """Remove any known secret from a string bound for an artifact.

    Belt and braces: nothing in this module deliberately writes the key, and
    this makes an accidental echo — a provider quoting the Authorization
    header back in an error body, say — non-fatal.
    """
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, _SECRET_MARKER)
    return out


Transport = Callable[[str, dict, dict], Awaitable[tuple[int, str]]]


async def _aiohttp_transport(url: str, headers: dict, payload: dict) -> tuple[int, str]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            return response.status, await response.text()


@dataclass
class LLMExecutor:
    """One provider call per request. Nothing else.

    ``transport`` exists so tests can exercise the success path, the failure
    classification and the redaction without a network or a credential.
    """

    config: LLMConfig
    api_key: str = field(repr=False, default="")
    transport: Transport | None = None
    name: str = "llm"

    def __post_init__(self) -> None:
        self._transport: Transport = self.transport or _aiohttp_transport
        #: Last call's provider-side facts, for telemetry. Never the payload.
        self.last_call: dict[str, Any] = {}

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return (
            f"LLMExecutor(provider={self.config.provider!r}, "
            f"model={self.config.model!r}, api_key={_SECRET_MARKER})"
        )

    # ------------------------------------------------------------- request

    def _endpoint(self) -> str:
        if self.config.provider == ANTHROPIC:
            return f"{self.config.base_url}/v1/messages"
        return f"{self.config.base_url}/v1/chat/completions"

    def _headers(self) -> dict[str, str]:
        if self.config.provider == ANTHROPIC:
            return {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

    def _payload(self, text: str) -> dict[str, Any]:
        if self.config.provider == ANTHROPIC:
            return {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "system": self.config.system_prompt,
                "messages": [{"role": "user", "content": text}],
            }
        return {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": text},
            ],
        }

    @staticmethod
    def _extract(provider: str, document: dict) -> str:
        if provider == ANTHROPIC:
            blocks = document.get("content") or []
            parts = [
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            return "".join(parts).strip()
        choices = document.get("choices") or []
        if not choices:
            return ""
        return ((choices[0].get("message") or {}).get("content") or "").strip()

    # -------------------------------------------------------------- decide

    async def decide(self, request: ExecutionRequest) -> str | None:
        text = request.text.strip()
        if not text:
            return None
        if len(text) > MAX_REQUEST_CHARS:
            text = text[:MAX_REQUEST_CHARS]

        secrets = (self.api_key,)
        try:
            status, body = await self._transport(
                self._endpoint(), self._headers(), self._payload(text)
            )
        except Exception as exc:  # noqa: BLE001
            # Could not reach the provider at all: the failure is outside the
            # integration being validated.
            raise LLMProviderError(
                f"provider unreachable: {redact(f'{type(exc).__name__}: {exc}', secrets)}",
                external=True,
            ) from None

        if status >= 500 or status in (401, 403, 429):
            # Outage, credential or quota: the provider did not perform the
            # work, so this is an external dependency condition.
            raise LLMProviderError(
                f"provider returned {status}: {redact(body, secrets)[:500]}",
                external=True,
                status=status,
            )
        if status >= 400:
            # The provider was available and rejected what the integration
            # sent. That is the integration failing, which is an experimental
            # outcome, not an external dependency failure.
            raise LLMProviderError(
                f"provider rejected the request with {status}: "
                f"{redact(body, secrets)[:500]}",
                external=False,
                status=status,
            )

        try:
            document = json.loads(body)
        except ValueError:
            raise LLMProviderError(
                "provider returned a body that is not JSON", external=False,
                status=status,
            ) from None

        answer = self._extract(self.config.provider, document)
        usage = document.get("usage") or {}
        self.last_call = {
            "provider": self.config.provider,
            "model": document.get("model") or self.config.model,
            "http_status": status,
            "stop_reason": document.get("stop_reason")
            or (document.get("choices") or [{}])[0].get("finish_reason"),
            "response_characters": len(answer),
            # Counts only. The prompt and completion themselves are already in
            # the Matrix transcript, which is the primary evidence (§27).
            "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
            "output_tokens": usage.get("output_tokens")
            or usage.get("completion_tokens"),
        }
        if not answer:
            raise LLMProviderError(
                "provider returned an empty response", external=False, status=status
            )
        return answer


def executor_from_environment(transport: Transport | None = None) -> LLMExecutor:
    return LLMExecutor(
        config=config_from_environment(),
        api_key=api_key_from_environment(),
        transport=transport,
    )
