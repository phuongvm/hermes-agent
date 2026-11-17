"""Streaming provenance must precede delivery and remain stable per attempt."""
from types import SimpleNamespace

import pytest

from agent import tier_floor_policy as policy


@pytest.fixture
def agent():
    return SimpleNamespace(
        model="cb-agent-reviewer", provider="custom:ai-router", base_url="http://router.test/v1",
        config={"providers": {"ai-router": {"base_url": "http://router.test/v1"}},
                "tier_floor": {"allowlist": [
            "cx/gpt-5.6-sol-review", "cx/gpt-6-astra[1m]",
        ], "prefixes": [], "forbidden_markers": ["nemotron", ":free"]}},
    )


def test_headerless_stream_validates_first_model_before_delivery(agent):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))
    guard.accept(SimpleNamespace(model=None))
    assert guard.model_id == "cx/gpt-5.6-sol-review"


def test_namespaced_header_and_bare_body_are_same_explicit_route(agent):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(
        headers={"x-model-id": "cx/gpt-5.6-sol-review"}))
    guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))
    assert guard.model_id == "cx/gpt-5.6-sol-review"


@pytest.mark.parametrize("model", [None, "", "nemotron-free", "gpt-unapproved"])
def test_unverified_first_chunk_is_never_delivered(agent, model):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    with pytest.raises(policy.TierFloorViolationError):
        guard.accept(SimpleNamespace(model=model, choices=[{"delta": {"content": "blocked"}}]))


@pytest.mark.parametrize("model", [123, [], {}, False])
def test_malformed_identity_is_policy_violation_even_after_header(agent, model):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(
        headers={"x-model-id": "cx/gpt-5.6-sol-review"}))
    with pytest.raises(policy.TierFloorViolationError, match="invalid"):
        guard.accept(SimpleNamespace(model=model))


def test_even_allowed_model_switch_is_rejected(agent):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))
    with pytest.raises(policy.TierFloorViolationError, match="changed"):
        guard.accept(SimpleNamespace(model="cx/gpt-6-astra[1m]"))


def test_retry_has_no_previous_attempt_provenance(agent):
    first = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    first.accept(SimpleNamespace(model="gpt-5.6-sol-review"))
    retry = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    with pytest.raises(policy.TierFloorViolationError, match="missing"):
        retry.accept(SimpleNamespace(model=None))


def test_bare_mapping_is_not_enabled_for_other_providers(agent):
    agent.provider = "openai"
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    with pytest.raises(policy.TierFloorViolationError):
        guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))


def test_explicit_prefix_does_not_authorize_bare_alias(agent):
    agent.config["tier_floor"]["prefixes"] = ["cx/gpt-5"]
    qualified = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    qualified.accept(SimpleNamespace(model="cx/gpt-5-prefix-authorized"))
    bare = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    with pytest.raises(policy.TierFloorViolationError):
        bare.accept(SimpleNamespace(model="gpt-5-prefix-authorized"))


def test_resolved_custom_provider_uses_named_route(agent):
    agent.provider = "custom"
    agent.requested_provider = "custom:ai-router"
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))
    assert guard.model_id == "cx/gpt-5.6-sol-review"


def test_named_route_with_different_endpoint_cannot_alias(agent):
    agent.base_url = "http://other.test/v1"
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    with pytest.raises(policy.TierFloorViolationError):
        guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))


def test_header_body_disagreement_is_rejected(agent):
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(
        headers={"x-model-id": "cx/gpt-6-astra[1m]"}))
    with pytest.raises(policy.TierFloorViolationError, match="changed"):
        guard.accept(SimpleNamespace(model="gpt-5.6-sol-review"))


def test_bad_header_cannot_be_repaired_by_good_body(agent):
    with pytest.raises(policy.TierFloorViolationError):
        policy.Tier1StreamGuard(agent, SimpleNamespace(headers={"x-model-id": "nemotron-free"}))


def test_real_sdk_stream_passes_production_chunk_gate(agent):
    import json
    from unittest.mock import MagicMock

    import httpx
    from openai import OpenAI
    from agent.chat_completion_helpers import _StreamingCall

    for name in ("_capture_rate_limits", "_capture_credits", "_capture_nous_model_switch",
                 "_stream_diag_capture_response", "_check_openrouter_cache_status", "_touch_activity"):
        setattr(agent, name, MagicMock())
    agent.api_mode = "chat_completions"
    call = _StreamingCall(agent=agent, api_kwargs={"model": agent.model}, on_first_delta=lambda d: None)
    call._stream_attempt_is_active = lambda stream_attempt_id: True
    call._writer_still_current = lambda label: True
    frames = [
        {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
         "model": model, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
        for model, text in [("gpt-5.6-sol-review", "approved"), ("nemotron-free", "blocked")]
    ]
    wire = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=wire))
    delivered = []
    with httpx.Client(transport=transport) as http, OpenAI(
        api_key="fixture", base_url="http://127.0.0.1/v1", http_client=http,
    ) as client:
        with client.chat.completions.create(model=agent.model, messages=[], stream=True) as stream:
            call._chat_stream_created(stream)
            with pytest.raises(policy.TierFloorViolationError):
                for chunk in stream:
                    if call._accept_chat_chunk(0, chunk):
                        delivered.append(chunk.choices[0].delta.content)
    assert delivered == ["approved"]


def test_unverified_tool_call_rejected_at_production_chunk_gate(agent):
    from agent.chat_completion_helpers import _StreamingCall

    call = _StreamingCall(agent=agent, api_kwargs={"model": agent.model}, on_first_delta=lambda d: None)
    call._tier_stream_guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    chunk = SimpleNamespace(model=None, choices=[SimpleNamespace(delta=SimpleNamespace(
        tool_calls=[{"function": {"name": "terminal"}}]), finish_reason=None)])
    with pytest.raises(policy.TierFloorViolationError, match="missing"):
        call._accept_chat_chunk(0, chunk)
    assert not call.provider_tool_in_flight["yes"]


def test_non_specialist_stream_retains_existing_behavior(agent):
    agent.model = "ordinary-model"
    guard = policy.Tier1StreamGuard(agent, SimpleNamespace(headers={}))
    guard.accept(SimpleNamespace(model=None))
