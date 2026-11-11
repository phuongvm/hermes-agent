"""Slice 4 / Module D4 — TierPinnedFallbackPolicy, Hermes client side (design.md §5.4).

The 9Router side persists ``fallback_tier_floor = "TIER_1_FRONTIER_REASONING"`` on the five specialist
combos and stamps ``x-model-id`` on every 2xx. This module is the client-side counterpart, one owner for:

* ``TIER_1_ALLOWLIST`` / ``is_tier1_model_id`` — which resolved model ids a specialist profile may accept;
* ``enforce_tier1_response_guard`` — raises ``TierFloorViolationError`` when a specialist profile's response
  was answered by a TIER_3 tail (``nemotron-free``, ``crouton-alpha``, ``cb-always-on`` …). The router is
  the primary enforcer; this guard is defence in depth against a stale router or a mis-stamped combo;
* ``tier1_backoff_for_attempt`` — the ``(15s, 30s, 60s, 120s)`` schedule then ``WAIT(120s)`` applied after a
  violation or a 503 from a tier-pinned combo. The agent WAITS; it never swaps in a cheaper model;
* ``verify_9router_combo_readback`` — verifies all 5 specialist combos against ``GET /api/combos`` (or the
  deterministic offline fixture named by ``NINEROUTER_READBACK_FIXTURE_PATH``) and writes
  ``9router_combo_readback_receipt.json``;
* ``audit_specialist_cheap_model`` — proves ``smart_model_routing.cheap_model`` (``cb-always-on``) is enabled
  for the default profile only and disabled for every specialist profile.

Stdlib only; no network unless ``verify_9router_combo_readback`` runs without a fixture.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

TIER_1_FRONTIER_REASONING = "TIER_1_FRONTIER_REASONING"

SPECIALIST_COMBO_IDS: tuple[str, ...] = (
    "cb-agent-coder", "cb-agent-reviewer", "cb-agent-leader", "cb-agent-designer", "cb-agent-qa",
)
SPECIALIST_PROFILES: tuple[str, ...] = ("coder", "reviewer", "leader", "designer", "qa")

# Exact combo / alias names a tier-pinned profile may be answered by (mirrors open-sse/services/tierFloor.js).
TIER_1_ALLOWLIST: tuple[str, ...] = SPECIALIST_COMBO_IDS + (
    "cb-gemini-argon", "cb-claude-opus", "cb-gpt-sol-review", "cb-gemini-pro", "cb-opus-5.5", "cb-gpt-astra",
)
TIER_1_PROVIDER_MODEL_PREFIXES: tuple[str, ...] = (
    "ag/gemini-3-pro", "ag/claude-opus", "cc/claude-opus", "cc/claude-sonnet-4", "cx/gpt-5",
)
TIER_3_FORBIDDEN_MARKERS: tuple[str, ...] = ("nemotron", "crouton", "cb-always-on", "cb-nous-free", ":free")

TIER1_BACKOFF_SCHEDULE_S: tuple[int, ...] = (15, 30, 60, 120)
TIER1_WAIT_S = 120
MODEL_ID_HEADER = "x-model-id"
RECEIPT_FILENAME = "9router_combo_readback_receipt.json"
FIXTURE_ENV = "NINEROUTER_READBACK_FIXTURE_PATH"


class TierFloorViolationError(RuntimeError):
    """A tier-pinned profile received a response from outside ``TIER_1_ALLOWLIST``."""

    def __init__(self, *, model_id: Optional[str], profile_model: str, reason: str = "") -> None:
        self.model_id = model_id
        self.profile_model = profile_model
        shown = model_id if model_id is not None else "<missing provenance>"
        super().__init__(
            f"TIER_1_FRONTIER_REASONING floor violated for {profile_model}: response answered by {shown!r}"
            + (f" ({reason})" if reason else "") + "; backing off instead of downgrading"
        )


class TierFloorReadbackError(RuntimeError):
    """9Router combo readback validation failed."""

    def __init__(self, violations: list[dict[str, Any]], receipt: Optional[dict[str, Any]] = None) -> None:
        self.violations = violations
        self.receipt = receipt
        super().__init__(f"9Router combo readback failed with {len(violations)} violation(s): {violations}")


# ---------------------------------------------------------------------------- allowlist
def is_tier1_model_id(model_id: Any) -> bool:
    if not isinstance(model_id, str) or not model_id.strip():
        return False
    m = model_id.strip()
    if m in TIER_1_ALLOWLIST:
        return True
    lower = m.lower()
    if any(marker in lower for marker in TIER_3_FORBIDDEN_MARKERS):
        return False
    return any(lower.startswith(prefix) for prefix in TIER_1_PROVIDER_MODEL_PREFIXES)


def is_tier_pinned_model(profile_model: Any) -> bool:
    return isinstance(profile_model, str) and profile_model.strip() in SPECIALIST_COMBO_IDS


def tier_floor_blocks_fallback(agent: Any, fb_provider: str, fb_model: str) -> bool:
    """Slice 4 / Module D4: a specialist primary (``cb-agent-*``) never falls back to a TIER_3 tail. The
    pinned profile's primary model is remembered on first sight so the check survives a mid-turn swap."""
    pinned = getattr(agent, "_tier_pinned_primary", None)
    if not is_tier_pinned_model(pinned):  # unset, stale, or a MagicMock attribute
        pinned = None
        if is_tier_pinned_model(getattr(agent, "model", None)):
            pinned = agent._tier_pinned_primary = agent.model
    if not pinned:
        return False
    if is_tier1_model_id(fb_model):
        return False
    logger.warning("Fallback skip: %s/%s is below the TIER_1 floor pinned by %s (waiting instead of downgrading)",
                   fb_provider, fb_model, pinned)
    return True


