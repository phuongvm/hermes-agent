import pytest
from acp_adapter.server import HermesACPAgent
from acp_adapter.session import SessionManager


class FakeSteerableAgent:
    def __init__(self):
        self.steered_texts = []

    def steer(self, text: str) -> bool:
        self.steered_texts.append(text)
        return True


@pytest.mark.asyncio
async def test_initialize_advertises_steering_support():
    agent = HermesACPAgent()
    resp = await agent.initialize()
    dumped = resp.model_dump(by_alias=True)
    assert "_meta" in dumped
    assert dumped["_meta"]["steering"]["supported"] is True


@pytest.mark.asyncio
async def test_session_steering_in_flight_success():
    manager = SessionManager()
    state = manager.create_session()
    fake_agent = FakeSteerableAgent()
    state.agent = fake_agent
    state.is_running = True

    server = HermesACPAgent(session_manager=manager)
    params = {
        "sessionId": state.session_id,
        "prompt": [{"type": "text", "text": "please also check tests"}],
    }

    res = await server.ext_method("session/steering", params)
    assert res == {"outcome": "injected"}
    assert fake_agent.steered_texts == ["please also check tests"]

    # Also test with leading underscore
    res2 = await server.ext_method("_session/steering", params)
    assert res2 == {"outcome": "injected"}
    assert len(fake_agent.steered_texts) == 2


@pytest.mark.asyncio
async def test_session_steering_not_running_returns_started_new_turn():
    manager = SessionManager()
    state = manager.create_session()
    fake_agent = FakeSteerableAgent()
    state.agent = fake_agent
    state.is_running = False

    server = HermesACPAgent(session_manager=manager)
    params = {
        "sessionId": state.session_id,
        "prompt": [{"type": "text", "text": "hello"}],
    }

    res = await server.ext_method("session/steering", params)
    assert res == {"outcome": "startedNewTurn"}
    assert fake_agent.steered_texts == []


@pytest.mark.asyncio
async def test_session_steering_invalid_session_returns_failed():
    server = HermesACPAgent()
    params = {
        "sessionId": "nonexistent-session-id",
        "prompt": [{"type": "text", "text": "hello"}],
    }
    res = await server.ext_method("session/steering", params)
    assert res == {"outcome": "failed"}


@pytest.mark.asyncio
async def test_unknown_ext_method_raises():
    from acp.exceptions import RequestError
    server = HermesACPAgent()
    with pytest.raises(RequestError):
        await server.ext_method("unknown/method", {})
