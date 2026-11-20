"""Bounded frozen-buffer adapters for skill_view and admitted OpenSpec reads (D3a).

All admitted prerequisite adapters materialize declared catalog pins exclusively
via ``reader.capture_descriptor_buffers``, reuse the assignment's frozen-buffer
cache without rescanning disk, never invoke subprocesses or external CLIs,
reject undeclared dependencies, redact sensitive strings, and enforce the
``MAX_OUTPUT_BYTES`` (256 KiB) returned-output ceiling.
"""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from agent.delegation_context import TaskIntent
from agent.redact import _is_secret_file_arg, redact_sensitive_text

from .catalog import MAX_OUTPUT_BYTES, ProposalSelection
from . import reader

SUPPORTED_PREREQUISITE_TOOLS = frozenset({
    "read_file",
    "skill_view",
    "openspec_status",
    "openspec_instructions",
    "openspec_spec_show",
    "openspec_spec_list",
})

_SUPPORT_GROUPS = ("references", "templates", "assets", "scripts")
_REQ_RE = re.compile(r"^###\s+Requirement:\s*(.+)$")
_SCN_RE = re.compile(r"^####\s+Scenario:\s*(.+)$")
_STEP_RE = re.compile(r"^[-*]\s+\*\*(\w+)\*\*\s*(.+)$")


def _decode_redacted_buffer(raw: bytes, path: str) -> str:
    text = raw.decode("utf-8", errors="replace").removeprefix("\ufeff")
    return redact_sensitive_text(text, file_read=True, secret_file=_is_secret_file_arg(path))


def _redact_nested(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value, file_read=True, secret_file=False)
    if isinstance(value, list):
        return [_redact_nested(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _redact_nested(v) for k, v in value.items()}
    return value


def _parse_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [part.strip().strip("\"'") for part in text.split(",") if part.strip()]


def _declared_skill_links(selection: ProposalSelection, skill_name: str) -> dict[str, list[str]] | None:
    prefix = f"reference/{skill_name}/"
    groups: dict[str, list[str]] = {}
    for pin in selection.resources:
        if not pin.key.startswith(prefix):
            continue
        rel = pin.key.removeprefix(prefix)
        top = rel.split("/", 1)[0] if "/" in rel else "other"
        bucket = top if top in _SUPPORT_GROUPS else "other"
        groups.setdefault(bucket, []).append(rel)
    ordered = {k: sorted(groups[k]) for k in (*_SUPPORT_GROUPS, "other") if k in groups}
    return ordered or None


def _template_vars_enabled() -> bool:
    try:
        from agent.skill_preprocessing import load_skills_config

        return bool(load_skills_config().get("template_vars", True))
    except (ImportError, OSError, ValueError):
        return True


def _apply_safe_skill_templates(content: str, skill_dir: Path, actor: str) -> str:
    if not _template_vars_enabled():
        return content
    from agent.skill_preprocessing import substitute_template_vars

    return substitute_template_vars(content, skill_dir, actor)


def _check_skill_policy(skill_name: str, frontmatter: dict[str, Any]) -> None:
    declared_deps = frontmatter.get("deps")
    if (isinstance(declared_deps, str) and declared_deps.strip()) or (
        isinstance(declared_deps, list) and any(str(x).strip() for x in declared_deps)
    ):
        raise reader.BoundedReadError(
            "undeclared_dependency",
            "Skill declares external runtime dependencies ineligible for prerequisite capture.",
        )
    try:
        from tools import skills_tool

        if skills_tool._is_skill_disabled(skill_name, str(frontmatter.get("name") or skill_name)):
            raise reader.BoundedReadError("denied_skill", "Skill is disabled in active profile policy.")
        if not skills_tool.skill_matches_platform(frontmatter):
            raise reader.BoundedReadError("unsupported_platform", "Skill is unsupported on active platform.")
    except reader.BoundedReadError:
        raise
    except (ImportError, OSError, ValueError) as exc:
        raise reader.BoundedReadError(
            "unsupported_backend",
            "Skill policy verification modules are unavailable.",
        ) from exc


def _serialize_capped_payload(payload: dict[str, Any], *, trim_path: tuple[str, ...] | None = None) -> str:
    output_cap = min(MAX_OUTPUT_BYTES, reader._effective_output_byte_ceiling())
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode()) <= output_cap:
        return serialized
    if trim_path is None:
        raise reader.BoundedReadError("oversize_output", "Generated adapter output exceeds output byte ceiling.")

    target_parent: Any = payload
    for step in trim_path[:-1]:
        target_parent = target_parent[step]
    leaf = trim_path[-1]
    original = str(target_parent.get(leaf) or "")
    budget = max(64, output_cap - 512)
    clipped = original.encode()[:budget].decode("utf-8", errors="ignore")
    payload["truncated"] = True
    target_parent[leaf] = clipped
    serialized = json.dumps(payload, ensure_ascii=False)
    while len(serialized.encode()) > output_cap and len(clipped) > 32:
        clipped = clipped[: max(32, int(len(clipped) * 0.85))]
        target_parent[leaf] = clipped
        serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode()) > output_cap:
        raise reader.BoundedReadError("oversize_output", "Generated adapter output exceeds output byte ceiling.")
    return serialized


