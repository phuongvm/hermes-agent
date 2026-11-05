"""Fail-closed staged Layer 0 gate for pending skill writes.

A pending skill write (``create`` / ``patch`` / ``edit`` / ``write_file`` / ``remove_file`` / ``batch``) is
materialised in a throw-away ``tempfile.TemporaryDirectory`` and judged by the deterministic
``evaluate_skill_layer0`` gate BEFORE any live skill directory or pending queue file is touched.

Any staging, import, timeout or malformed-verdict problem is treated exactly like a failed verdict:
``BLOCKED_LAYER0_EVAL`` / ``REVISE`` / ``NOT_ATTEMPTED`` with ``replayed=False`` and ``discarded=False``.

Public seam: :func:`evaluate_pending_skill_layer0_staged`. The module has no dependency on ``tools.write_approval``
so ``evaluate_pending_manifest.py`` can load and share it unchanged.
"""

from __future__ import annotations

import dataclasses
import json
import os
import posixpath
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

LAYER0_TIMEOUT_S = 5.0
LAYER0_GATE_PATH = Path("O:/workspaces/ai_agents/hermes-agent/scripts/skill_eval/hermes_skill_eval_gate.py")
LAYER0_GATE_ENV = "HERMES_LAYER0_GATE_PATH"
_STAGE_PREFIX = "hermes-layer0-stage-"
_GUARD_VERDICTS = frozenset({"safe", "caution", "dangerous", "unknown"})
_SHADOW_STATUSES = frozenset({"active", "shadowed_identical", "shadowed_divergent", "ambiguous"})
_NERVE_RECALL_MIN = 0.80
_RUNNER_MARKER = "LAYER0_JSON:"
_RUNNER = (
    "import sys, json, dataclasses, importlib.util\n"
    "sys.dont_write_bytecode = True\n"
    "gate_path, skill_dir, hermes_home = sys.argv[1:4]\n"
    "spec = importlib.util.spec_from_file_location('hermes_skill_eval_gate', gate_path)\n"
    "mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)\n"
    "from pathlib import Path\n"
    "v = mod.evaluate_skill_layer0(Path(skill_dir), None, Path(hermes_home))\n"
    f"print('{_RUNNER_MARKER}' + json.dumps(dataclasses.asdict(v)))\n"
)

Layer0Eval = Callable[[Path], Any]


def gate_path() -> Path:
    return Path(os.environ.get(LAYER0_GATE_ENV) or LAYER0_GATE_PATH)


def run_layer0_eval(skill_dir: Path, *, timeout_s: float = LAYER0_TIMEOUT_S, hermes_home: Optional[Path] = None) -> dict:
    """Run ``evaluate_skill_layer0(skill_dir)`` in an isolated interpreter (hard timeout, no sys.modules pollution)."""
    gate = gate_path()
    if not gate.is_file():
        raise ImportError(f"Layer 0 gate not found: {gate}")
    home = hermes_home or Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    python = os.environ.get("HERMES_LAYER0_PYTHON") or sys.executable
    try:
        proc = subprocess.run(
            [python, "-c", _RUNNER, str(gate), str(skill_dir), str(home)],
            capture_output=True, text=True, timeout=timeout_s, env=env, cwd=str(Path(skill_dir).parent), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"evaluate_skill_layer0 exceeded {timeout_s}s") from exc
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(_RUNNER_MARKER):
            return json.loads(line[len(_RUNNER_MARKER):])
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
    raise ImportError(f"Layer 0 gate failed to execute (rc={proc.returncode}): {tail[0][:300]}")


