"""E4: executor substitution, provider boundary, and session acceptance.

The provider is mocked here. The actual E4 development sessions run against a
real configured provider with a real person — that is the whole point of the
experiment, and nothing in this file substitutes for it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fam.agent import protocol as request_protocol
from fam.common.message import Correlation, build_ack, build_request, parse
from fam.executors.base import ExecutionRequest, Executor, decide
from fam.executors.deterministic import DeterministicExecutor
from fam.executors.llm import (
    ANTHROPIC,
    OPENAI_COMPATIBLE,
    LLMConfig,
    LLMExecutor,
    LLMProviderError,
    redact,
)

AGENT = "@llm-agent:hs-b.test"
HUMAN = "@actual-human:hs-a.test"
SECRET = "sk-ant-supersecret-0123456789abcdef"


def run(coro):
    return asyncio.run(coro)


def _config(provider: str = ANTHROPIC) -> LLMConfig:
    return LLMConfig(
        provider=provider,
        model="test-model-1",
        base_url="https://provider.invalid",
    )


def _transport(status: int, body: str):
    async def transport(url, headers, payload):
        transport.seen = {"url": url, "headers": headers, "payload": payload}
        return status, body

    transport.seen = {}
    return transport


ANTHROPIC_OK = json.dumps(
    {
        "model": "test-model-1",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 7},
        "content": [{"type": "text", "text": "Paris."}],
    }
)

OPENAI_OK = json.dumps(
    {
        "model": "test-model-1",
        "choices": [
            {"finish_reason": "stop", "message": {"content": "Paris."}}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }
)


# ------------------------------------------------- interface compatibility


def test_both_executors_satisfy_one_interface():
    deterministic = DeterministicExecutor()
    llm = LLMExecutor(config=_config(), api_key=SECRET, transport=_transport(200, ANTHROPIC_OK))
    assert isinstance(deterministic, Executor)
    assert isinstance(llm, Executor)
    assert deterministic.name == "deterministic"
    assert llm.name == "llm"


def test_the_runtime_helper_accepts_sync_and_async_executors():
    """The substitution E4 demonstrates: the caller does not branch."""
    correlation = Correlation("E0", "run-1", 5)
    controlled = ExecutionRequest(
        text=build_request(correlation), message=parse(build_request(correlation))
    )
    deterministic = run(decide(DeterministicExecutor(), controlled))
    assert deterministic == build_ack(correlation)

    llm = LLMExecutor(
        config=_config(), api_key=SECRET, transport=_transport(200, ANTHROPIC_OK)
    )
    answer = run(decide(llm, ExecutionRequest(text="capital of France?")))
    assert answer == "Paris."


# --------------------------------------------------------- provider paths


@pytest.mark.parametrize(
    "provider,body", [(ANTHROPIC, ANTHROPIC_OK), (OPENAI_COMPATIBLE, OPENAI_OK)]
)
def test_provider_success_path(provider, body):
    transport = _transport(200, body)
    executor = LLMExecutor(
        config=_config(provider), api_key=SECRET, transport=transport
    )
    assert run(executor.decide(ExecutionRequest(text="capital of France?"))) == "Paris."
    assert executor.last_call["model"] == "test-model-1"
    assert executor.last_call["input_tokens"] == 11
    assert executor.last_call["output_tokens"] == 7


def test_only_the_system_prompt_and_the_request_reach_the_provider():
    """§11: no conversational memory, no retrieval, no history."""
    transport = _transport(200, ANTHROPIC_OK)
    executor = LLMExecutor(config=_config(), api_key=SECRET, transport=transport)
    run(executor.decide(ExecutionRequest(text="hello there")))
    payload = transport.seen["payload"]
    assert payload["messages"] == [{"role": "user", "content": "hello there"}]
    assert payload["system"] == _config().system_prompt
    assert executor.config.conversation_history_turns == 0


def test_an_oversized_request_is_bounded():
    transport = _transport(200, ANTHROPIC_OK)
    executor = LLMExecutor(config=_config(), api_key=SECRET, transport=transport)
    run(executor.decide(ExecutionRequest(text="x" * 99_000)))
    assert len(transport.seen["payload"]["messages"][0]["content"]) <= 4000


# ------------------------------------------------- failure classification


@pytest.mark.parametrize("status", [500, 502, 503, 429, 401, 403])
def test_provider_side_conditions_classify_as_external(status):
    """§35: the provider did not do the work, so it is outside the integration."""
    executor = LLMExecutor(
        config=_config(), api_key=SECRET, transport=_transport(status, "upstream")
    )
    with pytest.raises(LLMProviderError) as caught:
        run(executor.decide(ExecutionRequest(text="hi")))
    assert caught.value.external is True
    assert caught.value.status == status


@pytest.mark.parametrize("status", [400, 404, 422])
def test_integration_faults_are_experimental_outcomes(status):
    """Provider available, request rejected: the integration failed."""
    executor = LLMExecutor(
        config=_config(), api_key=SECRET, transport=_transport(status, "bad request")
    )
    with pytest.raises(LLMProviderError) as caught:
        run(executor.decide(ExecutionRequest(text="hi")))
    assert caught.value.external is False


def test_unreachable_provider_is_external():
    async def transport(url, headers, payload):
        raise ConnectionError("name resolution failed")

    executor = LLMExecutor(config=_config(), api_key=SECRET, transport=transport)
    with pytest.raises(LLMProviderError) as caught:
        run(executor.decide(ExecutionRequest(text="hi")))
    assert caught.value.external is True


def test_an_empty_completion_is_an_integration_failure_not_a_silent_pass():
    body = json.dumps({"content": [{"type": "text", "text": "   "}]})
    executor = LLMExecutor(
        config=_config(), api_key=SECRET, transport=_transport(200, body)
    )
    with pytest.raises(LLMProviderError) as caught:
        run(executor.decide(ExecutionRequest(text="hi")))
    assert caught.value.external is False


def test_the_executor_never_retries_on_its_own():
    calls = {"n": 0}

    async def transport(url, headers, payload):
        calls["n"] += 1
        return 503, "unavailable"

    executor = LLMExecutor(config=_config(), api_key=SECRET, transport=transport)
    with pytest.raises(LLMProviderError):
        run(executor.decide(ExecutionRequest(text="hi")))
    assert calls["n"] == 1, "retrying until one call succeeds is what §51 forbids"


# ------------------------------------------------------ secret redaction


def test_the_api_key_never_appears_in_the_config_hash_or_public_config():
    config = _config()
    assert SECRET not in json.dumps(config.public())
    other = LLMExecutor(config=config, api_key="a-different-key")
    same = LLMExecutor(config=config, api_key=SECRET)
    assert other.config.config_hash() == same.config.config_hash()


def test_the_api_key_never_appears_in_repr_or_errors():
    executor = LLMExecutor(
        config=_config(),
        api_key=SECRET,
        transport=_transport(500, f"upstream rejected key {SECRET}"),
    )
    assert SECRET not in repr(executor)
    with pytest.raises(LLMProviderError) as caught:
        run(executor.decide(ExecutionRequest(text="hi")))
    assert SECRET not in str(caught.value)
    assert "<redacted>" in str(caught.value)


def test_redaction_handles_an_echoed_credential():
    assert redact(f"header was {SECRET}", (SECRET,)) == "header was <redacted>"
    assert redact("nothing here", (SECRET,)) == "nothing here"
    assert redact("empty secret", ("",)) == "empty secret"


def test_last_call_records_counts_not_payloads():
    """§27: the Matrix transcript already holds the request and the response."""
    executor = LLMExecutor(
        config=_config(), api_key=SECRET, transport=_transport(200, ANTHROPIC_OK)
    )
    run(executor.decide(ExecutionRequest(text="a private question")))
    blob = json.dumps(executor.last_call)
    assert "a private question" not in blob
    assert "Paris." not in blob
    assert executor.last_call["response_characters"] == len("Paris.")


# -------------------------------------------------------- request protocol


def test_natural_language_protocol_accepts_ordinary_prose():
    protocol = request_protocol.build("natural_language", agent_mxid=AGENT)
    inbound = protocol.inbound(
        event_id="$abc", sender=HUMAN, body="What is the capital of France?"
    )
    assert inbound is not None
    assert inbound.execution.text == "What is the capital of France?"
    assert inbound.execution.message is None
    assert inbound.response_txn_id.startswith("fam-e4-response-")


def test_natural_language_protocol_ignores_the_agent_and_empty_messages():
    protocol = request_protocol.build("natural_language", agent_mxid=AGENT)
    assert protocol.inbound(event_id="$a", sender=AGENT, body="my own reply") is None
    assert protocol.inbound(event_id="$b", sender=HUMAN, body="   ") is None


def test_identical_text_sent_twice_is_two_requests():
    """Identity is the event, not the content.

    Deduplicating on text would silently drop a human's repeated question,
    which is not what exactly-once processing means.
    """
    protocol = request_protocol.build("natural_language", agent_mxid=AGENT)
    first = protocol.inbound(event_id="$one", sender=HUMAN, body="hello")
    second = protocol.inbound(event_id="$two", sender=HUMAN, body="hello")
    assert first.correlation_key != second.correlation_key
    assert first.response_txn_id != second.response_txn_id


def test_response_transaction_id_is_deterministic():
    first = request_protocol.response_txn_for_event("$abc")
    assert first == request_protocol.response_txn_for_event("$abc")
    assert first != request_protocol.response_txn_for_event("$def")


def test_controlled_protocol_is_unchanged_by_e4():
    protocol = request_protocol.build("controlled", agent_mxid=AGENT)
    correlation = Correlation("E0", "run-1", 5)
    inbound = protocol.inbound(
        event_id="$x", sender=HUMAN, body=build_request(correlation)
    )
    assert inbound is not None
    assert inbound.correlation_key == correlation.key()
    assert inbound.response_txn_id == correlation.txn_id("response")
    assert inbound.sequence_id == 5
    # Prose must not be answered under the controlled protocol.
    assert protocol.inbound(event_id="$y", sender=HUMAN, body="hello there") is None


def test_unknown_protocol_is_rejected():
    with pytest.raises(ValueError):
        request_protocol.build("telepathy", agent_mxid=AGENT)