def _render_skill_view(
    selection: ProposalSelection,
    actor: str,
    params: dict[str, Any],
    captured: reader.CapturedPrerequisites,
) -> str:
    from agent.skill_utils import parse_frontmatter

    name = str(params["name"])
    file_path = params.get("file_path")
    main_pin = captured.descriptor.resources[0]
    raw_main = _decode_redacted_buffer(captured.buffers[main_pin.key], main_pin.path)
    try:
        frontmatter, _body = parse_frontmatter(raw_main)
    except (TypeError, ValueError):
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    _check_skill_policy(name, frontmatter)
    resolved_name = str(frontmatter.get("name") or name)[:64]

    if isinstance(file_path, str):
        ref_pin = captured.descriptor.resources[1]
        ref_text = _decode_redacted_buffer(captured.buffers[ref_pin.key], ref_pin.path)
        payload: dict[str, Any] = {
            "success": True,
            "name": resolved_name,
            "file": file_path,
            "content": ref_text,
            "file_type": PurePosixPath(file_path).suffix,
            "truncated": False,
        }
        return _serialize_capped_payload(payload, trim_path=("content",))

    skill_dir = Path(main_pin.path).parent
    rendered = _apply_safe_skill_templates(raw_main, skill_dir, actor)
    redacted_rendered = redact_sensitive_text(rendered, file_read=True, secret_file=False)
    meta = frontmatter.get("metadata")
    hermes_meta = (meta.get("hermes") or {}) if isinstance(meta, dict) and isinstance(meta.get("hermes"), dict) else {}
    tags = _parse_tags(hermes_meta.get("tags") or frontmatter.get("tags", ""))
    related = _parse_tags(hermes_meta.get("related_skills") or frontmatter.get("related_skills", ""))
    links = _declared_skill_links(selection, name)
    payload = {
        "success": True,
        "name": resolved_name,
        "description": redact_sensitive_text(str(frontmatter.get("description") or "")[:1024], file_read=True),
        "tags": tags,
        "related_skills": related,
        "content": redacted_rendered,
        "path": f"{name}/SKILL.md",
        "skill_dir": str(skill_dir),
        "linked_files": links,
        "readiness_status": "available",
        "setup_needed": False,
        "truncated": False,
    }
    return _serialize_capped_payload(payload, trim_path=("content",))


def _parse_json_or_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return ""
    try:
        return json.loads(stripped)
    except ValueError:
        return stripped


def _trim_largest_string_field(stdout_obj: dict[str, Any]) -> tuple[str, ...] | None:
    best_key: str | None = None
    best_len = 0
    for key, val in stdout_obj.items():
        if isinstance(val, str) and len(val) > best_len:
            best_key = key
            best_len = len(val)
    return ("stdout", best_key) if best_key is not None else None


def _render_openspec_snapshot(
    tool: str,
    selection: ProposalSelection,
    params: dict[str, Any],
    captured: reader.CapturedPrerequisites,
) -> str:
    pin = captured.descriptor.resources[0]
    raw_text = _decode_redacted_buffer(captured.buffers[pin.key], pin.path)
    parsed = _redact_nested(_parse_json_or_text(raw_text))
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        raise reader.BoundedReadError(
            "unavailable_snapshot",
            "Pinned OpenSpec snapshot reports an unready state; CLI/template fallback is prohibited.",
        )
    stdout_val = parsed.get("stdout") if isinstance(parsed, dict) and "stdout" in parsed else parsed
    payload: dict[str, Any] = {
        "ok": True,
        "exit_code": 0,
        "workdir": selection.planning_home,
        "change": selection.change,
        "schema": str(params.get("schema", "spec-driven")),
        "stdout": stdout_val,
        "stderr": "",
        "truncated": False,
    }
    if tool == "openspec_instructions":
        payload["artifact"] = str(params["artifact"])
    trim_path: tuple[str, ...] | None = ("stdout",) if isinstance(stdout_val, str) else None
    if trim_path is None and isinstance(stdout_val, dict):
        trim_path = _trim_largest_string_field(stdout_val)
    return _serialize_capped_payload(payload, trim_path=trim_path)


