"""Real-path Hermes + Nerve prerequisite workflow integration matrix (Task 4.1).

Exercises the authentic Gateway proposal selector, native child producer,
pre_llm_call/pre_tool_call/post_tool_call/pre_verify hook chain, native
tool_call bridge unwrapping, verified-handle bounded reader/adapters, and
real Nerve supervision against isolated HERMES_HOME/scratch storage.
Fakes only the LLM model transport and forbids external network sockets.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any

import pytest

from agent import chat_completion_helpers, delegation_context as dc, tool_executor as executor
from agent.prerequisite import adapters, catalog, reader, reservations
from agent.prerequisite.workflow import (
    _original_delivery_scope,
    authenticate_proposal_command,
    note_proposal_command,
    proposal_turn,
)
from gateway.platforms.event import MessageEvent
from hermes_cli import plugins
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_state import SessionDB
from run_agent import AIAgent
from tests.tools.proposal_catalog_fixture import (
    CHANGE,
    CONTEXT,
    GOAL,
    GOVERNANCE_PATHS,
    SKILL_NAMES,
    profile_config,
    proposal_entry,
)
from tools import file_state
from tools.delegate_tool_child_run import _ChildRun, _signal_child_stop
from tools.delegate_tool_routes import child_profile_scope
from tools.registry import invalidate_check_fn_cache, registry
from tools.terminal_tool import clear_session_cwd, record_session_cwd

OPENSPEC_PLUGIN_TOOLSET = "plugin:openspec"

_OPENSPEC_SCHEMAS: dict[str, dict[str, Any]] = {
    "openspec_status": {
        "name": "openspec_status",
        "description": "Read OpenSpec change status.",
        "parameters": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string"},
                "change": {"type": "string"},
                "schema": {"type": "string"},
            },
            "required": ["workdir", "change"],
        },
    },
    "openspec_instructions": {
        "name": "openspec_instructions",
        "description": "Read OpenSpec artifact instructions.",
        "parameters": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string"},
                "change": {"type": "string"},
                "artifact": {"type": "string"},
                "schema": {"type": "string"},
            },
            "required": ["workdir", "change", "artifact"],
        },
    },
    "openspec_spec_show": {
        "name": "openspec_spec_show",
        "description": "Show baseline OpenSpec specification.",
        "parameters": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string"},
                "spec": {"type": "string"},
            },
            "required": ["workdir", "spec"],
        },
    },
    "openspec_spec_list": {
        "name": "openspec_spec_list",
        "description": "List baseline OpenSpec specifications.",
        "parameters": {
            "type": "object",
            "properties": {
                "workdir": {"type": "string"},
            },
            "required": ["workdir"],
        },
    },
}


def _resolve_nerve_worktree() -> Path:
    explicit = os.environ.get("NERVE_TASK_INTENT_WORKTREE")
    if explicit:
        candidate = Path(explicit).resolve()
        if (candidate / "__init__.py").is_file():
            return candidate
    sibling = Path(__file__).resolve().parents[3] / "wt-nerve-prerequisite-descriptors"
    if (sibling / "__init__.py").is_file():
        return sibling
    pytest.skip("Set NERVE_TASK_INTENT_WORKTREE or create sibling wt-nerve-prerequisite-descriptors")
    raise AssertionError("unreachable")


def _pin_bytes(path: Path, content: bytes, *, relative_to: Path | None = None, max_bytes: int = 4096) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    st = path.lstat()
    stored_path = path.relative_to(relative_to).as_posix() if relative_to is not None else str(path)
    return {
        "path": stored_path,
        "device": st.st_dev,
        "inode": st.st_ino,
        "size": len(content),
        "max_file_bytes": max(max_bytes, len(content)),
        "sha256": sha256(content).hexdigest(),
    }


def _build_realistic_proposal_closure(planning: Path) -> dict[str, Any]:
    entry = proposal_entry(planning)
    rows = entry["resources"]

    status_rel = f"openspec/workspace/prerequisites/{CHANGE}/status.json"
    status_bytes = json.dumps({
        "changeName": CHANGE,
        "schemaName": "spec-driven",
        "artifacts": [
            {"id": name, "status": "ready"} for name in ("proposal", "design", "specs", "tasks")
        ],
    }).encode()
    rows["snapshot/status"] = _pin_bytes(planning / status_rel, status_bytes, relative_to=planning)

    for artifact in ("proposal", "design", "specs", "tasks"):
        rel = f"openspec/workspace/prerequisites/{CHANGE}/instructions/{artifact}.json"
        payload = json.dumps({
            "artifactId": artifact,
            "changeName": CHANGE,
            "instruction": f"Author validated {artifact} artifact for {CHANGE}.",
        }).encode()
        rows[f"snapshot/instructions/{artifact}"] = _pin_bytes(planning / rel, payload, relative_to=planning)

    for spec_name in catalog.BASELINES:
        rel = f"openspec/specs/{spec_name}/spec.md"
        spec_md = (
            f"# {spec_name} Specification\n\n"
            f"## Purpose\nBaseline contract for {spec_name}.\n\n"
            "## Requirements\n\n"
            f"### Requirement: Enforce {spec_name}\nThe system SHALL uphold {spec_name}.\n\n"
            "#### Scenario: Baseline active\n"
            "- **WHEN** workflow runs\n"
            f"- **THEN** {spec_name} remains enforced\n"
        ).encode()
        rows[f"baseline/{spec_name}"] = _pin_bytes(planning / rel, spec_md, relative_to=planning)

    for skill in SKILL_NAMES:
        sk_path = planning / "skills" / skill / "SKILL.md"
        sk_md = (
            f"---\nname: {skill}\ndescription: Mandatory proposal workflow skill {skill}\n---\n"
            f"# Skill {skill}\nFollow {skill} instructions strictly.\n"
        ).encode()
        rows[f"skill/{skill}"] = _pin_bytes(sk_path, sk_md)

    return entry


def _make_tool_call(call_id: str, name: str, args: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _make_completion(
    *,
    content: str = "",
    tool_calls: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    finish = "tool_calls" if tool_calls else "stop"
    msg = SimpleNamespace(
        role="assistant",
        content=content or (None if tool_calls else "Done."),
        tool_calls=tool_calls,
        reasoning_content=None,
        reasoning=None,
    )
    return SimpleNamespace(
        id="chatcmpl-offline-fixture",
        model="offline/test-model",
        choices=[SimpleNamespace(index=0, finish_reason=finish, message=msg)],
        usage=SimpleNamespace(prompt_tokens=32, completion_tokens=16, total_tokens=48),
    )


class FakeModelTransport:
    """Deterministic in-memory model transport feeding scripted assistant turns."""

    def __init__(self, turns: list[dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]]):
        self._turns = deque(turns)
        self.seen_requests: list[dict[str, Any]] = []

    def __call__(self, _agent: Any, api_kwargs: dict[str, Any], **_extra: Any) -> SimpleNamespace:
        self.seen_requests.append(api_kwargs)
        if not self._turns:
            return _make_completion(content="Completed fallback turn.")
        step = self._turns.popleft()
        spec = step(api_kwargs.get("messages", [])) if callable(step) else step
        tcs = [
            _make_tool_call(tc["id"], tc["name"], tc["args"])
            for tc in spec.get("tool_calls", [])
        ]
        return _make_completion(content=spec.get("content", ""), tool_calls=tcs or None)


_IN_BOUNDED_PREREQUISITE: ContextVar[bool] = ContextVar("in_bounded_prerequisite_test", default=False)
_IN_LOCAL_SOCKETPAIR: ContextVar[bool] = ContextVar("in_local_socketpair_test", default=False)
_OPENSPEC_CLI_RE = re.compile(r"(?:^|[\s/\\\"'])openspec(?:\.exe|\.cmd|\.bat)?(?=\s|$)", re.IGNORECASE)


def _guard_subprocesses(monkeypatch) -> None:
    real_run = subprocess.run
    real_popen = subprocess.Popen
    real_dispatch = adapters.dispatch_bounded_prerequisite

    def _wrapped_dispatch(*args: Any, **kwargs: Any):
        token = _IN_BOUNDED_PREREQUISITE.set(True)
        try:
            return real_dispatch(*args, **kwargs)
        finally:
            _IN_BOUNDED_PREREQUISITE.reset(token)

    def _check_cmd(cmd: Any) -> None:
        if _IN_BOUNDED_PREREQUISITE.get():
            raise AssertionError("Subprocess execution is strictly forbidden inside bounded prerequisite adapters")
        rendered = " ".join(str(x) for x in cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        lowered = rendered.lower()
        if "shell_must_never_run" in lowered or _OPENSPEC_CLI_RE.search(lowered):
            raise AssertionError(f"Forbidden OpenSpec/inline-shell subprocess during prerequisite test: {rendered}")

    def _guarded_run(cmd: Any, *args: Any, **kwargs: Any):
        _check_cmd(cmd)
        return real_run(cmd, *args, **kwargs)

    def _guarded_popen(cmd: Any, *args: Any, **kwargs: Any):
        _check_cmd(cmd)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(adapters, "dispatch_bounded_prerequisite", _wrapped_dispatch)
    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)


def _register_openspec_test_tools(monkeypatch) -> tuple[list[str], Callable[[], None]]:
    fallback_hits: list[str] = []
    saved_tools = dict(registry._tools)
    saved_toolsets = {k: set(v) for k, v in registry._toolset_Index.items()} if hasattr(registry, "_toolset_Index") else None

    for name, schema in _OPENSPEC_SCHEMAS.items():
        def _forbid_unverified(_args, _tool=name, **_kw):
            fallback_hits.append(_tool)
            raise AssertionError(f"Unverified OpenSpec handler {_tool} must never run for prerequisite allowance")

        registry.register(
            name=name,
            toolset=OPENSPEC_PLUGIN_TOOLSET,
            schema=schema,
            handler=_forbid_unverified,
            check_fn=lambda: True,
        )
    invalidate_check_fn_cache()

    def _restore() -> None:
        for name in _OPENSPEC_SCHEMAS:
            registry._tools.pop(name, None)
        if saved_toolsets is not None and hasattr(registry, "_toolset_Index"):
            registry._toolset_Index.clear()
            registry._toolset_Index.update(saved_toolsets)
        registry._tools.update(saved_tools)
        invalidate_check_fn_cache()

    _guard_subprocesses(monkeypatch)
    return fallback_hits, _restore


@pytest.fixture
def workflow_env(tmp_path, monkeypatch):
    nerve_root = _resolve_nerve_worktree()

    # Forbid external network connections completely while allowing OS local socketpair self-pipes.
    real_socketpair = socket.socketpair
    real_connect = socket.socket.connect

    def _safe_socketpair(*args: Any, **kwargs: Any):
        token = _IN_LOCAL_SOCKETPAIR.set(True)
        try:
            return real_socketpair(*args, **kwargs)
        finally:
            _IN_LOCAL_SOCKETPAIR.reset(token)

    def _forbid_connect(sock_self: socket.socket, address: Any):
        if _IN_LOCAL_SOCKETPAIR.get():
            return real_connect(sock_self, address)
        raise RuntimeError("External network sockets are forbidden in Task 4.1 integration tests")

    def _forbid_socket(*_a: Any, **_kw: Any):
        raise RuntimeError("External network sockets are forbidden in Task 4.1 integration tests")

    monkeypatch.setattr(socket, "socketpair", _safe_socketpair)
    monkeypatch.setattr(socket.socket, "connect", _forbid_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid_socket)
    monkeypatch.setattr(socket, "create_connection", _forbid_socket)
    monkeypatch.setattr("hermes_cli.env_loader.hydrate_profile_secret_sources", lambda _: None)

    parent_home = tmp_path / "parent_home"
    child_home = tmp_path / "child_home"
    planning = tmp_path / "planning"
    parent_home.mkdir()
    child_home.mkdir()

    events_path = tmp_path / "nervous-events.jsonl"
    monkeypatch.setenv("HERMES_HOME", str(child_home))
    monkeypatch.setenv("HERMES_NERVE_NERVOUS_EVENTS", str(events_path))
    monkeypatch.setenv("HERMES_NERVE_OUTCOMES", str(tmp_path / "outcomes.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_POLICY_MEMORY", str(tmp_path / "policy.jsonl"))
    monkeypatch.setenv("HERMES_NERVE_PREFERENCES", str(tmp_path / "preferences.json"))
    monkeypatch.setenv("HERMES_NERVE_GATE_EVENTS", str(tmp_path / "gate-events.jsonl"))
    for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_TASK_ID", "HERMES_NERVE_REMOTE_CONTROL_FILE"):
        monkeypatch.delenv(key, raising=False)

    parent_entry = _build_realistic_proposal_closure(planning)
    child_entry = deepcopy(parent_entry)
    extra_cfg = {
        "model": {"context_length": 128000},
        "auxiliary": {"background_review": {"enabled": False}},
        "skills": {"external_dirs": [str(planning / "skills")]},
        "tools": {
            "tool_search": {
                "enabled": "on",
                "defer": ["skill_view"],
            }
        },
    }
    (parent_home / "config.yaml").write_text(
        json.dumps({**profile_config(parent_entry), **extra_cfg}), encoding="utf-8"
    )
    (child_home / "config.yaml").write_text(
        json.dumps({**profile_config(child_entry), **extra_cfg}), encoding="utf-8"
    )

    spec = importlib.util.spec_from_file_location(
        "nerve_task41_plugin",
        nerve_root / "__init__.py",
        submodule_search_locations=[str(nerve_root)],
    )
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, plugin)
    spec.loader.exec_module(plugin)

    system = plugin.nervous.NervousSystem(
        engine_factory=lambda: (_ for _ in ()).throw(AssertionError("No remote Jev provider calls allowed"))
    )
    monkeypatch.setattr(plugin.nervous, "_default", system)
    gate_override: dict[str, Any] = {"verdict": None}
    work_override: dict[str, Any] = {"verdict": None}
    monkeypatch.setattr(plugin.gate, "pre_tool_call", lambda **_kw: gate_override["verdict"])
    monkeypatch.setattr(plugin.work_hooks, "pre_tool_call", lambda **_kw: work_override["verdict"])

    manager = plugins.PluginManager(str(child_home))
    manifest = plugins.PluginManifest(name="nerve", source="user")
    plugin_settings = {
        "nerve_profile": "operator",
        "gate_mode": "enforce",
        "nervous_mode": "correct_next",
        "nervous_turn_admission": False,
        "nervous_local_learning": False,
        "nervous_policy_memory_enabled": False,
        "nervous_call_threshold": 1.0,
        "prerequisite_max_operations": 48,
        "prerequisite_max_seconds": 300,
        "work_supervision_db": str(tmp_path / "work.db"),
        "remote_data_dir": str(tmp_path / "remote"),
    }

    class _HostPluginCtx:
        def __init__(self, inner: plugins.PluginContext):
            self._inner = inner
            self.reserve_prerequisite_read = inner.reserve_prerequisite_read
            self.register_pre_verify_no_edits = inner.register_pre_verify_no_edits

        def get_config(self, key: str, default: Any = None) -> Any:
            return plugin_settings.get(key, default)

        def register_tool(self, **_kw: Any) -> None:
            return

        def register_hook(self, name: str, callback: Any) -> None:
            self._inner.register_hook(name, callback)

        def register_context_engine(self, _engine: Any) -> None:
            return

    real_ctx = plugins.PluginContext(manifest, manager)
    plugin.register(_HostPluginCtx(real_ctx))
    manager._discovered = True
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)

    fallback_hits, restore_registry = _register_openspec_test_tools(monkeypatch)
    file_state.get_registry().clear()

    try:
        yield SimpleNamespace(
            tmp_path=tmp_path,
            planning=planning,
            parent_home=parent_home,
            child_home=child_home,
            parent_entry=parent_entry,
            child_entry=child_entry,
            plugin=plugin,
            system=system,
            manager=manager,
            gate_override=gate_override,
            work_override=work_override,
            events_path=events_path,
            fallback_hits=fallback_hits,
            last_child=None,
        )
    finally:
        system.configure(enabled=False)
        restore_registry()
        file_state.get_registry().clear()


@contextmanager
def _active_native_proposal_turn(
    env: SimpleNamespace,
    *,
    parent_session_id: str = "parent-sess",
    child_task_id: str = "proposal-child-task",
    child_workspace: Path | None = None,
    event_internal: bool = False,
    allow_gateway_control: bool = True,
    authenticate: bool = True,
):
    workspace = env.planning if child_workspace is None else child_workspace
    event = MessageEvent(
        text=f"/openspec-propose {CHANGE}",
        user_id="commander-user",
        internal=event_internal,
        allow_gateway_control=allow_gateway_control,
    )
    event.metadata["proposal_selection"] = env.child_entry
    home_token = set_hermes_home_override(str(env.parent_home))
    dummy_child = SimpleNamespace(_delegate_profile_home=str(env.child_home))
    try:
        with child_profile_scope(dummy_child):
            record_session_cwd(child_task_id, str(workspace))
        if authenticate:
            with _original_delivery_scope(event):
                authenticate_proposal_command(event, event.text)
                skill_path = env.parent_entry["resources"]["skill/openspec-propose"]["path"]
                note_proposal_command(event, "route", "openspec-propose", CHANGE, skill_path)
        with proposal_turn(event, "route", parent_session_id):
            yield
    finally:
        try:
            with child_profile_scope(dummy_child):
                clear_session_cwd(child_task_id)
        finally:
            reset_hermes_home_override(home_token)


def _run_child_agent_turns(
    env: SimpleNamespace,
    monkeypatch,
    turns: list[dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]],
    *,
    goal: str = GOAL,
    context: str | None = CONTEXT,
    child_session_id: str = "child-sess",
    parent_session_id: str = "parent-sess",
    event_parent_id: str = "parent-sess",
    child_task_id: str = "proposal-child-task",
    enabled_toolsets: list[str] | None = None,
    event_internal: bool = False,
    allow_gateway_control: bool = True,
    authenticate: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], SessionDB]:
    transport = FakeModelTransport(turns)
    monkeypatch.setattr(chat_completion_helpers, "interruptible_api_call", transport)
    monkeypatch.setattr(chat_completion_helpers, "interruptible_streaming_api_call", transport)

    db = SessionDB(db_path=env.tmp_path / f"{child_session_id}.db")
    db.create_session(parent_session_id, "cli")
    child = AIAgent(
        api_key="offline-key",
        base_url="http://127.0.0.1:1/v1",
        model="offline/test-model",
        platform="subagent",
        quiet_mode=True,
        session_db=db,
        session_id=child_session_id,
        parent_session_id=parent_session_id,
        enabled_toolsets=enabled_toolsets or ["file", "skills", OPENSPEC_PLUGIN_TOOLSET],
        skip_context_files=True,
        skip_memory=True,
        max_iterations=40,
    )
    child.streaming = False
    child._delegate_context = context
    child._delegate_profile_home = str(env.child_home)
    env.last_child = child
    parent = SimpleNamespace(session_id=parent_session_id)

    try:
        with _active_native_proposal_turn(
            env,
            parent_session_id=event_parent_id,
            child_task_id=child_task_id,
            event_internal=event_internal,
            allow_gateway_control=allow_gateway_control,
            authenticate=authenticate,
        ):
            with child_profile_scope(child):
                run = _ChildRun(child, parent, 0, goal, None, None, child_task_id=child_task_id)
                result, error, deferred = run.await_child()
    finally:
        if hasattr(child, "release_clients"):
            child.release_clients()
    assert error is None and not deferred
    assert isinstance(result, dict)
    return result, result.get("messages", []), db


def _decode_tool_json(raw_content: Any) -> dict[str, Any]:
    raw = str(raw_content or "").lstrip()
    if raw.startswith("{"):
        try:
            obj, _end = json.JSONDecoder().raw_decode(raw)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    clean = raw.split("\n\n<JEV_NERVOUS_SYSTEM>", 1)[0]
    return {"raw": clean}


def _tool_messages(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            by_id[str(msg["tool_call_id"])] = _decode_tool_json(msg.get("content"))
    return by_id


def _make_unpinned_files(base_dir: Path, prefix: str, count: int = 12) -> list[Path]:
    paths: list[Path] = []
    for idx in range(count):
        path = base_dir / f"{prefix}-{idx}.txt"
        path.write_text(f"{prefix} note {idx}\n", encoding="utf-8")
        paths.append(path)
    return paths


def _matrix_spec_for_tool(planning: Path, tool_name: str) -> dict[str, Any]:
    workdir = str(planning)
    table: dict[str, dict[str, Any]] = {
        "read_file": {
            "valid": {"path": str(planning / "rules/AGENTS.md"), "offset": 1, "limit": 20},
            "valid_alt": {"path": str(planning / "rules/AGENTS.md"), "offset": 2, "limit": 20},
            "bad_param": {"path": str(planning / "rules/undeclared.md"), "offset": 1, "limit": 20},
            "pin_key": "governance/AGENTS.md",
            "pin_path": planning / "rules/AGENTS.md",
        },
        "skill_view": {
            "valid": {"name": "verification-before-claim"},
            "valid_alt": {"name": "verification-before-claim", "file_path": "references/completion-gate.md"},
            "bad_param": {"name": "verification-before-claim", "file_path": "references/undeclared.md"},
            "pin_key": "skill/verification-before-claim",
            "pin_path": planning / "skills/verification-before-claim/SKILL.md",
        },
        "openspec_status": {
            "valid": {"workdir": workdir, "change": CHANGE},
            "valid_alt": {"workdir": workdir, "change": CHANGE, "schema": "spec-driven"},
            "bad_param": {"workdir": workdir, "change": "wrong-change"},
            "pin_key": "snapshot/status",
            "pin_path": planning / f"openspec/workspace/prerequisites/{CHANGE}/status.json",
        },
        "openspec_instructions": {
            "valid": {"workdir": workdir, "change": CHANGE, "artifact": "proposal"},
            "valid_alt": {"workdir": workdir, "change": CHANGE, "artifact": "proposal", "schema": "spec-driven"},
            "bad_param": {"workdir": workdir, "change": CHANGE, "artifact": "apply"},
            "pin_key": "snapshot/instructions/proposal",
            "pin_path": planning / f"openspec/workspace/prerequisites/{CHANGE}/instructions/proposal.json",
        },
        "openspec_spec_show": {
            "valid": {"workdir": workdir, "spec": "nerve-crew-dod-and-role-handoff"},
            "valid_alt": {"workdir": workdir, "spec": "nerve-reflex-resilience"},
            "bad_param": {"workdir": workdir, "spec": "undeclared-spec"},
            "pin_key": "baseline/nerve-crew-dod-and-role-handoff",
            "pin_path": planning / "openspec/specs/nerve-crew-dod-and-role-handoff/spec.md",
        },
        "openspec_spec_list": {
            "valid": {"workdir": workdir},
            "valid_alt": {"workdir": workdir},
            "bad_param": {"workdir": str(planning / "other")},
            "pin_key": "baseline/nerve-crew-dod-and-role-handoff",
            "pin_path": planning / "openspec/specs/nerve-crew-dod-and-role-handoff/spec.md",
        },
    }
    return table[tool_name]


@pytest.mark.parametrize(
    "tool_name",
    [
        "read_file",
        "skill_view",
        "openspec_instructions",
        "openspec_status",
        "openspec_spec_show",
        "openspec_spec_list",
    ],
)
def test_full_six_tool_matrix_beyond_12_binding_bytes_denial_replay_and_exhaustion(
    workflow_env, monkeypatch, tool_name
):
    env = workflow_env
    env.system.configure(prerequisite_max_operations=3, prerequisite_max_seconds=120)
    spec = _matrix_spec_for_tool(env.planning, tool_name)

    # Create 12 unpinned scratch files so actions 1..12 trigger Nerve's local-progress-stall at 12.
    scratch_files = _make_unpinned_files(env.tmp_path, "unpinned-note", 12)
    orig_pin_bytes = spec["pin_path"].read_bytes()
    orig_pin_ino = spec["pin_path"].lstat().st_ino
    step_bytes: dict[str, int] = {}
    last_total = 0

    def _tamper_after_reserve(**kwargs: Any) -> None:
        if kwargs.get("tool_call_id") == f"{tool_name}-oversize":
            spec["pin_path"].write_bytes(b"X" * 9000)

    env.manager._hooks["pre_tool_call"].append(_tamper_after_reserve)

    def _seed_replay_slot() -> None:
        active_intent = getattr(env.last_child, "_delegate_running_intent", None)
        assert active_intent is not None
        assert (
            reservations.reserve_read(
                active_intent, f"child-{tool_name}", f"{tool_name}-replay", tool_name, dict(spec["valid"])
            )
            == "reserved"
        )
        reservations.discard_read(active_intent, f"child-{tool_name}", f"{tool_name}-replay")

    def _turn_step(
        label: str,
        payload: dict[str, Any],
        side_effect: Callable[[], Any] | None = None,
    ) -> Callable[[list[dict[str, Any]]], dict[str, Any]]:
        def _run(_msgs: list[dict[str, Any]]) -> dict[str, Any]:
            nonlocal last_total
            cur = physical.bytes_read
            step_bytes[label] = cur - last_total
            last_total = cur
            if side_effect is not None:
                side_effect()
            return payload

        return _run

    turns: list[Any] = [
        {
            "tool_calls": [
                {"id": f"pre-{i}", "name": "read_file", "args": {"path": str(scratch_files[i])}}
                for i in range(12)
            ]
        },
        # Action 13 (slot 1/3): uncached resource grows oversize after reserve -> rejected at handle stat with 0 bytes read!
        _turn_step(
            "pre12",
            {"tool_calls": [{"id": f"{tool_name}-oversize", "name": tool_name, "args": dict(spec["valid"])}]},
        ),
        # Action 14 (slot 2/3): restore exact pinned bytes; valid read beyond action 12 -> MUST SUCCEED!
        _turn_step(
            "oversize",
            {"tool_calls": [{"id": f"{tool_name}-ok", "name": tool_name, "args": dict(spec["valid"])}]},
            side_effect=lambda: spec["pin_path"].write_bytes(orig_pin_bytes),
        ),
        # Action 15: undeclared parameter/resource -> MUST BE BLOCKED by progress guard (0 slots spent).
        _turn_step(
            "ok",
            {"tool_calls": [{"id": f"{tool_name}-bad", "name": tool_name, "args": dict(spec["bad_param"])}]},
        ),
        # Action 16 (slot 3/3 consumed into seen): replay of consumed call_id -> MUST BE BLOCKED without re-execution!
        _turn_step(
            "bad",
            {"tool_calls": [{"id": f"{tool_name}-replay", "name": tool_name, "args": dict(spec["valid_alt"])}]},
            side_effect=_seed_replay_slot,
        ),
        # Action 17 (slot 4 > max 3): budget exhausted -> blocked by progress guard!
        _turn_step(
            "replay",
            {"tool_calls": [{"id": f"{tool_name}-exhausted", "name": tool_name, "args": dict(spec["valid"])}]},
        ),
        _turn_step("exhausted", {"content": "Finished matrix inspection."}),
    ]

    try:
        with reader.measure_physical_reads() as physical:
            _res, messages, db = _run_child_agent_turns(
                env, monkeypatch, turns, child_session_id=f"child-{tool_name}"
            )
        db.close()
    finally:
        env.manager._hooks["pre_tool_call"].remove(_tamper_after_reserve)
        spec["pin_path"].write_bytes(orig_pin_bytes)
    assert spec["pin_path"].lstat().st_ino == orig_pin_ino

    tool_rows = [
        (m.get("tool_call_id"), _decode_tool_json(m.get("content")))
        for m in messages
        if m.get("role") == "tool"
    ]
    by_call: dict[str, list[dict[str, Any]]] = {}
    for cid, payload in tool_rows:
        by_call.setdefault(str(cid), []).append(payload)

    # 1. Oversize uncached resource rejected at handle open before os.read with 0 physical bytes read!
    oversize_payload = by_call[f"{tool_name}-oversize"][0]
    assert oversize_payload.get("disposition") in {"size_mismatch", "oversize_resource"}
    assert "XXXX" not in json.dumps(oversize_payload)
    assert step_bytes["oversize"] == 0

    # 2. Success beyond action 12 with real bounded physical bytes read (and zero unverified fallback).
    ok_payload = by_call[f"{tool_name}-ok"][0]
    assert "error" not in ok_payload
    assert ok_payload.get("ok") is True or ok_payload.get("success") is True or "content" in ok_payload
    assert 0 < step_bytes["ok"] <= catalog.MAX_SOURCE_BYTES
    assert env.fallback_hits == []

    # 3. Exact parameter/resource binding enforced with 0 physical bytes read.
    bad_payload = by_call[f"{tool_name}-bad"][0]
    assert "progress guard" in str(bad_payload.get("error", "")).lower()
    assert step_bytes["bad"] == 0

    # 4. Reused call_id rejected on replay with 0 physical bytes read.
    replay_payload = by_call[f"{tool_name}-replay"][0]
    assert "error" in replay_payload
    assert step_bytes["replay"] == 0

    # 5. Exhaustion after 3/3 slots spent blocks further prerequisite reads with 0 physical bytes read.
    exhausted_payload = by_call[f"{tool_name}-exhausted"][0]
    assert "progress guard" in str(exhausted_payload.get("error", "")).lower()
    assert step_bytes["exhausted"] == 0

    receipts = env.system.accounting_receipts(session_id=f"child-{tool_name}")
    post_counts = [r["counters"]["tool_actions"] for r in receipts if r["stage"] == "post_tool"]
    assert post_counts[:14] == list(range(1, 15))

    # 6. Downstream independent gate denial preserved verbatim with 0 physical bytes read!
    veto_turns: list[Any] = [
        {
            "tool_calls": [
                {"id": f"vp-{i}", "name": "read_file", "args": {"path": str(scratch_files[i])}}
                for i in range(12)
            ]
        },
        {"tool_calls": [{"id": f"{tool_name}-veto", "name": tool_name, "args": dict(spec["valid_alt"])}]},
        {"content": "Veto checked."},
    ]
    env.gate_override["verdict"] = {"action": "block", "message": "independent action-gate veto"}
    try:
        with reader.measure_physical_reads() as veto_physical:
            _vres, veto_msgs, vdb = _run_child_agent_turns(
                env, monkeypatch, veto_turns, child_session_id=f"child-{tool_name}-veto"
            )
        vdb.close()
    finally:
        env.gate_override["verdict"] = None
    veto_payload = _tool_messages(veto_msgs)[f"{tool_name}-veto"]
    assert "independent action-gate veto" in str(veto_payload.get("error", ""))
    assert veto_physical.bytes_read == 0


def test_verified_native_tool_call_unwrapping_and_denied_forged_or_shell_wrappers(
    workflow_env, monkeypatch
):
    env = workflow_env
    env.system.configure(prerequisite_max_operations=6, prerequisite_max_seconds=120)

    wrap_scratch = _make_unpinned_files(env.tmp_path, "wrap-scratch", 12)
    forged_scratch = _make_unpinned_files(env.tmp_path, "forged-scratch", 12)
    workdir = str(env.planning)

    turns: list[Any] = [
        {
            "tool_calls": [
                {"id": f"w-{i}", "name": "read_file", "args": {"path": str(wrap_scratch[i]), "offset": 1, "limit": 1}}
                for i in range(12)
            ]
        },
        # 1. Valid deferrable tools invoked via native `tool_call` wrapper -> unwrapped & admitted!
        {
            "tool_calls": [
                {
                    "id": "wrap-os-status",
                    "name": "tool_call",
                    "args": {"name": "openspec_status", "arguments": {"workdir": workdir, "change": CHANGE}},
                },
                {
                    "id": "wrap-skill",
                    "name": "tool_call",
                    "args": {"name": "skill_view", "arguments": {"name": "openspec-propose"}},
                },
            ]
        },
        # 2a. Direct terminal & execute_code receive ZERO prerequisite allowance under progress stall!
        {
            "tool_calls": [
                {
                    "id": "direct-terminal",
                    "name": "terminal",
                    "args": {"command": "cat rules/AGENTS.md"},
                }
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "direct-exec-code",
                    "name": "execute_code",
                    "args": {"code": "print(open('rules/AGENTS.md').read())"},
                }
            ]
        },
        # 2b. Wrapped terminal, wrapped execute_code, forged tool name, and invalid schema -> ALL DENIED!
        {
            "tool_calls": [
                {
                    "id": "wrap-exec-code",
                    "name": "tool_call",
                    "args": {"name": "execute_code", "arguments": {"code": "print('forbidden')"}},
                },
                {
                    "id": "wrap-forged-name",
                    "name": "tool_call",
                    "args": {"name": "nonexistent_prereq_tool", "arguments": {}},
                },
                {
                    "id": "wrap-bad-schema",
                    "name": "tool_call",
                    "args": {"name": "openspec_status", "arguments": {"workdir": workdir}},
                },
                {
                    "id": "wrap-terminal",
                    "name": "tool_call",
                    "args": {"name": "terminal", "arguments": {"command": "echo hi"}},
                },
            ]
        },
        {"content": "Wrapper checks complete."},
    ]

    with reader.measure_physical_reads() as metrics:
        _res, messages, db = _run_child_agent_turns(
            env,
            monkeypatch,
            turns,
            child_session_id="child-wrappers",
            enabled_toolsets=["file", "skills", "terminal", "code_execution", OPENSPEC_PLUGIN_TOOLSET],
        )
    db.close()
    by_id = _tool_messages(messages)

    assert by_id["wrap-os-status"]["ok"] is True
    assert by_id["wrap-skill"]["success"] is True
    assert metrics.bytes_read > 0

    for denied_id in (
        "direct-terminal",
        "direct-exec-code",
        "wrap-exec-code",
        "wrap-forged-name",
        "wrap-bad-schema",
        "wrap-terminal",
    ):
        payload_str = json.dumps(by_id[denied_id]).lower()
        assert "error" in payload_str or "blocked" in payload_str or "progress guard" in payload_str

    # 3. Forged/unauthorized Gateway event (`authenticate=False`) grants NO prerequisite selection!
    forged_turns = [
        {
            "tool_calls": [
                {"id": f"fg-{i}", "name": "read_file", "args": {"path": str(forged_scratch[i])}}
                for i in range(12)
            ]
        },
        {
            "tool_calls": [
                {
                    "id": "fg-13",
                    "name": "read_file",
                    "args": {"path": str(env.planning / "rules/AGENTS.md"), "offset": 1, "limit": 5},
                }
            ]
        },
        {"content": "Forged event stopped."},
    ]
    _res_fg, msgs_fg, db_fg = _run_child_agent_turns(
        env, monkeypatch, forged_turns, child_session_id="child-forged-event", authenticate=False
    )
    db_fg.close()
    assert "progress guard" in json.dumps(_tool_messages(msgs_fg)["fg-13"]).lower()


def test_end_to_end_proposal_closure_authoring_validation_and_completion_gate(
    workflow_env, monkeypatch
):
    env = workflow_env
    env.system.configure(prerequisite_max_operations=64, prerequisite_max_seconds=300)
    planning = env.planning
    workdir = str(planning)

    # Build the complete >22-operation prerequisite read sequence across the entire frozen closure!
    closure_calls: list[dict[str, Any]] = []
    for idx, gov_rel in enumerate(GOVERNANCE_PATHS):
        closure_calls.append({
            "id": f"gov-{idx}",
            "name": "read_file",
            "args": {"path": str(planning / "rules" / gov_rel), "offset": 1, "limit": 50},
        })
    closure_calls.extend([
        {
            "id": "proj-share",
            "name": "read_file",
            "args": {"path": str(planning / "openspec/workspace/sessions/agent_share.md"), "offset": 1, "limit": 50},
        },
        {
            "id": "proj-exp",
            "name": "read_file",
            "args": {"path": str(planning / "openspec/workspace/explorations/approved.md"), "offset": 1, "limit": 50},
        },
        {
            "id": "proj-rcpt",
            "name": "read_file",
            "args": {"path": str(planning / "openspec/workspace/explorations/receipt.md"), "offset": 1, "limit": 50},
        },
    ])
    for idx, sk_name in enumerate(("verification-before-claim", "openspec-propose", "openspec-workflow-governance")):
        closure_calls.append({"id": f"sk-{idx}", "name": "skill_view", "args": {"name": sk_name}})
    closure_calls.extend([
        {
            "id": "ref-0",
            "name": "skill_view",
            "args": {"name": "verification-before-claim", "file_path": "references/completion-gate.md"},
        },
        {
            "id": "ref-1",
            "name": "skill_view",
            "args": {
                "name": "openspec-workflow-governance",
                "file_path": "references/openspec-agent-workflow-guide.md",
            },
        },
        {"id": "os-status", "name": "openspec_status", "args": {"workdir": workdir, "change": CHANGE}},
        {
            "id": "os-instr-prop",
            "name": "openspec_instructions",
            "args": {"workdir": workdir, "change": CHANGE, "artifact": "proposal"},
        },
        {
            "id": "os-instr-des",
            "name": "openspec_instructions",
            "args": {"workdir": workdir, "change": CHANGE, "artifact": "design"},
        },
        {"id": "os-list", "name": "openspec_spec_list", "args": {"workdir": workdir}},
        {
            "id": "os-show",
            "name": "openspec_spec_show",
            "args": {"workdir": workdir, "spec": "nerve-crew-dod-and-role-handoff"},
        },
    ])
    assert len(closure_calls) >= 24  # Crosses well past ordinary 12-action stall threshold!

    change_dir = planning / "openspec" / "changes" / CHANGE
    proposal_md = "# Proposal: example-topic\n\n## Why\nEnsure bounded prerequisite progress.\n\n## What Changes\nAdd bounded adapters.\n"
    design_md = "# Design: example-topic\n\n## Decisions\nUse verified handles and nonsecret receipts.\n"
    delta_spec_md = (
        "# Delta Specification\n\n## ADDED Requirements\n\n"
        "### Requirement: Bounded Proposal Closure\nThe workflow SHALL validate prerequisites.\n\n"
        "#### Scenario: Proposal authored\n- **WHEN** prerequisites finish\n- **THEN** artifacts validate cleanly\n"
    )
    tasks_md = "# Tasks\n\n- [ ] 1.1 Implement bounded change\n- [ ] 1.2 Verify change\n"

    turns: list[Any] = [
        {"tool_calls": closure_calls[:12]},
        {"tool_calls": closure_calls[12:]},
        # Premature stop before writing outputs -> Nerve pre_verify_no_edits nudges continuation!
        {"content": "Attempting premature completion before authoring files."},
        # Author the complete proposal package via real write_file tool!
        {
            "tool_calls": [
                {"id": "w-prop", "name": "write_file", "args": {"path": str(change_dir / "proposal.md"), "content": proposal_md}},
                {"id": "w-des", "name": "write_file", "args": {"path": str(change_dir / "design.md"), "content": design_md}},
                {
                    "id": "w-spec",
                    "name": "write_file",
                    "args": {"path": str(change_dir / "specs/nerve-crew-dod-and-role-handoff/spec.md"), "content": delta_spec_md},
                },
                {"id": "w-tasks", "name": "write_file", "args": {"path": str(change_dir / "tasks.md"), "content": tasks_md}},
            ]
        },
        # After relevant output dispatch, prerequisite allowance is retired to OUTPUT:
        # another prerequisite read_file is no longer exempted once stall/output governs.
        {"content": "Proposal package created and validated."},
    ]

    with reader.measure_physical_reads() as metrics:
        result, messages, db = _run_child_agent_turns(
            env, monkeypatch, turns, goal="Create proposal.md, design.md, specs, and tasks.md for example-topic.",
            child_session_id="child-author",
        )
    db.close()

    assert result.get("completed") is True
    assert 0 < metrics.bytes_read <= catalog.MAX_SOURCE_BYTES
    by_id = _tool_messages(messages)
    for call in closure_calls:
        assert "error" not in by_id[call["id"]], f"Failed closure call {call['id']}: {by_id[call['id']]}"

    # Validate authored OpenSpec proposal package on disk!
    assert (change_dir / "proposal.md").read_text(encoding="utf-8") == proposal_md
    assert (change_dir / "design.md").read_text(encoding="utf-8") == design_md
    assert "- [ ] 1.1" in (change_dir / "tasks.md").read_text(encoding="utf-8")
    parsed_delta = adapters.parse_spec_markdown(
        (change_dir / "specs/nerve-crew-dod-and-role-handoff/spec.md").read_text(encoding="utf-8")
    )
    assert len(parsed_delta["requirements"]) == 1
    assert parsed_delta["requirements"][0]["scenarios"][0]["steps"][0]["type"] == "WHEN"

    receipts = env.system.accounting_receipts(session_id="child-author")
    assert any(r["guard_disposition"] == "completion_blocked" for r in receipts)
    assert any(
        r["operation_category"] == "relevant_mutation" and r["counters"]["successful_relevant_mutations"] >= 1
        for r in receipts
    )


def test_race_continuation_termination_missing_manifest_replacement_and_inflight_revocation(
    workflow_env, monkeypatch
):
    env = workflow_env
    planning = env.planning
    agents_doc = planning / "rules" / "AGENTS.md"
    safety_doc = planning / "rules" / "shared" / "safety.md"

    # 1. Missing mandatory manifest entry in child config fails closed at selection!
    incomplete_entry = deepcopy(env.child_entry)
    incomplete_entry["resources"].pop("snapshot/status")
    bad_home = env.tmp_path / "bad_child_home"
    bad_home.mkdir()
    (bad_home / "config.yaml").write_text(
        json.dumps({
            **profile_config(incomplete_entry),
            "model": {"context_length": 128000},
            "auxiliary": {"background_review": {"enabled": False}},
        }),
        encoding="utf-8",
    )
    orig_child_home = env.child_home
    env.child_home = bad_home
    try:
        miss_scratch = _make_unpinned_files(env.tmp_path, "miss-scratch", 12)
        miss_turns = [
            {"tool_calls": [{"id": f"m-{i}", "name": "read_file", "args": {"path": str(miss_scratch[i])}} for i in range(12)]},
            {"tool_calls": [{"id": "m-13", "name": "read_file", "args": {"path": str(agents_doc)}}]},
            {"content": "done"},
        ]
        _r, msgs_miss, db_miss = _run_child_agent_turns(
            env, monkeypatch, miss_turns, child_session_id="child-missing-manifest"
        )
        db_miss.close()
        assert "progress guard" in json.dumps(_tool_messages(msgs_miss)["m-13"]).lower()
    finally:
        env.child_home = orig_child_home

    # 2. Final-slot concurrency race + continuation without budget renewal + termination closure!
    with _active_native_proposal_turn(env, parent_session_id="parent-race", child_task_id="race-task"):
        scope = dict(
            actor_session_id="child-race",
            parent_session_id="parent-race",
            goal=GOAL,
            context=CONTEXT,
            child_task_id="race-task",
        )
        dummy_child = SimpleNamespace(_delegate_profile_home=str(env.child_home))
        with child_profile_scope(dummy_child), dc.native_task_intent(**scope) as intent:
            assert intent is not None and intent.proposal_selection is not None
            env.system.configure(prerequisite_max_operations=1, prerequisite_max_seconds=120)
            env.system.start_turn(
                user_message=GOAL,
                session_id="child-race",
                parent_session_id="parent-race",
                turn_id="t-race-1",
                task_intent=intent,
            )
            barrier = Barrier(2, timeout=10)

            def compete(idx: int) -> str:
                args = {"path": str(agents_doc if idx == 0 else safety_doc), "offset": 1, "limit": 5}
                cid = f"race-slot-{idx}"
                barrier.wait()
                dec = reservations.reserve_read(intent, "child-race", cid, "read_file", args)
                if dec == "reserved":
                    _out, err = adapters.dispatch_bounded_prerequisite(
                        intent, "child-race", cid, "read_file", args, task_id="race-task"
                    )
                    assert err is None
                env.system.observe_tool_call(
                    turn_id="t-race-1",
                    session_id="child-race",
                    tool_name="read_file",
                    args=args,
                    tool_call_id=cid,
                    status="ok" if dec == "reserved" else "blocked",
                    result="ok",
                )
                return dec

            with ThreadPoolExecutor(max_workers=2) as pool:
                futs = [pool.submit(copy_context().run, compete, i) for i in (0, 1)]
                decisions = sorted(f.result(timeout=10) for f in futs)
            assert decisions == ["exhausted", "reserved"]
            env.system.finish_turn(turn_id="t-race-1")
            handoff = dc._ResumeIntent(intent)

        # Continuation preserves exhausted budget without renewal.
        env.system.configure(prerequisite_max_operations=64, prerequisite_max_seconds=300)
        with child_profile_scope(dummy_child), dc.native_task_intent(**scope, resumed=True, handoff=handoff) as resumed:
            assert resumed is intent
            env.system.start_turn(
                user_message="Continue.",
                session_id="child-race",
                parent_session_id="parent-race",
                turn_id="t-race-2",
                task_intent=resumed,
            )
            assert reservations.reserve_read(
                resumed, "child-race", "after-resume", "read_file", {"path": str(agents_doc)}
            ) == "exhausted"
            # Termination permanently invalidates assignment; subsequent resume fails closed.
            child_stub = SimpleNamespace(_delegate_running_intent=resumed, _interrupt_requested=False)
            _signal_child_stop(child_stub)
            assert reservations.reserve_read(
                resumed, "child-race", "after-stop", "read_file", {"path": str(agents_doc)}
            ) == "closed"
        with child_profile_scope(dummy_child), dc.native_task_intent(**scope, resumed=True, handoff=handoff) as revived:
            assert revived is None

    # 3. Resource replacement & in-flight revocation with physical handle measurement!
    with _active_native_proposal_turn(env, parent_session_id="parent-rev", child_task_id="rev-task"):
        scope_rev = dict(
            actor_session_id="child-rev",
            parent_session_id="parent-rev",
            goal=GOAL,
            context=CONTEXT,
            child_task_id="rev-task",
        )
        dummy_child = SimpleNamespace(_delegate_profile_home=str(env.child_home))
        with child_profile_scope(dummy_child), dc.native_task_intent(**scope_rev) as intent_rev:
            assert reservations.start_preparation(intent_rev, "child-rev", 4, 120)
            args_safety = {"path": str(safety_doc), "offset": 1, "limit": 10}
            assert reservations.reserve_read(intent_rev, "child-rev", "rep-1", "read_file", args_safety) == "reserved"

            # Replace file with a different inode between reservation and dispatch -> 0 replacement bytes!
            safety_backup = env.tmp_path / "safety-orig-inode.md"
            swapped = env.tmp_path / "replacement-safety.md"
            swapped.write_bytes(b"REPLACEMENT_SECRET_BYTES\n")
            os.replace(safety_doc, safety_backup)
            os.replace(swapped, safety_doc)
            try:
                with reader.measure_physical_reads() as m_rep:
                    raw_rep, err_rep = adapters.dispatch_bounded_prerequisite(
                        intent_rev, "child-rev", "rep-1", "read_file", args_safety, task_id="rev-task"
                    )
                assert err_rep == "prerequisite_identity_mismatch"
                assert m_rep.bytes_read == 0
                assert "REPLACEMENT_SECRET_BYTES" not in raw_rep
            finally:
                os.replace(safety_backup, safety_doc)

            # In-flight revocation while handle is open -> honest revoked_in_flight, 0 leaked handles!
            args_agents = {"path": str(agents_doc), "offset": 1, "limit": 10}
            assert reservations.reserve_read(intent_rev, "child-rev", "inflight-1", "read_file", args_agents) == "reserved"
            opened, release = Event(), Event()

            def _pause_open():
                opened.set()
                assert release.wait(10)

            with ThreadPoolExecutor(max_workers=2) as pool:
                fut = pool.submit(
                    copy_context().run,
                    lambda: adapters.dispatch_bounded_prerequisite(
                        intent_rev,
                        "child-rev",
                        "inflight-1",
                        "read_file",
                        args_agents,
                        task_id="rev-task",
                        on_after_open=_pause_open,
                    ),
                )
                try:
                    assert opened.wait(10)
                    dc.revoke_task_intent(intent_rev)
                finally:
                    release.set()
                raw_inf, err_inf = fut.result(timeout=10)

            assert reader.active_handle_count() == 0
            assert err_inf == "prerequisite_revoked_in_flight"
            parsed_inf = json.loads(raw_inf)
            assert parsed_inf["disposition"] == "revoked_in_flight"
            assert parsed_inf["in_flight"] is True
