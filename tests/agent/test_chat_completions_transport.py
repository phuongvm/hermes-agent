"""``ChatCompletionsTransport`` puts at most ONE of ``reasoning`` / ``reasoning_content`` on an
assistant wire message.

The two keys carry the same text. Gateways with a serde-style schema (9Router relays, Rust/axum
proxies) alias ``reasoning`` onto ``reasoning_content`` and reject a message holding both:
``Failed to deserialize the JSON body into the target type: duplicate field `reasoning_content```
(HTTP 400). The replay itself must survive: the kept carrier still holds the stored thinking text.
"""

import json
from copy import deepcopy

import pytest

from agent.message_sanitization import apply_reasoning_content_policy, reasoning_replay_route
from agent.transports.chat_completions import ChatCompletionsTransport

_TEXT_CARRIERS = ("reasoning", "reasoning_content")
_NINE_ROUTER = "http://192.168.100.110:20128/v1"


def _history(**reasoning):
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", **reasoning,
         "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]


def _wire_assistant(provider, base_url, history, model="m"):
    """The assistant message exactly as ``build_kwargs`` serializes it (route shaping included)."""
    route = reasoning_replay_route("chat_completions", provider, model, base_url)
    shaped = []
    for msg in history:
        api_msg = deepcopy(msg)
        apply_reasoning_content_policy(msg, api_msg, route.pad, route.carriers)
        shaped.append(api_msg)
    kwargs = ChatCompletionsTransport().build_kwargs(model, shaped, base_url=base_url, provider_name=provider)
    return json.loads(json.dumps(kwargs["messages"]))[1]


@pytest.mark.parametrize("provider,base_url", [
    ("custom", _NINE_ROUTER),                                # 9Router (the reported HTTP 400)
    ("custom", "http://127.0.0.1:8080/v1"),                  # llama.cpp / SGLang / vLLM
    ("openrouter", "https://openrouter.ai/api/v1"),
    ("nous", "https://inference-api.nousresearch.com/v1"),
    ("kilocode", "https://api.kilo.ai/api/gateway"),
    ("zai", "https://api.z.ai/api/paas/v4"),
])
def test_both_text_carriers_never_reach_the_wire_together(provider, base_url):
    history = _history(reasoning="thought", reasoning_content="thought")
    wire = _wire_assistant(provider, base_url, history)
    assert [k for k in _TEXT_CARRIERS if k in wire] == ["reasoning_content"]
    assert wire["reasoning_content"] == "thought"  # the thinking replay survives
    assert history[1]["reasoning"] == "thought" and history[1]["reasoning_content"] == "thought"  # history untouched


def test_ollama_keeps_the_carrier_it_reads():
    for provider, base_url in (("ollama-cloud", "https://ollama.com/v1"), ("custom", "http://localhost:11434/v1")):
        wire = _wire_assistant(provider, base_url, _history(reasoning="thought", reasoning_content="thought"))
        assert [k for k in _TEXT_CARRIERS if k in wire] == ["reasoning"], base_url
        assert wire["reasoning"] == "thought"


def test_convert_messages_dedupes_a_prebuilt_message_and_prefers_the_string_value():
    transport = ChatCompletionsTransport()
    both = _history(reasoning="thought", reasoning_content="thought")
    out = transport.convert_messages(both, model="m", base_url=_NINE_ROUTER, provider="custom")
    assert "reasoning" not in out[1] and out[1]["reasoning_content"] == "thought"
    # A null preferred key never wins over the real text.
    null_rc = _history(reasoning="thought", reasoning_content=None)
    out = transport.convert_messages(null_rc, model="m", base_url=_NINE_ROUTER, provider="custom")
    assert "reasoning_content" not in out[1] and out[1]["reasoning"] == "thought"


def test_single_carrier_and_details_pass_through_unchanged():
    transport = ChatCompletionsTransport()
    only_reasoning = _history(reasoning="thought")
    out = transport.convert_messages(only_reasoning, model="m", base_url=_NINE_ROUTER, provider="custom")
    assert out[1]["reasoning"] == "thought"
    details = [{"type": "reasoning.text", "text": "thought", "signature": "S"}]
    full = _history(reasoning="thought", reasoning_content="thought", reasoning_details=details)
    out = transport.convert_messages(full, model="m", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    assert out[1]["reasoning_details"] == details and out[1]["reasoning_content"] == "thought"
    assert "reasoning" not in out[1]


def test_must_echo_pad_survives_dedupe():
    wire = _wire_assistant("deepseek", "https://api.deepseek.com/v1", _history(), model="deepseek-v4-flash")
    assert wire["reasoning_content"] == " " and "reasoning" not in wire