def _consume_spec_line(
    trim: str,
    state: str,
    title: str,
    purpose: str,
    reqs: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if trim.startswith("# ") and not trim.startswith("## "):
        return "top", trim[2:].strip().removesuffix("Specification").strip(), purpose
    if trim.lower().startswith("## purpose"):
        return "purpose", title, purpose
    if trim.lower().startswith("## requirements"):
        return "top", title, purpose
    if m_req := _REQ_RE.match(trim):
        reqs.append({"name": m_req.group(1).strip(), "description": "", "scenarios": []})
        return "req", title, purpose
    if m_scn := _SCN_RE.match(trim):
        if reqs:
            reqs[-1]["scenarios"].append({"name": m_scn.group(1).strip(), "steps": []})
        return "scenario", title, purpose
    if not trim:
        return state, title, purpose
    if state == "purpose":
        return state, title, (f"{purpose}\n{trim}" if purpose else trim)
    if state == "req" and reqs:
        prev = reqs[-1]["description"]
        reqs[-1]["description"] = f"{prev}\n{trim}" if prev else trim
    elif state == "scenario" and reqs and reqs[-1]["scenarios"]:
        scn = reqs[-1]["scenarios"][-1]
        if step := _STEP_RE.match(trim):
            scn["steps"].append({"type": step.group(1).upper(), "text": step.group(2).strip()})
        elif scn["steps"]:
            scn["steps"][-1]["text"] += f" {trim}"
    return state, title, purpose


def parse_spec_markdown(md: str) -> dict[str, Any]:
    title = ""
    purpose = ""
    reqs: list[dict[str, Any]] = []
    state = "top"
    for line in md.split("\n"):
        state, title, purpose = _consume_spec_line(line.strip(), state, title, purpose, reqs)
    return {"title": title, "purpose": purpose, "requirements": reqs}


def _render_openspec_spec_show(
    selection: ProposalSelection,
    params: dict[str, Any],
    captured: reader.CapturedPrerequisites,
) -> str:
    spec_name = str(params["spec"])
    pin = captured.descriptor.resources[0]
    md = _decode_redacted_buffer(captured.buffers[pin.key], pin.path)
    parsed = parse_spec_markdown(md)
    payload: dict[str, Any] = {
        "ok": True,
        "spec": spec_name,
        "change": None,
        "path": f"openspec/specs/{spec_name}/spec.md",
        "title": parsed["title"],
        "purpose": parsed["purpose"],
        "requirements": parsed["requirements"],
        "workdir": selection.planning_home,
        "truncated": False,
    }
    return _serialize_capped_payload(payload, trim_path=("purpose",))


def _render_openspec_spec_list(
    selection: ProposalSelection,
    captured: reader.CapturedPrerequisites,
) -> str:
    names = [pin.key.removeprefix("baseline/") for pin in captured.descriptor.resources]
    payload: dict[str, Any] = {
        "ok": True,
        "change": None,
        "specs": names,
        "count": len(names),
        "workdir": selection.planning_home,
    }
    return _serialize_capped_payload(payload)


def dispatch_bounded_prerequisite(
    intent: TaskIntent,
    actor: str,
    call_id: str,
    tool: str,
    args: dict,
    *,
    task_id: str | None = None,
    on_before_open: Callable[[], None] | None = None,
    on_after_open: Callable[[], None] | None = None,
) -> tuple[str, str | None]:
    """Dispatch any admitted prerequisite operation through verified frozen buffers."""
    if tool == "read_file":
        return reader.dispatch_bounded_read_file(
            intent,
            actor,
            call_id,
            args,
            task_id=task_id,
            on_before_open=on_before_open,
            on_after_open=on_after_open,
        )
    if tool not in SUPPORTED_PREREQUISITE_TOOLS:
        body = {"error": "Prerequisite verified reader unavailable; no read dispatched."}
        return json.dumps(body, ensure_ascii=False), "prerequisite_reader_unavailable"

    selection = intent.proposal_selection
    if selection is None:
        body = {"error": "Prerequisite selection unavailable; no read dispatched.", "disposition": "unavailable"}
        return json.dumps(body, ensure_ascii=False), "prerequisite_unavailable"

    try:
        captured = reader.capture_descriptor_buffers(
            intent,
            actor,
            call_id,
            tool,
            args,
            task_id=task_id,
            on_before_open=on_before_open,
            on_after_open=on_after_open,
        )
        params = dict(captured.descriptor.parameters)
        if tool == "skill_view":
            return _render_skill_view(selection, actor, params, captured), None
        if tool in {"openspec_status", "openspec_instructions"}:
            return _render_openspec_snapshot(tool, selection, params, captured), None
        if tool == "openspec_spec_show":
            return _render_openspec_spec_show(selection, params, captured), None
        return _render_openspec_spec_list(selection, captured), None
    except reader.BoundedReadError as exc:
        body = {
            "error": f"Prerequisite bounded adapter rejected ({exc.code}).",
            "disposition": exc.code,
            "in_flight": bool(exc.in_flight),
        }
        return json.dumps(body, ensure_ascii=False), f"prerequisite_{exc.code}"