def _header_lookup(source: Any) -> Callable[[str], Optional[str]]:
    headers = getattr(source, "headers", source)
    if headers is None:
        return lambda _k: None
    getter = getattr(headers, "get", None)
    if callable(getter):
        def _get(key: str) -> Optional[str]:
            for candidate in (key, key.lower(), key.title(), key.upper(), "X-Model-Id"):
                try:
                    value = getter(candidate)
                except Exception:
                    value = None
                if value is not None:
                    return str(value)
            return None
        return _get
    return lambda _k: None


def enforce_tier1_response_guard(response_or_headers: Any, *, profile_model: Optional[str],
                                 require_header: bool = False) -> Optional[str]:
    """Return the model provenance that answered (or ``None``) — raising ``TierFloorViolationError`` when
    ``profile_model`` is a specialist combo and either ``x-model-id`` or ``response.model`` is outside
    ``TIER_1_ALLOWLIST``, or if model provenance is missing/empty on a specialist profile. Non-specialist
    profiles are never guarded."""
    if not is_tier_pinned_model(profile_model):
        return None
    assert profile_model is not None

    header_model_id = _header_lookup(response_or_headers)(MODEL_ID_HEADER)
    body_model = getattr(response_or_headers, "model", None)
    if body_model is None and isinstance(response_or_headers, dict):
        body_model = response_or_headers.get("model")

    if require_header and not header_model_id:
        raise TierFloorViolationError(model_id=None, profile_model=profile_model, reason="header required")

    if not header_model_id and not body_model:
        raise TierFloorViolationError(model_id=None, profile_model=profile_model, reason="missing provenance")

    if header_model_id and not is_tier1_model_id(header_model_id):
        raise TierFloorViolationError(model_id=header_model_id, profile_model=profile_model, reason="header rejected")

    if body_model and not is_tier1_model_id(str(body_model)):
        raise TierFloorViolationError(model_id=str(body_model), profile_model=profile_model, reason="body model rejected")

    return header_model_id or str(body_model)


# ---------------------------------------------------------------------------- backoff
def tier1_backoff_for_attempt(attempt: int, *, retry_after_s: Optional[float] = None) -> int:
    """Seconds to WAIT before retrying a tier-pinned combo: schedule step for ``attempt`` (0-based), then the
    ``TIER1_WAIT_S`` plateau. A ``Retry-After`` hint can lengthen the wait (capped at the plateau) but never
    shorten it below the schedule."""
    idx = max(0, int(attempt))
    base = TIER1_BACKOFF_SCHEDULE_S[idx] if idx < len(TIER1_BACKOFF_SCHEDULE_S) else TIER1_WAIT_S
    if retry_after_s is not None:
        try:
            hinted = int(float(retry_after_s))
        except (TypeError, ValueError):
            hinted = 0
        base = max(base, min(hinted, TIER1_WAIT_S))
    return base


# ---------------------------------------------------------------------------- 9router readback
def _default_fetch(url: str, headers: Mapping[str, str]) -> Any:
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _combo_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("combos", payload.get("data", []))
    return [c for c in (payload or []) if isinstance(c, dict)]


