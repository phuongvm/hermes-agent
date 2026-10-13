"""Deterministic evaluation: raw probe output + config + optional baseline -> findings.

A metric whose probe section failed is reported UNKNOWN, never estimated. A metric with a
second, independent source is cross-checked; ``verified`` is True only when both sources agree,
False when they disagree, None when no second source exists.
"""
from __future__ import annotations

import json
import posixpath

import sentinel_parse as P
from sentinel_probes import DB_PORTS

ORDER = {"CRIT": 0, "WARN": 1, "UNKNOWN": 2, "INFO": 3, "OK": 4}
GB = 1e9
BASH_PROBES = ("linux_host", "docker_engine", "docker_vm", "ubuntu_root")


def finding(fid, area, title, status, value=None, threshold=None, evidence=(), verified=None, note=""):
    return {"id": fid, "area": area, "title": title, "status": status, "value": value,
            "threshold": threshold, "verified": verified, "note": note,
            "evidence": [e for e in evidence if e]}


def ev(probe, section, line):
    return {"probe": probe, "section": section, "line": str(line)[:300]}


def unknown(fid, area, title, probe, section, sec):
    rc = None if sec is None else sec.get("rc")
    head = "\n".join((sec or {}).get("lines", [])[:5])
    return finding(fid, area, title, "UNKNOWN", note=f"{probe}/{section} failed or missing (rc={rc})",
                   evidence=[ev(probe, section, head)] if head else [])


def level(value, warn, crit=None):
    if value is None:
        return "UNKNOWN"
    if crit is not None and value >= crit:
        return "CRIT"
    return "WARN" if value >= warn else "OK"


def gb(n):
    return round(n / GB, 2)


class Ctx:
    """Parsed view of one run's raw probe output."""

    def __init__(self, cfg: dict, raw: dict[str, dict], baseline: dict | None):
        self.cfg, self.raw, self.baseline = cfg, raw, baseline or {}
        self.t = cfg["thresholds"]
        self.sec = {pid: P.sections(raw.get(pid, {}).get("stdout", "")) for pid in BASH_PROBES}
        self.containers = self._containers()
        self.win = self._windows()

    def s(self, probe, name):
        return self.sec.get(probe, {}).get(name)

    def ok(self, probe, name):
        sec = self.s(probe, name)
        return sec if P.ok(sec) else None

    def sql(self, probe, sep):
        r = self.raw.get(probe) or {}
        return P.tagged_rows(r.get("stdout", ""), sep) if r.get("rc") == 0 else None

    def _containers(self):
        ps, ins = self.ok("docker_engine", "docker_ps"), self.ok("docker_engine", "docker_inspect")
        if ps is None or ins is None:
            return None
        rows, _ = P.json_lines(ps["lines"])
        by_id = {d["id"]: d for d in P.json_lines(ins["lines"])[0]}
        for r in rows:
            r["inspect"] = by_id.get(r["id"], {})
        return sorted(rows, key=lambda r: r["name"])

    def _windows(self):
        r = self.raw.get("windows_host") or {}
        if r.get("rc") != 0:
            return None
        try:
            return json.loads(r.get("stdout", "").strip())
        except ValueError:
            return None


# ------------------------------------------------------------------ collection
def check_collection(c: Ctx) -> list[dict]:
    out = []
    for pid in sorted(c.raw):
        r = c.raw[pid]
        failed = sorted(n for n, s in c.sec.get(pid, {}).items() if s.get("rc") != 0)
        if r.get("rc") != 0 or failed:
            out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} incomplete", "UNKNOWN",
                               value={"rc": r.get("rc"), "failed_sections": failed},
                               evidence=[ev(pid, "stderr", (r.get("stderr") or "")[:300])]))
    return out


# ------------------------------------------------------------------ docker
def check_docker(c: Ctx) -> list[dict]:
    if c.containers is None:
        return [unknown("DOCKER-LIVENESS", "docker", "Container liveness", "docker_engine", "docker_ps",
                        c.s("docker_engine", "docker_ps"))]
    return [_liveness(c), _health(c), _oom(c), _restarts(c), _log_rotation(c), _rw_layers(c),
            _log_sizes(c), _privileged(c), _db_ports(c)]


def _liveness(c):
    down = [r for r in c.containers if r["state"] != "running"]
    running = len(c.containers) - len(down)
    verified, note = None, "docker info unavailable"
    info_sec = c.ok("docker_engine", "docker_info")
    info = P.json_lines(info_sec["lines"])[0] if info_sec else []
    if info:
        verified = info[0]["running"] == running and info[0]["total"] == len(c.containers)
        note = (f"docker info running={info[0]['running']} total={info[0]['total']}; "
                f"ps running={running} total={len(c.containers)}")
    evid = [ev("docker_engine", "docker_ps", f"{r['name']}|{r['state']}|{r['status']}") for r in down]
    return finding("DOCKER-LIVENESS", "docker", "Containers not running", "CRIT" if down else "OK",
                   value={"running": running, "total": len(c.containers), "down": [r["name"] for r in down]},
                   evidence=evid or [ev("docker_engine", "docker_info", note)], verified=verified, note=note)


