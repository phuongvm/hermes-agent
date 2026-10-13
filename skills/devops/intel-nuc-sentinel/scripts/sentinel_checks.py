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
    evid = [e for e in evidence if e and e.get("line")]
    if not evid:
        # Spec: every finding cites the raw probe/section line it was derived from; never synthesise one.
        raise ValueError(f"finding {fid}: no evidence line")
    return {"id": fid, "area": area, "title": title, "status": status, "value": value,
            "threshold": threshold, "verified": verified, "note": note,
            "evidence": evid}


def ev(probe, section, line):
    return {"probe": probe, "section": section, "line": str(line)[:300]}


def unknown(fid, area, title, probe, section, sec):
    rc = None if sec is None else sec.get("rc")
    lines = (sec or {}).get("lines", [])
    head = "\n".join(lines[:5]) if lines else f"{probe}/{section} missing or failed (rc={rc})"
    return finding(fid, area, title, "UNKNOWN", note=f"{probe}/{section} failed or missing (rc={rc})",
                   evidence=[ev(probe, section, head)])


def level(value, warn, crit=None):
    if value is None:
        return "UNKNOWN"
    if crit is not None and value >= crit:
        return "CRIT"
    return "WARN" if value >= warn else "OK"


def gb(n):
    return round(n / GB, 2)


def expected_sections(cfg: dict) -> dict[str, list[str]]:
    return {
        "linux_host": ["epoch", "uptime", "nproc", "loadavg", "meminfo", "df", "vhdx_stat"],
        "docker_engine": [
            "docker_ps", "docker_info", "docker_inspect", "docker_df",
            *[f"redis_ping:{c}" for c in cfg.get("redis", [])],
            *[f"redis_persistence:{c}" for c in cfg.get("redis", [])],
            *[f"redis_memory:{c}" for c in cfg.get("redis", [])],
            "zookeeper_env",
        ],
        "docker_vm": ["df", "container_logs"],
        "ubuntu_root": ["df", "volumes_du", "data_du", "sshd", "apt_upgradable", "apt_cache_mtime", "auto_upgrades", "reboot_required"],
    }


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
        """The section, only when it exited 0 AND has the shape its command prints."""
        sec = self.s(probe, name)
        return sec if P.ok(sec) and P.well_formed(name, sec["lines"]) else None

    def malformed(self, probe):
        return sorted(n for n, s in self.sec.get(probe, {}).items() if P.ok(s) and not P.well_formed(n, s["lines"]))

    def sql(self, probe, sep):
        r = self.raw.get(probe) or {}
        if r.get("rc") != 0:
            return None
        rows = P.tagged_rows(r.get("stdout", ""), sep)
        return rows if P.sql_well_formed(probe, rows) else None

    def _containers(self):
        ps, ins = self.ok("docker_engine", "docker_ps"), self.ok("docker_engine", "docker_inspect")
        if ps is None or ins is None:
            return None
        rows, _ = P.json_lines(ps["lines"])
        by_id = {d["id"]: d for d in P.json_lines(ins["lines"])[0]}
        if any(r["id"] not in by_id for r in rows):
            # A container listed by ps with no inspect record: its health/OOM/ports are unknown.
            return None
        for r in rows:
            r["inspect"] = by_id[r["id"]]
        return sorted(rows, key=lambda r: r["name"])

    def _windows(self):
        r = self.raw.get("windows_host") or {}
        if r.get("rc") != 0:
            return None
        try:
            obj = json.loads(r.get("stdout", "").strip())
        except ValueError:
            return None
        return obj if P.windows_well_formed(obj) else None


# ------------------------------------------------------------------ collection
def check_collection(c: Ctx) -> list[dict]:
    out = []
    exp_sec = expected_sections(c.cfg)
    expected_probes = ["linux_host", "windows_host", "docker_engine", "docker_vm", "ubuntu_root", "postgres", "clickhouse"]
    for pid in expected_probes:
        if pid not in c.raw:
            out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} missing from run", "UNKNOWN",
                               value={"rc": None, "missing_probe": True},
                               evidence=[ev(pid, "status", f"probe {pid} not found in raw.json")]))
            continue
        r = c.raw[pid]
        if pid in exp_sec:
            parsed_sec = c.sec.get(pid, {})
            missing = [s for s in exp_sec[pid] if s not in parsed_sec]
            failed = sorted(n for n, s in parsed_sec.items() if s.get("rc") != 0)
            malformed = c.malformed(pid)
            if r.get("rc") != 0 or failed or missing or malformed:
                val = {"rc": r.get("rc")}
                if failed:
                    val["failed_sections"] = failed
                if missing:
                    val["missing_sections"] = missing
                if malformed:
                    val["malformed_sections"] = malformed
                err = (r.get("stderr") or "").strip()
                evid_line = err[:300] if err else f"rc={r.get('rc')} failed={failed} missing={missing} malformed={malformed}"
                out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} incomplete", "UNKNOWN",
                                   value=val, evidence=[ev(pid, "stderr" if err else "status", evid_line)]))
        else:
            failed = (r.get("rc") != 0 or
                      (pid == "windows_host" and c.win is None) or
                      (pid == "postgres" and c.sql("postgres", "|") is None) or
                      (pid == "clickhouse" and c.sql("clickhouse", "\t") is None))
            if failed:
                err = (r.get("stderr") or "").strip()
                head = (r.get("stdout") or "").strip()[:200]
                evid_line = err[:300] if err else f"rc={r.get('rc')} output not in expected shape: {head!r}"
                out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} incomplete", "UNKNOWN",
                                   value={"rc": r.get("rc")}, evidence=[ev(pid, "stderr" if err else "status", evid_line)]))
    return out