def _combo_violations(combos: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {str(c.get("name")): c for c in combos}
    violations: list[dict[str, Any]] = []
    for name in SPECIALIST_COMBO_IDS:
        combo = by_name.get(name)
        if combo is None:
            violations.append({"combo": name, "reason_code": "COMBO_ABSENT"})
            continue
        if combo.get("fallback_tier_floor") != TIER_1_FRONTIER_REASONING:
            violations.append({"combo": name, "reason_code": "MISSING_TIER_FLOOR",
                               "observed": combo.get("fallback_tier_floor")})
            continue
        models = combo.get("models")
        if not models or len(models) < 1:
            violations.append({"combo": name, "reason_code": "EMPTY_MODELS"})
            continue
        bad = [m for m in models if not is_tier1_model_id(m)]
        if bad:
            violations.append({"combo": name, "reason_code": "TIER_3_MEMBER", "members": bad})
    return violations


def verify_9router_combo_readback(*, base_url: str, receipt_path: Optional[Path] = None,
                                  api_key: Optional[str] = None,
                                  fetch: Optional[Callable[[str, Mapping[str, str]], Any]] = None,
                                  fixture_path: Optional[Path] = None) -> dict[str, Any]:
    """Verify the five specialist combos carry the TIER_1 floor with TIER_1-only members. Reads
    ``NINEROUTER_READBACK_FIXTURE_PATH`` (or ``fixture_path``) for a deterministic offline run, otherwise
    ``GET {base_url}/api/combos``. Always writes ``receipt_path`` (default ``./9router_combo_readback_receipt.json``);
    raises ``TierFloorReadbackError`` if violations exist (or router unreachable without fixture)."""
    started = time.monotonic()
    fixture = fixture_path or (Path(os.environ[FIXTURE_ENV]) if os.environ.get(FIXTURE_ENV) else None)
    receipt: dict[str, Any] = {
        "schema": "9router_combo_readback_receipt.v1",
        "tier_floor": TIER_1_FRONTIER_REASONING,
        "specialists": list(SPECIALIST_COMBO_IDS),
        "base_url": base_url,
        "mode": "offline_fixture" if fixture is not None else "live",
        "source": "fixture" if fixture is not None else "live",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    combos: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    if fixture is not None:
        receipt["fixture_path"] = str(fixture)
        try:
            raw = Path(fixture).read_bytes()
            receipt["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
            combos = _combo_list(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError) as exc:
            violations.append({"combo": "*", "reason_code": "FIXTURE_UNREADABLE", "error": str(exc)})
    else:
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            combos = _combo_list((fetch or _default_fetch)(base_url.rstrip("/") + "/api/combos", headers))
        except Exception as exc:
            violations.append({"combo": "*", "reason_code": "ROUTER_UNREACHABLE", "error": str(exc)[:200]})
    if not violations:
        violations = _combo_violations(combos)
    by_name = {str(c.get("name")): c for c in combos}
    verified = [n for n in SPECIALIST_COMBO_IDS if not any(v["combo"] in (n, "*") for v in violations)]
    per_combo_digests = {
        n: hashlib.sha256(json.dumps(by_name[n].get("models", []), sort_keys=True).encode()).hexdigest()
        for n in verified if n in by_name
    }
    receipt.update({
        "combo_count": len(verified),
        "combos_verified": len(verified),
        "verified": verified,
        "per_combo_digests": per_combo_digests,
        "violations": violations,
        "passed": not violations,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    })
    out = Path(receipt_path) if receipt_path is not None else Path.cwd() / RECEIPT_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.parent / f".{out.name}.tmp.{os.getpid()}"
    with open(tmp_out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_out, out)
    receipt["receipt_path"] = str(out)
    if violations:
        raise TierFloorReadbackError(violations, receipt)
    return receipt


# ---------------------------------------------------------------------------- config audit
_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][\w.-]*):\s*(?P<value>.*?)\s*$")


def _yaml_scalar_at(text: str, path: Sequence[str]) -> Optional[str]:  # pragma: no cover - superseded by _scalar
    return _scalar(text, *path)


def _scalar(text: str, *path: str) -> Optional[str]:
    # Walk with a flexible child indent: the helper above requires exact indents, so re-scan per level.
    cur_text, cur_indent = text, 0
    for i, key in enumerate(path):
        lines = cur_text.splitlines()
        found = None
        for idx, raw in enumerate(lines):
            m = _KEY_RE.match(raw)
            if m and len(m.group("indent")) == cur_indent and m.group("key") == key:
                found = idx
                break
        if found is None:
            return None
        m = _KEY_RE.match(lines[found])
        assert m is not None
        if i == len(path) - 1:
            return m.group("value").strip().strip("'\"") or None
        block: list[str] = []
        child_indent: Optional[int] = None
        for raw in lines[found + 1:]:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            ind = len(raw) - len(raw.lstrip())
            if ind <= cur_indent:
                break
            if child_indent is None:
                child_indent = ind
            block.append(raw)
        if child_indent is None:
            return None
        cur_text, cur_indent = "\n".join(block), child_indent
    return None


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"true", "yes", "on", "1"}


