"""Validate and freeze the operator's proposal catalog without opening resources.

A catalog entry is a declaration, not a tool grant. Identity and digest must
still be verified from a bounded handle at the guarded dispatch seam.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from agent.file_safety import is_blocked_device_path, is_nt_namespace_path
from hermes_platform.host.local_volume import is_fixed_local_volume
from typing import Any

MAX_RESOURCES = 128
MAX_FILE_BYTES = 1 << 20
MAX_SOURCE_BYTES = 16 << 20
MAX_OUTPUT_BYTES = 256 << 10

GOVERNANCE = (
    "AGENTS.md",
    "shared/startup-rules.md",
    "shared/safety.md",
    "skills/task-verification-mandate.md",
    "shared/rules-index.md",
    "user/communication-language.md",
    "shared/lessons-digest.md",
    "skills/anti-deception-instruction.md",
    "skills/compliance-enforcer.md",
)
REQUIRED_SKILLS = frozenset({
    "verification-before-claim", "openspec-propose", "openspec-workflow-governance",
    "codebase-design", "deep-reference-protocol", "ai-agent-collaboration", "hermes-agent",
})
BASELINES = ("nerve-crew-dod-and-role-handoff", "nerve-reflex-resilience")
ARTIFACTS = ("proposal", "design", "specs", "tasks")
SNAPSHOTS = frozenset({"snapshot/status", *(f"snapshot/instructions/{artifact}" for artifact in ARTIFACTS)})
WORKFLOW = "openspec-propose"
_CHANGE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REQUIRED_REFERENCES = frozenset({
    "reference/verification-before-claim/references/completion-gate.md",
    "reference/openspec-workflow-governance/references/openspec-agent-workflow-guide.md",
})
PROJECT_DOCUMENTS = {"project/agent-share": "openspec/workspace/sessions/agent_share.md"}
OPTIONAL_DOCUMENTS = {"project/requirements": "REQUIREMENTS.md", "project/architecture": "ARCHITECTURE.md"}
_REQUIRED = frozenset({
    *(f"governance/{p}" for p in GOVERNANCE), *(f"skill/{s}" for s in REQUIRED_SKILLS),
    *REQUIRED_REFERENCES, *SNAPSHOTS, *PROJECT_DOCUMENTS, "project/exploration", "project/receipt",
    *(f"baseline/{b}" for b in BASELINES),
})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PART = re.compile(r"[a-zA-Z0-9_.-]+")


class InvalidCatalog(ValueError):
    """A nonsecret catalog diagnostic, intentionally without paths or raw values."""


@dataclass(frozen=True, slots=True)
class ResourcePin:
    key: str
    path: str
    device: int
    inode: int
    size: int
    max_file_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProposalSelection:
    change: str
    planning_home: str
    resources: tuple[ResourcePin, ...]
    absent_optional: tuple[str, ...]
    operations: int
    seconds: int

    def intersect(self, child: ProposalSelection) -> ProposalSelection | None:
        if (self.change, self.planning_home) != (child.change, child.planning_home):
            return None
        shared = frozenset(self.resources).intersection(child.resources)
        if not ({item.key for item in shared} >= _REQUIRED):
            return None
        # Explicit dependencies cannot silently disappear at the profile boundary.
        strict = ("reference/", "snapshot/")
        if ({item for item in self.resources if item.key.startswith(strict)}
                != {item for item in child.resources if item.key.startswith(strict)}):
            return None
        return ProposalSelection(
            self.change, self.planning_home, tuple(item for item in self.resources if item in shared),
            tuple(sorted(set(self.absent_optional) | set(child.absent_optional) | {
                item.key for item in (*self.resources, *child.resources)
                if item.key in OPTIONAL_DOCUMENTS and item not in shared
            })), min(self.operations, child.operations), min(self.seconds, child.seconds),
        )


def _integer(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise InvalidCatalog("invalid-limit")
    return value


def _local_path(value: Any, base: Path | None = None) -> Path:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(ord(c) < 32 for c in value):
        raise InvalidCatalog("invalid-path")
    if is_nt_namespace_path(value) or value.replace("\\", "/").startswith("//"):
        raise InvalidCatalog("unsupported-path")
    windows_path = PureWindowsPath(value)
    if os.name == "nt" and any(os.path.isreserved(part) for part in windows_path.parts if part != windows_path.anchor):
        raise InvalidCatalog("unsupported-path")
    path = Path(value)
    if (value.startswith(("//", "\\\\")) or ".." in path.parts or path.drive.startswith(("//", "\\\\"))
            or any(":" in part or "*" in part or "?" in part for part in path.parts if part != path.anchor)):
        raise InvalidCatalog("unsupported-path")
    if base is None:
        if not path.is_absolute():
            raise InvalidCatalog("absolute-path-required")
        return path
    if path.anchor:
        raise InvalidCatalog("relative-project-path-required")
    return base / path


def _resource(key: str, row: Any, home: Path, change: str) -> ResourcePin:
    if not isinstance(row, dict) or set(row) != {"path", "device", "inode", "size", "max_file_bytes", "sha256"}:
        raise InvalidCatalog("invalid-resource")
    absolute = _local_path(row["path"], home if key.startswith(("project/", "baseline/", "snapshot/")) else None)
    expected = PROJECT_DOCUMENTS.get(key, OPTIONAL_DOCUMENTS.get(key))
    if key.startswith("baseline/"):
        expected = f"openspec/specs/{key.removeprefix('baseline/')}/spec.md"
    if key in SNAPSHOTS:
        expected = f"openspec/workspace/prerequisites/{change}/{key.removeprefix('snapshot/')}.json"
    if expected is not None and absolute != home / expected:
        raise InvalidCatalog("unexpected-project-resource")
    if key in {"project/exploration", "project/receipt"}:
        if absolute.suffix.lower() != ".md" or not absolute.is_relative_to(home / "openspec/workspace/explorations"):
            raise InvalidCatalog("invalid-exploration-selection")
    if key.startswith("reference/"):
        if absolute.suffix.lower() not in {".md", ".txt"}:
            raise InvalidCatalog("unsupported-reference-format")
    digest = row["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise InvalidCatalog("invalid-digest")
    cap = _integer(row["max_file_bytes"], 1, MAX_FILE_BYTES)
    return ResourcePin(key, str(absolute), _integer(row["device"], 0, (1 << 128) - 1),
                       _integer(row["inode"], 1, (1 << 128) - 1), _integer(row["size"], 0, cap), cap, digest)


def valid_change_id(change: object) -> bool:
    return isinstance(change, str) and len(change) <= 128 and _CHANGE.fullmatch(change) is not None


def freeze_proposal_catalog(change: str, entry: Any) -> ProposalSelection:
    if not valid_change_id(change):
        raise InvalidCatalog("invalid-change-id")
    if not isinstance(entry, dict) or set(entry) - {"planning_home", "resources", "operations", "seconds"}:
        raise InvalidCatalog("invalid-entry")
    home = _local_path(entry.get("planning_home"))
    rows = entry.get("resources")
    if not isinstance(rows, dict) or not (_REQUIRED | OPTIONAL_DOCUMENTS.keys()) <= rows.keys() or len(rows) > MAX_RESOURCES:
        raise InvalidCatalog("incomplete-resource-set")
    pins = []
    absent = []
    for key, row in rows.items():
        if key in OPTIONAL_DOCUMENTS and row is None:
            absent.append(key)
            continue
        if key not in _REQUIRED and key not in OPTIONAL_DOCUMENTS and key not in SNAPSHOTS:
            if not isinstance(key, str) or len(key) > 256:
                raise InvalidCatalog("invalid-resource-key")
            kind, _, remainder = key.partition("/")
            skill, _, name = remainder.partition("/")
            if kind != "reference" or skill not in REQUIRED_SKILLS or not name or any(
                part in (".", "..") or not _PART.fullmatch(part) for part in name.split("/")
            ):
                raise InvalidCatalog("unsupported-resource")
        pins.append(_resource(key, row, home, change))
    by_key = {pin.key: pin for pin in pins}
    governance_root = Path(by_key["governance/AGENTS.md"].path).parent
    for pin in pins:
        path = Path(pin.path)
        if pin.key.startswith("governance/") and path != governance_root / pin.key.removeprefix("governance/"):
            raise InvalidCatalog("unexpected-governance-resource")
        if pin.key.startswith("skill/") and (path.parent.name, path.name) != (pin.key.removeprefix("skill/"), "SKILL.md"):
            raise InvalidCatalog("unexpected-skill-resource")
        if pin.key.startswith("reference/"):
            _, skill, name = pin.key.split("/", 2)
            if Path(pin.path) != Path(by_key[f"skill/{skill}"].path).parent / name:
                raise InvalidCatalog("reference-outside-pinned-skill")
    if len({(pin.device, pin.inode) for pin in pins}) != len(pins):
        raise InvalidCatalog("duplicate-resource-identity")
    if len({Path(pin.path) for pin in pins}) != len(pins) or sum(pin.max_file_bytes for pin in pins) > MAX_SOURCE_BYTES:
        raise InvalidCatalog("ambiguous-resource-or-byte-budget")
    return ProposalSelection(change, str(home), tuple(sorted(pins, key=lambda item: item.key)), tuple(sorted(absent)),
                             _integer(entry.get("operations", 64), 1, 128),
                             _integer(entry.get("seconds", 600), 1, 900))


def verify_catalog_metadata(selection: ProposalSelection) -> None:
    """Reject missing or stale identities without reading content. Dispatch must recheck."""
    checked_dirs: set[Path] = set()
    checked_volumes: set[str] = set()

    def inspect(path: Path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InvalidCatalog("unsupported-resource-link")
        return info

    try:
        for pin in selection.resources:
            path = _local_path(pin.path)
            if is_blocked_device_path(str(path)):
                raise InvalidCatalog("unsupported-path")
            if path.anchor not in checked_volumes:
                if not is_fixed_local_volume(path.anchor):
                    raise InvalidCatalog("nonlocal-volume")
                checked_volumes.add(path.anchor)
            for parent in reversed(path.parents):
                if parent not in checked_dirs:
                    if not stat.S_ISDIR(inspect(parent).st_mode):
                        raise InvalidCatalog("invalid-resource-parent")
                    checked_dirs.add(parent)
            info = inspect(path)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size) != (
                pin.device, pin.inode, pin.size,
            ):
                raise InvalidCatalog("stale-resource-metadata")
    except OSError as exc:
        raise InvalidCatalog("resource-metadata-unavailable") from exc


def proposal_entries(catalog: object) -> dict:
    if not isinstance(catalog, dict) or set(catalog) != {WORKFLOW}:
        raise InvalidCatalog("invalid-workflow-map")
    entries = catalog[WORKFLOW]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 128:
        raise InvalidCatalog("invalid-change-map")
    return entries


def catalog_diagnostics(catalog: object) -> list[str]:
    """Check declaration shape and pins without opening or trusting resource contents."""
    if catalog == {}:
        return []
    try:
        entries = proposal_entries(catalog)
    except InvalidCatalog as exc:
        return [str(exc)]
    errors = []
    for change, entry in entries.items():
        try:
            freeze_proposal_catalog(change, entry)
        except InvalidCatalog as exc:
            errors.append(str(exc))
    return errors
