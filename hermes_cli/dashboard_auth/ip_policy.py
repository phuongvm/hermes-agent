"""Application-layer ingress deny driven by the Radar IP policy (Boundary 1).

``RadarIpPolicyMiddleware`` is a pure ASGI layer registered as the outermost layer of the Dashboard. A client IP
listed in ``$HERMES_HOME/security/radar_ip_policy.json`` (``blocked_ips``) gets ``403`` on HTTP and a pre-accept
close ``1008`` on WebSocket before any host-header, authentication, session, database or audit work runs.

Invariants:
  * the client identity is ``scope["client"][0]`` only (uvicorn rewrites it for operator-trusted proxies);
    ``X-Forwarded-For`` and friends are never read;
  * loopback, RFC1918, link-local, IPv6 ULA and the policy's ``exempt_ips`` are never denied, even if listed;
  * the policy file is only ever read (mtime-checked, at most one ``stat`` per second); a missing or malformed file
    keeps the last good snapshot; nothing here raises into the request path;
  * the only write is a per-IP deny aggregate appended to ``logs/ingress-deny.log`` at most once per interval.
    It is deliberately not ``dashboard-auth.log`` (Radar tails that file). No request data is recorded.
    A blocked entry that matches an exempt peer is forwarded and recorded in the same log as an
    ``exempt_ignored`` aggregate (ts, ip, source, reason, count), also at most once per IP per interval.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

STAT_INTERVAL_S = 1.0
FLUSH_INTERVAL_S = 60.0
MAX_ENTRIES = 4096
_BODY = b'{"detail":"Forbidden"}'

_EXEMPT_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16", "fc00::/7", "fe80::/10",
))


def _canonical(value) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """*value* as an address with IPv4-mapped IPv6 unmapped, or None when it is not an IP."""
    try:
        addr = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def is_exempt(ip, extra=()) -> bool:
    """True when *ip* may never be denied: loopback, RFC1918, link-local, IPv6 ULA or listed in *extra*."""
    addr = _canonical(ip)
    if addr is None:
        return False
    if any(addr in net for net in _EXEMPT_NETS if net.version == addr.version):
        return True
    return any(addr in net for net in extra if net.version == addr.version)


def _default_home() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home())


def _default_policy_path() -> Path:
    return _default_home() / "security" / "radar_ip_policy.json"


def _default_log_path() -> Path:
    return _default_home() / "logs" / "ingress-deny.log"


def _net(text):
    try:
        net = ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError:
        return None
    mapped = getattr(net.network_address, "ipv4_mapped", None)
    if mapped is not None and net.version == 6 and net.prefixlen >= 96:
        return ipaddress.ip_network(f"{mapped}/{net.prefixlen - 96}", strict=False)
    return net


class RadarIpPolicyMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        policy_path_fn: Callable[[], Path] | None = None,
        now_fn: Callable[[], float] = time.time,
        *,
        log_path_fn: Callable[[], Path] | None = None,
        mono_fn: Callable[[], float] = time.monotonic,
        stat_fn: Callable[[Path], os.stat_result] = os.stat,
    ) -> None:
        self.app = app
        self._policy_path_fn = policy_path_fn or _default_policy_path
        self._log_path_fn = log_path_fn or _default_log_path
        self._now = now_fn
        self._mono = mono_fn
        self._stat = stat_fn
        self._next_stat = float("-inf")
        self._mtime_ns: int | None = None
        self._blocked: list[tuple] = []  # (network, expires_at | None)
        self._exempt: tuple = ()
        self._warned: str | None = None
        self._counts: dict[str, dict] = {}
        self._ignored: dict[str, dict] = {}
        self._last_flush = self._mono()

    # ------------------------------------------------------------------ policy cache
    def _refresh(self) -> None:
        mono = self._mono()
        if mono < self._next_stat:
            return
        self._next_stat = mono + STAT_INTERVAL_S
        path = self._policy_path_fn()
        try:
            mtime = self._stat(path).st_mtime_ns
        except FileNotFoundError:
            return  # missing: keep the last good snapshot (initially empty = deny nothing)
        except OSError as exc:
            self._warn(f"radar ip policy unreadable: {exc.__class__.__name__}")
            return
        if mtime == self._mtime_ns:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("policy root is not an object")
            blocked = data.get("blocked_ips")
            exempt = data.get("exempt_ips")
            parsed = self._parse(blocked if isinstance(blocked, dict) else {})
            exempt_nets = tuple(n for n in (_net(x) for x in (exempt if isinstance(exempt, list) else [])) if n)
        except (OSError, ValueError) as exc:
            self._mtime_ns = mtime  # do not re-warn / re-read the same bad bytes every second
            self._warn(f"radar ip policy malformed, keeping last good: {exc.__class__.__name__}")
            return
        self._blocked, self._exempt, self._mtime_ns, self._warned = parsed, exempt_nets, mtime, None

    @staticmethod
    def _parse(blocked: dict) -> list[tuple]:
        out: list[tuple] = []
        for key, meta in blocked.items():
            net = _net(key)
            if net is None:
                continue
            expires = meta.get("expires_at") if isinstance(meta, dict) else None
            try:
                expires = float(expires) if expires is not None else None
            except (TypeError, ValueError):
                expires = None
            out.append((net, expires))
            if len(out) >= MAX_ENTRIES:
                logger.warning("radar ip policy: more than %d entries, extras ignored", MAX_ENTRIES)
                break
        return out

    def _warn(self, message: str) -> None:
        if self._warned != message:
            self._warned = message
            logger.warning(message)

    def _is_denied(self, ip: str) -> bool:
        self._refresh()
        if not self._blocked:
            return False
        addr = _canonical(ip)
        if addr is None:
            return False
        now = self._now()
        listed = any(
            addr in net and (expires is None or expires > now)
            for net, expires in self._blocked if net.version == addr.version
        )
        if not listed:
            return False
        if is_exempt(addr):
            self._count_ignored(str(addr), "hard_exempt")
            return False
        if is_exempt(addr, self._exempt):
            self._count_ignored(str(addr), "operator_exempt")
            return False
        return True

    def _count_ignored(self, ip: str, reason: str) -> None:
        """Aggregate a policy entry that was bypassed because the peer is exempt (never denied)."""
        now = self._now()
        slot = self._ignored.get(ip)
        if slot is None:
            if len(self._ignored) >= MAX_ENTRIES:
                return  # bounded: drop new keys until the next flush
            slot = self._ignored[ip] = {"count": 0, "first": now, "last": now, "reason": reason}
        slot["count"] += 1
        slot["last"] = now
        if self._mono() - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()

    # ------------------------------------------------------------------ deny aggregate
    def _count(self, ip: str) -> None:
        now = self._now()
        slot = self._counts.setdefault(ip, {"count": 0, "first": now, "last": now})
        slot["count"] += 1
        slot["last"] = now
        if self._mono() - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()

    def flush(self) -> None:
        """Append one aggregate line per denied / exempt-ignored IP to ``ingress-deny.log`` and reset the counters."""
        self._last_flush = self._mono()
        if not self._counts and not self._ignored:
            return
        counts, self._counts = self._counts, {}
        ignored, self._ignored = self._ignored, {}
        ts = self._now()
        records = [
            {"ts": ts, "ip": ip, "count": slot["count"], "first": slot["first"],
             "last": slot["last"], "source": "ingress"}
            for ip, slot in counts.items()
        ] + [
            {"ts": ts, "ip": ip, "count": slot["count"], "first": slot["first"],
             "last": slot["last"], "source": "ingress", "event": "exempt_ignored",
             "reason": slot["reason"]}
            for ip, slot in ignored.items()
        ]
        try:
            path = self._log_path_fn()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(rec) + "\n" for rec in records)
        except OSError as exc:
            self._warn(f"ingress-deny log unwritable: {exc.__class__.__name__}")

    # ------------------------------------------------------------------ ASGI
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        try:
            client = scope.get("client")
            ip = client[0] if client else ""
            denied = bool(ip) and self._is_denied(ip)
            if denied:
                self._count(str(_canonical(ip)))
        except Exception:  # health: allow BLE001 -- ASGI fail-open boundary: a policy-check fault must never 500 the Dashboard
            logger.warning("radar ip policy check failed; request forwarded", exc_info=True)
            denied = False
        if not denied:
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(_BODY)).encode())]})
        await send({"type": "http.response.body", "body": _BODY})