def audit_specialist_cheap_model(hermes_home: Path) -> dict[str, Any]:
    """``smart_model_routing.cheap_model`` must be enabled for the default profile only; every specialist
    profile (``profiles/<p>/config.yaml`` whose ``model.default`` is a specialist combo) must have
    ``smart_model_routing.enabled: false`` so no turn is quietly answered by ``cb-always-on``."""
    hermes_home = Path(hermes_home)
    root_cfg = (hermes_home / "config.yaml").read_text(encoding="utf-8", errors="ignore")
    report: dict[str, Any] = {
        "default_profile": {
            "model": _scalar(root_cfg, "model", "default"),
            "cheap_model": _scalar(root_cfg, "smart_model_routing", "cheap_model", "model"),
            "cheap_model_enabled": _truthy(_scalar(root_cfg, "smart_model_routing", "enabled")),
        },
        "profiles": {}, "violations": [],
    }
    profiles_dir = hermes_home / "profiles"
    for cfg in sorted(profiles_dir.glob("*/config.yaml")) if profiles_dir.exists() else []:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        model = _scalar(text, "model", "default")
        enabled = _truthy(_scalar(text, "smart_model_routing", "enabled"))
        cheap = _scalar(text, "smart_model_routing", "cheap_model", "model")
        report["profiles"][cfg.parent.name] = {"model": model, "cheap_model": cheap, "cheap_model_enabled": enabled}
        if is_tier_pinned_model(model) and enabled:
            report["violations"].append({"profile": cfg.parent.name,
                                         "reason_code": "CHEAP_MODEL_ENABLED_ON_SPECIALIST", "cheap_model": cheap})
    report["passed"] = not report["violations"]
    return report


__all__ = [
    "FIXTURE_ENV",
    "MODEL_ID_HEADER",
    "RECEIPT_FILENAME",
    "SPECIALIST_COMBO_IDS",
    "SPECIALIST_PROFILES",
    "TIER1_BACKOFF_SCHEDULE_S",
    "TIER1_WAIT_S",
    "TIER_1_ALLOWLIST",
    "TIER_1_FRONTIER_REASONING",
    "TierFloorReadbackError",
    "TierFloorViolationError",
    "audit_specialist_cheap_model",
    "enforce_tier1_response_guard",
    "is_tier1_model_id",
    "is_tier_pinned_model",
    "main",
    "tier1_backoff_for_attempt",
    "tier_floor_blocks_fallback",
    "verify_9router_combo_readback",
]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``--audit-config <hermes_home>`` and/or ``--readback <base_url> [--receipt PATH] [--api-key-env VAR]``.
    Exit 0 when every requested check passes, 2 otherwise."""
    import argparse
    parser = argparse.ArgumentParser(prog="tier_floor_policy", description=__doc__.splitlines()[0])
    parser.add_argument("--audit-config", type=Path, metavar="HERMES_HOME")
    parser.add_argument("--readback", metavar="BASE_URL")
    parser.add_argument("--receipt", type=Path, default=None)
    parser.add_argument("--api-key-env", default="HERMES_CUSTOM_AI_ROUTER_API_KEY")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    rc = 0
    results: dict[str, Any] = {}
    if args.audit_config is not None:
        results["config_audit"] = audit_specialist_cheap_model(args.audit_config)
        rc = rc or (0 if results["config_audit"]["passed"] else 2)
    if args.readback:
        try:
            results["readback"] = verify_9router_combo_readback(
                base_url=args.readback, receipt_path=args.receipt, api_key=os.environ.get(args.api_key_env))
            rc = rc or (0 if results["readback"]["passed"] else 2)
        except TierFloorReadbackError as exc:
            results["readback"] = exc.receipt or {"passed": False, "violations": exc.violations}
            rc = 2
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for name, rep in results.items():
            print(f"{'PASS' if rep['passed'] else 'FAIL'} {name}: {len(rep['violations'])} violation(s)")
            for v in rep["violations"]:
                print(f"  - {json.dumps(v, sort_keys=True)}")
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