# ------------------------------------------------------------------ docker
def check_docker(c: Ctx) -> list[dict]:
    if c.containers is None:
        if c.ok("docker_engine", "docker_ps") is None:
            failed_sec, sec_obj = "docker_ps", c.s("docker_engine", "docker_ps")
        elif c.ok("docker_engine", "docker_inspect") is None:
            failed_sec, sec_obj = "docker_inspect", c.s("docker_engine", "docker_inspect")
        else:
            failed_sec, sec_obj = "docker_inspect", {"rc": 0, "lines": ["a container listed by docker ps has no docker inspect record"]}
        return [
            unknown("DOCKER-LIVENESS", "docker", "Container liveness", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-HEALTH", "docker", "Healthcheck reports unhealthy", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-OOM", "docker", "Container last exit was OOM kill", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-RESTARTS", "docker", "Restarts since baseline", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-LOG-ROTATION", "docker", "json-file logs without max-size", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-RW-LAYER", "storage", "Container writable layer over threshold", "docker_engine", failed_sec, sec_obj),
            unknown("DOCKER-LOG-SIZE", "storage", "Container json log size", "docker_engine", failed_sec, sec_obj),
            unknown("SEC-DOCKER-PRIV", "security", "Privileged container or docker.sock mount", "docker_engine", failed_sec, sec_obj),
            unknown("SEC-DB-PORTS", "security", "Datastore port published on all interfaces", "docker_engine", failed_sec, sec_obj),
        ]
    return [_liveness(c), _health(c), _oom(c), _restarts(c), _log_rotation(c), _rw_layers(c),
            _log_sizes(c), _privileged(c), _db_ports(c)]


def _each(c, section, fmt):
    """Evidence for an all-clear finding: the per-container values it was derived from."""
    if not c.containers:
        return [ev("docker_engine", section, "docker ps listed 0 containers")]
    return [ev("docker_engine", section, fmt(r)) for r in c.containers[:5]]


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
                   evidence=evid or _each(c, "docker_ps", lambda r: f"{r['name']}|{r['state']}|{r['status']}"),
                   verified=verified, note=note)


def _health(c):
    bad = [r for r in c.containers if r["inspect"].get("health") == "unhealthy"]
    if bad:
        evid = [ev("docker_engine", "docker_ps", f"{r['name']}|{r['status']}") for r in bad]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} health={r['inspect'].get('health') or 'none'}")
    return finding("DOCKER-HEALTH", "docker", "Healthcheck reports unhealthy", "WARN" if bad else "OK",
                   value=[r["name"] for r in bad],
                   evidence=evid)


def _oom(c):
    bad = [r for r in c.containers if r["inspect"].get("oom_killed")]
    if bad:
        evid = [ev("docker_engine", "docker_inspect", f"{r['name']} oom_killed=true") for r in bad]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} oom_killed=false")
    return finding("DOCKER-OOM", "docker", "Container last exit was OOM kill", "CRIT" if bad else "OK",
                   value=[r["name"] for r in bad],
                   evidence=evid)


def _restarts(c):
    cur = {r["name"]: r["inspect"].get("restart_count", 0) for r in c.containers}
    prev = c.baseline.get("restart_counts")
    if prev is None:
        nonzero = {n: v for n, v in cur.items() if v}
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} restart_count={cur[r['name']]}")
        return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "INFO", value=nonzero,
                       evidence=evid,
                       note="no baseline: cumulative RestartCount shown; weekly delta starts next run")
    # RestartCount resets when a container is re-created, so a smaller value counts as all-new restarts.
    deltas = {n: (v - prev[n] if v >= prev[n] else v) for n, v in cur.items() if n in prev}
    deltas = {n: d for n, d in deltas.items() if d > 0}
    if deltas:
        evid = [ev("docker_engine", "docker_inspect", f"{n} restart_count={cur[n]} baseline={prev[n]}") for n in deltas]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} restart_count={cur[r['name']]} baseline={prev.get(r['name'])}")
    return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "WARN" if deltas else "OK",
                   value=deltas, evidence=evid)