def _health(c):
    bad = [r for r in c.containers if r["inspect"].get("health") == "unhealthy"]
    return finding("DOCKER-HEALTH", "docker", "Healthcheck reports unhealthy", "WARN" if bad else "OK",
                   value=[r["name"] for r in bad],
                   evidence=[ev("docker_engine", "docker_ps", f"{r['name']}|{r['status']}") for r in bad])


def _oom(c):
    bad = [r for r in c.containers if r["inspect"].get("oom_killed")]
    return finding("DOCKER-OOM", "docker", "Container last exit was OOM kill", "CRIT" if bad else "OK",
                   value=[r["name"] for r in bad],
                   evidence=[ev("docker_engine", "docker_inspect", f"{r['name']} oom_killed=true") for r in bad])


def _restarts(c):
    cur = {r["name"]: r["inspect"].get("restart_count", 0) for r in c.containers}
    prev = c.baseline.get("restart_counts")
    if prev is None:
        nonzero = {n: v for n, v in cur.items() if v}
        return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "INFO", value=nonzero,
                       note="no baseline: cumulative RestartCount shown; weekly delta starts next run")
    # RestartCount resets when a container is re-created, so a smaller value counts as all-new restarts.
    deltas = {n: (v - prev[n] if v >= prev[n] else v) for n, v in cur.items() if n in prev}
    deltas = {n: d for n, d in deltas.items() if d > 0}
    return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "WARN" if deltas else "OK",
                   value=deltas, evidence=[ev("docker_engine", "docker_inspect",
                                              f"{n} restart_count={cur[n]} baseline={prev[n]}") for n in deltas])


def _log_rotation(c):
    bad = [r["name"] for r in c.containers
           if r["inspect"].get("log_type") == "json-file" and "max-size" not in (r["inspect"].get("log_opts") or {})]
    return finding("DOCKER-LOG-ROTATION", "docker", "json-file logs without max-size", "WARN" if bad else "OK",
                   value={"count": len(bad), "containers": bad},
                   evidence=[ev("docker_engine", "docker_inspect", f"{n} log_opts has no max-size") for n in bad[:5]])


def _rw_layers(c):
    limit = c.t["container_rw_mb_warn"] * 1e6
    big = []
    for r in c.containers:
        size = P.human_bytes((r.get("size") or "").split(" ", 1)[0])
        if size is not None and size >= limit:
            big.append((r["name"], size, r["size"]))
    return finding("DOCKER-RW-LAYER", "storage", "Container writable layer over threshold", "WARN" if big else "OK",
                   value={n: gb(s) for n, s, _ in big}, threshold=f">= {c.t['container_rw_mb_warn']} MB",
                   evidence=[ev("docker_engine", "docker_ps", f"{n} size={raw}") for n, _, raw in big])


def _log_sizes(c):
    sec = c.s("docker_vm", "container_logs")
    if not P.ok(sec):
        return unknown("DOCKER-LOG-SIZE", "storage", "Container json log size", "docker_vm", "container_logs", sec)
    by_file = {posixpath.basename(p): v for p, v in P.du_rows(sec["lines"]).items()}
    limit = c.t["container_log_mb_warn"] * 1e6
    big = []
    for r in c.containers:
        row = by_file.get(posixpath.basename(r["inspect"].get("log_path") or ""))
        if row and row["bytes"] >= limit:
            big.append((r["name"], row))
    total = sum(v["bytes"] for v in by_file.values())
    return finding("DOCKER-LOG-SIZE", "storage", "Container json log over threshold", "WARN" if big else "OK",
                   value={"total_gb": gb(total), "over_gb": {n: gb(row["bytes"]) for n, row in big}},
                   threshold=f">= {c.t['container_log_mb_warn']} MB",
                   evidence=[ev("docker_vm", "container_logs", row["raw"]) for _, row in big])


def _privileged(c):
    bad = []
    for r in c.containers:
        i = r["inspect"]
        sock = any(m.get("source") in ("/var/run/docker.sock", "/run/docker.sock") for m in i.get("mounts", []))
        if i.get("privileged") or sock:
            bad.append(f"{r['name']} privileged={i.get('privileged')} docker_sock={sock}")
    return finding("SEC-DOCKER-PRIV", "security", "Privileged container or docker.sock mount", "WARN" if bad else "OK",
                   value=bad, evidence=[ev("docker_engine", "docker_inspect", b) for b in bad])


def _db_ports(c):
    exposed = []
    for r in c.containers:
        for port, binds in sorted((r["inspect"].get("ports") or {}).items()):
            for b in binds or []:
                if port.split("/", 1)[0] in DB_PORTS and b.get("HostIp", "") in ("", "0.0.0.0", "::"):
                    exposed.append(f"{r['name']} {port} -> {b.get('HostIp') or '*'}:{b.get('HostPort')}")
    return finding("SEC-DB-PORTS", "security", "Datastore port published on all interfaces",
                   "WARN" if exposed else "OK", value=exposed,
                   evidence=[ev("docker_engine", "docker_inspect", e) for e in exposed])
