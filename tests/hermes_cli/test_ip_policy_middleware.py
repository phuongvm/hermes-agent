"""Behavioural tests for ``RadarIpPolicyMiddleware`` (Boundary 1: application ingress deny).

The middleware is driven through Starlette ``TestClient`` (HTTP + WebSocket). A tiny outer ASGI shim injects
the peer address (``scope["client"]``), which is what uvicorn does for the real listener.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hermes_cli.dashboard_auth.ip_policy import RadarIpPolicyMiddleware, is_exempt

NOW = 1_793_500_000.0
BLOCKED = "65.50.137.66"


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def _write(path, blocked=None, **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"blocked_ips": blocked or {}, "dismissed_alerts": {}}
    body.update(extra)
    path.write_text(json.dumps(body), encoding="utf-8")


def _entry(**kw):
    return {"blocked_at": NOW - 10, "reason": "t", **kw}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Rig:
    """The middleware wrapped around a trivial app, with a mutable injected peer address."""

    def __init__(self, tmp_path, **mw_kwargs):
        self.policy = tmp_path / "security" / "radar_ip_policy.json"
        self.deny_log = tmp_path / "logs" / "ingress-deny.log"
        self.clock = Clock()
        self.mono = Clock(1000.0)
        self.stats = 0
        self.peer = BLOCKED

        def counting_stat(p):
            self.stats += 1
            return os.stat(p)

        async def ok(request):
            return PlainTextResponse("ok")

        async def ws(websocket):
            await websocket.accept()
            await websocket.send_text("hi")
            await websocket.close()

        inner = Starlette(routes=[
            Route("/{path:path}", ok, methods=["GET", "POST"]),
            WebSocketRoute("/ws", ws),
        ])
        self.mw = RadarIpPolicyMiddleware(
            inner,
            policy_path_fn=lambda: self.policy,
            log_path_fn=lambda: self.deny_log,
            now_fn=self.clock,
            mono_fn=self.mono,
            stat_fn=counting_stat,
            **mw_kwargs,
        )

        async def shim(scope, receive, send):
            if scope["type"] in ("http", "websocket"):
                scope = dict(scope, client=(self.peer, 50000))
            await self.mw(scope, receive, send)

        self.client = TestClient(shim)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_blocked_ip_gets_403_on_http_without_reaching_the_app(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    for path in ("/", "/api/auth/ws-ticket", "/auth/password-login"):
        resp = rig.client.get(path)
        assert resp.status_code == 403
        assert resp.json() == {"detail": "Forbidden"}
    assert rig.client.post("/auth/password-login", content=b"x").status_code == 403


def test_blocked_ip_websocket_is_closed_1008_before_accept(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    with pytest.raises(WebSocketDisconnect) as exc:
        with rig.client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 1008


def test_unlisted_ip_is_forwarded_unchanged_http_and_ws(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    rig.peer = "203.0.113.77"
    assert rig.client.get("/x").text == "ok"
    with rig.client.websocket_connect("/ws") as ws:
        assert ws.receive_text() == "hi"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.5.5", "192.168.100.10", "169.254.1.1",
                                 "::1", "fd12:3456::1", "fe80::1"])
def test_private_loopback_and_linklocal_are_never_denied_even_if_listed(rig, ip):
    _write(rig.policy, {ip: _entry()})
    rig.peer = ip
    assert rig.client.get("/").status_code == 200


def test_operator_exempt_ips_are_never_denied(rig):
    _write(rig.policy, {BLOCKED: _entry()}, exempt_ips=[BLOCKED])
    assert rig.client.get("/").status_code == 200


def _records(rig):
    return [json.loads(x) for x in rig.deny_log.read_text(encoding="utf-8-sig").splitlines()]


def test_exempt_peer_listed_in_policy_is_forwarded_and_audited_as_ignored(rig):
    _write(rig.policy, {"127.0.0.1": _entry(), "192.168.100.10": _entry(), "::1": _entry()})
    before = (_sha(rig.policy), rig.policy.stat().st_mtime_ns)
    for peer in ("127.0.0.1", "192.168.100.10", "::1"):
        rig.peer = peer
        for _ in range(4):
            assert rig.client.get("/?token=SECRET", headers={"Authorization": "Bearer SECRET"}).status_code == 200
    assert not rig.deny_log.exists() or rig.deny_log.read_text(encoding="utf-8-sig") == ""  # nothing per-request
    rig.clock.advance(61)
    rig.mw.flush()
    recs = {r["ip"]: r for r in _records(rig)}
    assert set(recs) == {"127.0.0.1", "192.168.100.10", "::1"}
    for rec in recs.values():
        assert rec["event"] == "exempt_ignored" and rec["reason"] == "hard_exempt"
        assert rec["count"] == 4 and rec["source"] == "ingress" and rec["ts"] and rec["first"] <= rec["last"]
    assert "SECRET" not in rig.deny_log.read_text(encoding="utf-8-sig")
    assert (_sha(rig.policy), rig.policy.stat().st_mtime_ns) == before


def test_operator_exempt_entry_is_audited_with_its_own_reason(rig):
    _write(rig.policy, {BLOCKED: _entry()}, exempt_ips=[BLOCKED])
    assert rig.client.get("/").status_code == 200
    rig.mw.flush()
    (rec,) = _records(rig)
    assert (rec["ip"], rec["event"], rec["reason"], rec["count"]) == (BLOCKED, "exempt_ignored", "operator_exempt", 1)


def test_exempt_peer_not_listed_or_expired_is_not_audited(rig):
    _write(rig.policy, {BLOCKED: _entry(), "10.9.9.9": _entry(expires_at=NOW - 5)})
    rig.peer = "127.0.0.1"
    assert rig.client.get("/").status_code == 200  # exempt, but no policy entry matches it
    rig.peer = "10.9.9.9"
    assert rig.client.get("/").status_code == 200  # entry expired -> nothing was ignored
    rig.mw.flush()
    assert not rig.deny_log.exists()


def test_exempt_ignored_audit_is_rate_limited_and_self_flushes(rig):
    _write(rig.policy, {"127.0.0.1": _entry()})
    rig.peer = "127.0.0.1"
    for _ in range(50):
        rig.client.get("/")
    assert not rig.deny_log.exists()
    rig.mono.advance(61)
    rig.client.get("/")  # interval elapsed -> previous window flushed as one line
    lines = _records(rig)
    assert len(lines) == 1 and lines[0]["count"] == 51


def test_spoofed_forwarded_for_header_is_ignored(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    resp = rig.client.get("/", headers={"X-Forwarded-For": "127.0.0.1", "CF-Connecting-IP": "127.0.0.1"})
    assert resp.status_code == 403
    rig.peer = "203.0.113.5"
    assert rig.client.get("/", headers={"X-Forwarded-For": BLOCKED}).status_code == 200


def test_ipv4_mapped_ipv6_peer_is_unmapped_before_matching(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    rig.peer = "::ffff:65.50.137.66"
    assert rig.client.get("/").status_code == 403


def test_ipv6_prefix_entry_matches_suffix_rotation(rig):
    _write(rig.policy, {"2404:c0:960b:858c::/64": _entry()})
    rig.peer = "2404:c0:960b:858c:56bd::1"
    assert rig.client.get("/").status_code == 403
    rig.peer = "2404:c0:960b:858d::1"
    assert rig.client.get("/").status_code == 200


def test_expired_entry_is_ignored_and_future_expiry_is_enforced(rig):
    _write(rig.policy, {BLOCKED: _entry(expires_at=NOW - 1)})
    assert rig.client.get("/").status_code == 200
    _write(rig.policy, {BLOCKED: _entry(expires_at=NOW + 3600)})
    rig.mono.advance(2)
    os.utime(rig.policy, ns=(1, 2))  # force an mtime change regardless of fs timestamp granularity
    assert rig.client.get("/").status_code == 403


def test_missing_policy_file_allows_everything(rig):
    assert not rig.policy.exists()
    assert rig.client.get("/").status_code == 200


def test_corrupt_policy_after_good_load_keeps_last_good_and_warns_once(rig, caplog):
    _write(rig.policy, {BLOCKED: _entry()})
    assert rig.client.get("/").status_code == 403
    rig.policy.write_text("{not json", encoding="utf-8")
    os.utime(rig.policy, ns=(5, 7))
    rig.mono.advance(2)
    with caplog.at_level(logging.WARNING, logger="hermes_cli.dashboard_auth.ip_policy"):
        assert rig.client.get("/").status_code == 403
        rig.mono.advance(2)
        assert rig.client.get("/").status_code == 403
    assert sum("policy" in r.message.lower() for r in caplog.records) == 1


def test_legacy_policy_file_enforces_nothing(rig):
    rig.policy.parent.mkdir(parents=True)
    rig.policy.write_text('{"blocked_ips": {}, "dismissed_alerts": {}}', encoding="utf-8")
    assert rig.client.get("/").status_code == 200


def test_policy_stat_is_throttled_to_once_per_second(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    for _ in range(200):
        assert rig.client.get("/").status_code == 403
    assert rig.stats == 1
    rig.mono.advance(1.0)
    rig.client.get("/")
    assert rig.stats == 2


def test_policy_is_reparsed_only_when_mtime_changes(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    assert rig.client.get("/").status_code == 403
    _write(rig.policy, {})  # unblock
    os.utime(rig.policy, ns=(11, 13))
    assert rig.client.get("/").status_code == 403  # still inside the 1 s stat window
    rig.mono.advance(1.5)
    assert rig.client.get("/").status_code == 200


def test_middleware_never_mutates_the_policy_file_and_writes_no_auth_log(rig, tmp_path):
    _write(rig.policy, {BLOCKED: _entry()})
    before = (_sha(rig.policy), rig.policy.stat().st_mtime_ns)
    for _ in range(5):
        rig.client.get("/api/auth/ws-ticket")
    rig.clock.advance(120)
    rig.mw.flush()
    assert (_sha(rig.policy), rig.policy.stat().st_mtime_ns) == before
    assert not (tmp_path / "logs" / "dashboard-auth.log").exists()


def test_ingress_deny_audit_is_one_rate_limited_aggregate_per_ip(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    before = (_sha(rig.policy), rig.policy.stat().st_mtime_ns)
    n = 25
    for _ in range(n):
        rig.client.get("/auth/password-login?token=SECRET", headers={"Authorization": "Bearer SECRET"},
                       cookies={"s": "SECRET"})
        rig.clock.advance(0.5)
    assert not rig.deny_log.exists() or rig.deny_log.read_text(encoding="utf-8-sig") == ""  # nothing per-request
    rig.clock.advance(61)
    rig.mw.flush()
    lines = [json.loads(line) for line in rig.deny_log.read_text(encoding="utf-8-sig").splitlines()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["ip"] == BLOCKED and rec["count"] == n and rec["first"] <= rec["last"]
    assert "SECRET" not in rig.deny_log.read_text(encoding="utf-8-sig")
    assert set(rec) == {"ts", "ip", "count", "first", "last", "source"}
    rig.mw.flush()  # nothing new -> nothing appended
    assert len(rig.deny_log.read_text(encoding="utf-8-sig").splitlines()) == 1
    assert (_sha(rig.policy), rig.policy.stat().st_mtime_ns) == before


def test_deny_aggregate_flushes_itself_after_the_interval_without_per_request_lines(rig):
    _write(rig.policy, {BLOCKED: _entry()})
    for _ in range(3):
        rig.client.get("/")
    rig.mono.advance(61)
    rig.client.get("/")  # interval elapsed: this deny triggers the flush of the previous window
    lines = [json.loads(x) for x in rig.deny_log.read_text(encoding="utf-8-sig").splitlines()]
    assert sum(r["count"] for r in lines) >= 3


def test_internal_failure_forwards_instead_of_raising(tmp_path):
    rig = Rig(tmp_path)
    rig.mw._stat = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
    assert rig.client.get("/").status_code == 200


def test_lifespan_scope_passes_through(rig):
    with TestClient(rig.mw) as client:  # runs lifespan startup/shutdown through the middleware
        assert client is not None


@pytest.mark.parametrize("ip,expected", [
    ("127.0.0.1", True), ("::1", True), ("192.168.100.4", True), ("10.0.0.1", True),
    ("172.31.255.255", True), ("172.32.0.1", False), ("169.254.9.9", True), ("fc00::1", True),
    ("fe80::2", True), ("65.50.137.66", False), ("203.0.113.9", False), ("::ffff:192.168.1.1", True),
])
def test_is_exempt(ip, expected):
    assert is_exempt(ip) is expected


def test_registered_outermost_in_web_server():
    import hermes_cli.web_server as ws

    from starlette.middleware.cors import CORSMiddleware

    classes = [m.cls for m in ws.app.user_middleware]
    assert classes[0].__name__ == "RadarIpPolicyMiddleware"  # Starlette: index 0 = outermost
    # CORS may be a CORSMiddleware subclass (e.g. _LocalOriginCORSMiddleware).
    cors_idx = [i for i, c in enumerate(classes) if issubclass(c, CORSMiddleware)]
    assert cors_idx and cors_idx[0] > 0