def _log_rotation(c):
    bad = [r["name"] for r in c.containers
           if r["inspect"].get("log_type") == "json-file" and "max-size" not in (r["inspect"].get("log_opts") or {})]
    if bad:
        evid = [ev("docker_engine", "docker_inspect", f"{n} log_opts has no max-size") for n in bad[:5]]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} log_type={r['inspect'].get('log_type')} "
                     f"max-size={(r['inspect'].get('log_opts') or {}).get('max-size')}")
    return finding("DOCKER-LOG-ROTATION", "docker", "json-file logs without max-size", "WARN" if bad else "OK",
                   value={"count": len(bad), "containers": bad},
                   evidence=evid)


def _rw_layers(c):
    limit = c.t["container_rw_mb_warn"] * 1e6
    big = []
    for r in c.containers:
        size = P.human_bytes((r.get("size") or "").split(" ", 1)[0])
        if size is not None and size >= limit:
            big.append((r["name"], size, r["size"]))
    if big:
        evid = [ev("docker_engine", "docker_ps", f"{n} size={raw}") for n, _, raw in big]
    else:
        evid = _each(c, "docker_ps", lambda r: f"{r['name']} size={r.get('size') or '0B'}")
    return finding("DOCKER-RW-LAYER", "storage", "Container writable layer over threshold", "WARN" if big else "OK",
                   value={n: gb(s) for n, s, _ in big}, threshold=f">= {c.t['container_rw_mb_warn']} MB",
                   evidence=evid)


def _log_sizes(c):
    sec = c.s("docker_vm", "container_logs")
    if c.ok("docker_vm", "container_logs") is None:
        return unknown("DOCKER-LOG-SIZE", "storage", "Container json log size", "docker_vm", "container_logs", sec)
    by_file = {posixpath.basename(p): v for p, v in P.du_rows(sec["lines"]).items()}
    limit = c.t["container_log_mb_warn"] * 1e6
    big = []
    for r in c.containers:
        row = by_file.get(posixpath.basename(r["inspect"].get("log_path") or ""))
        if row and row["bytes"] >= limit:
            big.append((r["name"], row))
    total = sum(v["bytes"] for v in by_file.values())
    if big:
        evid = [ev("docker_vm", "container_logs", row["raw"]) for _, row in big]
    else:
        evid = [ev("docker_vm", "container_logs", ln) for ln in sec["lines"][:5]] if sec["lines"] else [
            ev("docker_vm", "container_logs", "no container log files found")
        ]
    return finding("DOCKER-LOG-SIZE", "storage", "Container json log over threshold", "WARN" if big else "OK",
                   value={"total_gb": gb(total), "over_gb": {n: gb(row["bytes"]) for n, row in big}},
                   threshold=f">= {c.t['container_log_mb_warn']} MB",
                   evidence=evid)


def _privileged(c):
    bad = []
    for r in c.containers:
        i = r["inspect"]
        sock = any(m.get("source") in ("/var/run/docker.sock", "/run/docker.sock") for m in i.get("mounts", []))
        if i.get("privileged") or sock:
            bad.append(f"{r['name']} privileged={i.get('privileged')} docker_sock={sock}")
    if bad:
        evid = [ev("docker_engine", "docker_inspect", b) for b in bad]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} privileged=false docker_sock=false")
    return finding("SEC-DOCKER-PRIV", "security", "Privileged container or docker.sock mount", "WARN" if bad else "OK",
                   value=bad, evidence=evid)


def _db_ports(c):
    exposed = []
    for r in c.containers:
        for port, binds in sorted((r["inspect"].get("ports") or {}).items()):
            for b in binds or []:
                if port.split("/", 1)[0] in DB_PORTS and b.get("HostIp", "") in ("", "0.0.0.0", "::"):
                    exposed.append(f"{r['name']} {port} -> {b.get('HostIp') or '*'}:{b.get('HostPort')}")
    if exposed:
        evid = [ev("docker_engine", "docker_inspect", e) for e in exposed]
    else:
        evid = _each(c, "docker_inspect", lambda r: f"{r['name']} ports={sorted((r['inspect'].get('ports') or {}).keys())}")
    return finding("SEC-DB-PORTS", "security", "Datastore port published on all interfaces",
                   "WARN" if exposed else "OK", value=exposed,
                   evidence=evid)
