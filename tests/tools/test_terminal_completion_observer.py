"""Verify the host's foreground evidence without starting any process."""

import copy
import io
import json
import pickle
from types import SimpleNamespace

import pytest

from hermes_cli import lifecycle
from model_tools import _CallIds, _approval_observability, _emit_post_tool_call_hook
from tools.environments.base import BaseEnvironment
from tools.terminal_tool_result import confirms_foreground_completion, finalize_foreground_result


@pytest.mark.parametrize(
    ("backend_result", "expected"),
    [
        ({"returncode": 0}, True),
        ({}, False),
        ({"returncode": False}, False),
        ({"returncode": 1}, False),
        ({"returncode": 0, "stdin_error": "encoding failed"}, False),
        ({"returncode": 0, "hermes_timed_out": True}, False),
        ({"returncode": 0, "environment_recreated": True}, False),
    ],
)
def test_foreground_evidence_is_bound_to_the_observed_call(monkeypatch, tmp_path, backend_result, expected):
    events = []
    monkeypatch.setattr(lifecycle, "has_hook", lambda name: name == "post_tool_call")
    def capture(name, **fields):
        if name == "post_tool_call":
            events.append((name, fields))
        return []

    monkeypatch.setattr(lifecycle, "invoke_hook", capture)
    ids = _CallIds(task_id="task", session_id="session", turn_id="turn", tool_call_id="call")
    workdir = str(tmp_path)
    with _approval_observability(ids):
        result = finalize_foreground_result(
            command="echo fixture", result={"output": "fixture", **backend_result},
            env=SimpleNamespace(), env_type="local", effective_task_id="task", task_id="task",
            session_id="session", session_key="session", workdir=workdir, command_cwd=workdir,
            approval_note=None,
        )
    assert "terminal_foreground_completed" not in json.loads(result)

    def observe(value, **overrides):
        _emit_post_tool_call_hook(
            function_name="terminal", function_args={"command": "echo fixture", "workdir": workdir},
            result=value, **{**ids.hook_kwargs(), **overrides},
        )
        name, fields = events.pop()
        assert name == "post_tool_call"
        assert fields["result"] == result
        return fields.get("terminal_foreground_completed", False)

    assert observe(result) is expected
    assert observe(str(result)) is False
    for field in ("session_id", "turn_id", "tool_call_id"):
        assert observe(result, **{field: "different"}) is False
    assert observe(result, status="blocked") is False
    assert not events


class _FixtureEnv(BaseEnvironment):
    def cleanup(self) -> None:
        return None


@pytest.mark.parametrize(
    ("stdin_errors", "expected"),
    [
        ([], True),
        (["encoding failed"], False),
        ([OSError()], False),
    ],
)
def test_backend_wait_stdin_error_disqualifies_foreground_completion(monkeypatch, tmp_path, stdin_errors, expected):
    events = []
    monkeypatch.setattr(lifecycle, "has_hook", lambda name: name == "post_tool_call")
    monkeypatch.setattr(
        lifecycle, "invoke_hook",
        lambda name, **fields: events.append(fields) or [] if name == "post_tool_call" else [],
    )
    env = _FixtureEnv.__new__(_FixtureEnv)
    proc = SimpleNamespace(
        poll=lambda: 0,
        returncode=0,
        stdout=io.StringIO("fixture\n"),
        _hermes_stdin_errors=list(stdin_errors),
    )
    backend_result = env._wait_for_process(proc, timeout=5)
    assert backend_result["returncode"] == 0
    assert bool(backend_result.get("stdin_error")) is bool(stdin_errors)

    ids = _CallIds(task_id="task", session_id="session", turn_id="turn", tool_call_id="call")
    workdir = str(tmp_path)
    with _approval_observability(ids):
        result = finalize_foreground_result(
            command="cat", result=backend_result,
            env=SimpleNamespace(), env_type="local", effective_task_id="task", task_id="task",
            session_id="session", session_key="session", workdir=workdir, command_cwd=workdir,
            approval_note=None,
        )
    _emit_post_tool_call_hook(
        function_name="terminal", function_args={"command": "cat", "workdir": workdir},
        result=result, **ids.hook_kwargs(),
    )
    fields = events.pop()
    assert fields.get("terminal_foreground_completed", False) is expected
    assert not events


def test_foreground_result_survives_copy_deepcopy_and_pickle_as_plain_str(tmp_path):
    """Regression: compression snapshot copy.deepcopy(messages) crashed with
    TypeError: _CompletedForeground.__new__() missing 1 required positional argument: 'call'.
    """
    ids = _CallIds(task_id="task", session_id="session", turn_id="turn", tool_call_id="call")
    workdir = str(tmp_path)
    with _approval_observability(ids):
        result = finalize_foreground_result(
            command="echo fixture", result={"output": "fixture", "returncode": 0},
            env=SimpleNamespace(), env_type="local", effective_task_id="task", task_id="task",
            session_id="session", session_key="session", workdir=workdir, command_cwd=workdir,
            approval_note=None,
        )
    call_tuple = ("session", "turn", "call")
    assert confirms_foreground_completion(result, call_tuple) is True

    # Shallow copy
    shallow = copy.copy(result)
    assert type(shallow) is str
    assert shallow == result
    assert confirms_foreground_completion(shallow, call_tuple) is False

    # Deep copy inside a messages list (matches compression_facade._snapshot_worker)
    messages = [{"role": "tool", "tool_call_id": "call", "content": result}]
    snapshot = copy.deepcopy(messages)
    copied_content = snapshot[0]["content"]
    assert type(copied_content) is str
    assert copied_content == result
    assert confirms_foreground_completion(copied_content, call_tuple) is False

    # Pickle round-trip across all protocols
    for proto in range(pickle.HIGHEST_PROTOCOL + 1):
        restored = pickle.loads(pickle.dumps(result, protocol=proto))
        assert type(restored) is str
        assert restored == result
        assert confirms_foreground_completion(restored, call_tuple) is False