# ------------------------------------------------------------------------------------------ verdict contract
def coerce_verdict(obj: Any) -> dict:
    """Return the verdict as a plain dict, or raise ``TypeError`` / ``ValueError`` when it is malformed."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        data = dataclasses.asdict(obj)
    elif isinstance(obj, Mapping):
        data = dict(obj)
    else:
        raise TypeError(f"Layer 0 verdict must be a Layer0Verdict or mapping, got {type(obj).__name__}")

    def need(key: str, kind: tuple, *, allow_bool: bool = False) -> Any:
        if key not in data:
            raise ValueError(f"Layer 0 verdict missing '{key}'")
        value = data[key]
        if isinstance(value, bool) and not allow_bool:
            raise ValueError(f"Layer 0 verdict '{key}' must not be a bool")
        if not isinstance(value, kind):
            raise ValueError(f"Layer 0 verdict '{key}' has invalid type {type(value).__name__}")
        return value

    need("passed", (bool,), allow_bool=True)
    need("front_loaded_trigger_ok", (bool,), allow_bool=True)
    need("effective_desc_length", (int,))
    need("max_desc_budget", (int,))
    errors = need("linter_errors", (list, tuple))
    if not all(isinstance(e, str) for e in errors):
        raise ValueError("Layer 0 verdict 'linter_errors' must contain strings")
    if need("guard_verdict", (str,)) not in _GUARD_VERDICTS:
        raise ValueError(f"Layer 0 verdict 'guard_verdict' invalid: {data['guard_verdict']!r}")
    if need("shadow_status", (str,)) not in _SHADOW_STATUSES:
        raise ValueError(f"Layer 0 verdict 'shadow_status' invalid: {data['shadow_status']!r}")
    recall = need("nerve_admission_recall", (int, float))
    if not 0.0 <= float(recall) <= 1.0:
        raise ValueError("Layer 0 verdict 'nerve_admission_recall' outside 0..1")
    if need("provider_calls", (int,)) != 0:
        raise ValueError("Layer 0 verdict 'provider_calls' must equal 0")
    data["linter_errors"] = list(errors)
    return data


def verdict_passes(verdict: dict) -> bool:
    """The verdict's own ``passed`` flag AND an independent re-check of every hard criterion (never trust a lone flag)."""
    return bool(
        verdict["passed"]
        and 0 < verdict["effective_desc_length"] <= verdict["max_desc_budget"]
        and verdict["front_loaded_trigger_ok"]
        and not verdict["linter_errors"]
        and verdict["guard_verdict"] != "dangerous"
        and verdict["shadow_status"] not in ("shadowed_divergent", "ambiguous")
        and float(verdict["nerve_admission_recall"]) >= _NERVE_RECALL_MIN
    )


# ------------------------------------------------------------------------------------------ staging
_FLAT_KEYS = ("action", "name", "content", "category", "file_path", "file_content", "old_string", "new_string",
              "replace_all", "absorbed_into")


def _operations(payload: Mapping[str, Any]) -> list[dict]:
    ops = payload.get("operations")
    default = payload.get("name")
    if isinstance(ops, list) and ops:
        out = []
        for i, op in enumerate(ops):
            if not isinstance(op, Mapping) or not op.get("action"):
                raise ValueError(f"operations[{i}] needs an 'action'")
            out.append({**op, "name": op.get("name") or default})
        return out
    if payload.get("action") in (None, "", "batch"):
        raise ValueError("pending payload has no action or operations")
    return [{k: payload.get(k) for k in _FLAT_KEYS}]


def _safe_member(skill_dir: Path, rel: Any) -> Path:
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("file_path is required")
    norm = posixpath.normpath(rel.replace("\\", "/").lstrip("/"))
    if norm.startswith("..") or norm in (".", "") or ":" in norm.split("/")[0]:
        raise ValueError(f"unsafe supporting file path: {rel!r}")
    target = (skill_dir / norm).resolve()
    if not target.is_relative_to(skill_dir.resolve()):
        raise ValueError(f"supporting file escapes the skill directory: {rel!r}")
    return target


def _replace(content: str, old: str, new: str, replace_all: bool) -> str:
    try:
        from tools.fuzzy_match import fuzzy_find_and_replace
    except Exception:  # pragma: no cover - exact fallback keeps staging usable without the helper
        fuzzy_find_and_replace = None
    if fuzzy_find_and_replace is not None:
        updated, _count, _strategy, error = fuzzy_find_and_replace(content, old, new, replace_all, correction_notes=[])
        if error:
            raise ValueError(f"patch does not apply: {str(error)[:200]}")
        return updated
    if old not in content or (not replace_all and content.count(old) != 1):
        raise ValueError("patch does not apply: old_string must match exactly once")
    return content.replace(old, new) if replace_all else content.replace(old, new, 1)


def _copy_skill(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, copy_function=shutil.copyfile,
                    ignore=shutil.ignore_patterns("__pycache__", ".scan-cache", "*.pyc"))


