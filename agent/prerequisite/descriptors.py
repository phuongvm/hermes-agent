"""Resolve metadata for declared reads; never dispatch or approve a call."""
from __future__ import annotations

from dataclasses import dataclass

from agent.delegation_context import TaskIntent, current_task_intent

from .catalog import ARTIFACTS, BASELINES, InvalidCatalog, ProposalSelection, ResourcePin, _local_path, verify_catalog_metadata


@dataclass(frozen=True, slots=True)
class ReadDescriptor:
    tool: str
    parameters: tuple[tuple[str, str | int], ...]
    resources: tuple[ResourcePin, ...]


def _file(selection: ProposalSelection, args: dict) -> ReadDescriptor | None:
    if args.keys() - {"path", "offset", "limit"} or type(args.get("path")) is not str:
        return None
    path = _local_path(args["path"])
    offset, limit = args.get("offset", 1), args.get("limit", 2000)
    if type(offset) is not int or type(limit) is not int or not 1 <= offset <= 1048577 or not 1 <= limit <= 2000:
        return None
    pin = next((pin for pin in selection.resources if _local_path(pin.path).parts == path.parts), None)
    if pin is None:
        return None
    return ReadDescriptor("read_file", (("path", pin.path), ("offset", offset), ("limit", limit)), (pin,))


def _skill(args: dict, pins: dict[str, ResourcePin]) -> ReadDescriptor | None:
    if args.keys() - {"name", "file_path"} or type(args.get("name")) is not str:
        return None
    name, ref = args["name"], args.get("file_path")
    main = pins.get(f"skill/{name}")
    if main is None:
        return None
    params = (("name", name),)
    if ref is None:
        return ReadDescriptor("skill_view", params, (main,))
    support = pins.get(f"reference/{name}/{ref}") if type(ref) is str else None
    if support is None:
        return None
    return ReadDescriptor("skill_view", (*params, ("file_path", ref)), (main, support))


_OPENSPEC_FIELDS = {
    "openspec_status": {"workdir", "change", "schema"},
    "openspec_instructions": {"workdir", "change", "schema", "artifact"},
    "openspec_spec_show": {"workdir", "spec"},
    "openspec_spec_list": {"workdir"},
}


def _openspec(tool: str, selection: ProposalSelection, args: dict,
              pins: dict[str, ResourcePin]) -> ReadDescriptor | None:
    if (args.keys() - _OPENSPEC_FIELDS[tool] or any(type(value) is not str for value in args.values())
            or args.get("workdir") != selection.planning_home):
        return None
    params = dict(args)
    if tool == "openspec_spec_list":
        keys = tuple(f"baseline/{name}" for name in BASELINES)
    elif tool == "openspec_spec_show":
        if args.get("spec") not in BASELINES:
            return None
        keys = (f"baseline/{args['spec']}",)
    else:
        schema = args.get("schema", "spec-driven")
        if args.get("change") != selection.change or schema != "spec-driven":
            return None
        params["schema"] = schema
        if tool == "openspec_instructions":
            if args.get("artifact") not in ARTIFACTS:
                return None
            keys = (f"snapshot/instructions/{args['artifact']}",)
        else:
            keys = ("snapshot/status",)
    if any(key not in pins for key in keys):
        return None
    return ReadDescriptor(tool, tuple(sorted(params.items())), tuple(pins[key] for key in keys))


def match_operation(intent: TaskIntent | None, actor: str, tool: str, args: dict) -> ReadDescriptor | None:
    """Match an active assignment's frozen catalog pins and parameters without filesystem I/O."""
    if type(intent) is not TaskIntent or type(tool) is not str or type(args) is not dict:
        return None
    if tool not in _OPENSPEC_FIELDS and tool not in {"read_file", "skill_view"}:
        return None
    if current_task_intent(actor, intent.parent_session_id) is not intent:
        return None
    selection = intent.proposal_selection
    if selection is None:
        return None
    try:
        if tool == "read_file":
            return _file(selection, args)
        pins = {pin.key: pin for pin in selection.resources}
        return _skill(args, pins) if tool == "skill_view" else _openspec(tool, selection, args, pins)
    except InvalidCatalog:
        return None


def resolve_operation(intent: TaskIntent | None, actor: str, tool: str, args: dict) -> ReadDescriptor | None:
    """Match only an active native assignment's catalog, with no content reads.

    The result is neither a reservation nor an authorization. Dispatch still
    needs to verify adapter support, current authority and content integrity.
    """
    result = match_operation(intent, actor, tool, args)
    if result is None or intent is None or intent.proposal_selection is None:
        return None
    try:
        verify_catalog_metadata(intent.proposal_selection)
        return result
    except InvalidCatalog:
        return None