def _apply_op(op: dict, skill_dir: Path) -> None:
    action = op.get("action")
    if action == "create":
        content = op.get("content")
        if not content:
            raise ValueError("create requires content")
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8", newline="\n")
    elif action == "edit" or (action == "patch" and op.get("content")):
        if not op.get("content"):
            raise ValueError("edit requires content")
        (skill_dir / "SKILL.md").write_text(op["content"], encoding="utf-8", newline="\n")
    elif action == "patch":
        target = _safe_member(skill_dir, op["file_path"]) if op.get("file_path") else skill_dir / "SKILL.md"
        if not target.is_file():
            raise ValueError(f"patch target not found: {op.get('file_path') or 'SKILL.md'}")
        if not op.get("old_string") or op.get("new_string") is None:
            raise ValueError("patch requires old_string and new_string")
        text = target.read_text(encoding="utf-8-sig")
        target.write_text(_replace(text, op["old_string"], op["new_string"], bool(op.get("replace_all"))),
                          encoding="utf-8", newline="\n")
    elif action == "write_file":
        target = _safe_member(skill_dir, op.get("file_path"))
        if op.get("file_content") is None:
            raise ValueError("write_file requires file_content")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(op["file_content"], encoding="utf-8", newline="\n")
    elif action == "remove_file":
        target = _safe_member(skill_dir, op.get("file_path"))
        if not target.is_file():
            raise ValueError(f"remove_file target not found: {op.get('file_path')}")
        target.unlink()
    else:
        raise ValueError(f"unsupported pending skill action: {action!r}")


def _blocked(*, verdict: Optional[dict], error: Optional[str]) -> dict:
    return {
        "passed": False, "success": False, "governance_verdict": "REVISE", "native_disposition": "NOT_ATTEMPTED",
        "effective_status": "BLOCKED_LAYER0_EVAL", "replayed": False, "discarded": False,
        "layer0_verdict": verdict, "layer0_error": error,
    }


def evaluate_pending_skill_layer0_staged(
    record: Mapping[str, Any],
    canonical_match: Optional[Path] = None,
    *,
    timeout_s: float = LAYER0_TIMEOUT_S,
    resolve_canonical: Optional[Callable[[str], Sequence[Path]]] = None,
    run_eval: Optional[Layer0Eval] = None,
    hermes_home: Optional[Path] = None,
) -> dict:
    """Stage ``record``'s candidate skill state in a temp dir and run Layer 0 on it. Never raises, never mutates live state.

    ``canonical_match`` is the already-resolved live skill dir for the (single) target; other targets of a batch are
    resolved through ``resolve_canonical``. ``run_eval`` replaces the isolated-interpreter runner (unit tests).
    """
    try:
        payload = record.get("payload") if isinstance(record, Mapping) else None
        if not isinstance(payload, Mapping):
            raise ValueError("pending record has no payload")
        ops = _operations(payload)
        if all(op.get("action") == "delete" for op in ops):
            return {"passed": True, "layer0_verdict": None, "layer0_error": None, "layer0_skipped": "delete"}
        if any(op.get("action") == "delete" for op in ops):
            raise ValueError("delete cannot be mixed with other operations")
        evaluator = run_eval or (lambda d: run_layer0_eval(d, timeout_s=timeout_s, hermes_home=hermes_home))
        first_name = ops[0].get("name")
        with tempfile.TemporaryDirectory(prefix=_STAGE_PREFIX) as tmp:
            root = Path(tmp)
            staged: dict[str, Path] = {}
            for op in ops:
                name = op.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("pending operation needs a skill name")
                bare = Path(name.replace("\\", "/")).name
                if bare not in staged:
                    skill_dir = root / bare
                    if op.get("action") == "create":
                        skill_dir.mkdir()
                    else:
                        match = canonical_match if (canonical_match is not None and name == first_name) else None
                        if match is None:
                            found = list(resolve_canonical(name)) if resolve_canonical else []
                            if len(found) != 1:
                                raise ValueError(f"cannot resolve a single canonical skill for '{name}' ({len(found)} matches)")
                            match = found[0]
                        _copy_skill(Path(match), skill_dir)
                    staged[bare] = skill_dir
                _apply_op(op, staged[bare])
            verdicts: list[dict] = []
            for skill_dir in staged.values():
                verdicts.append(coerce_verdict(evaluator(skill_dir)))
        failing = next((v for v in verdicts if not verdict_passes(v)), None)
        if failing is not None:
            return _blocked(verdict=failing, error=None)
        return {"passed": True, "layer0_verdict": verdicts[0], "layer0_verdicts": verdicts, "layer0_error": None}
    except BaseException as exc:  # fail closed on ANY error (ImportError/OSError/TimeoutError/TypeError/...)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return _blocked(verdict=None, error=f"{type(exc).__name__}: {exc}"[:500] or type(exc).__name__)
