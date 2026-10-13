"""Deterministic evaluation: raw probe output + config + optional baseline -> findings.

Every finding names the sources it needs, primary and cross-check. If any of them failed, is
missing or is malformed, the finding is UNKNOWN and cites the raw lines that show why; it is never
estimated, filled from an older run, or reported with an unverified cross-check. Every evidence
item is an exact line of the stored raw output plus its locator (see ``sentinel_evidence``).
"""
from __future__ import annotations

import json
import posixpath

import sentinel_evidence as E
import sentinel_parse as P
from sentinel_probes import DB_PORTS

ORDER = {"CRIT": 0, "WARN": 1, "UNKNOWN": 2, "INFO": 3, "OK": 4}
GB = 1e9
BASH_PROBES = E.BASH_PROBES
PROBES = ("linux_host", "windows_host", "docker_engine", "docker_vm", "ubuntu_root", "postgres", "clickhouse")
# Sources a finding can require: (probe, section) for framed shell output, (probe, None) for a
# structured probe, and CONTAINERS for `docker ps` joined one-to-one with `docker inspect`.
CONTAINERS = ("docker_engine", "*containers")
WIN, PG, CH = ("windows_host", None), ("postgres", None), ("clickhouse", None)
DOCKER_INFO = ("docker_engine", "docker_info")


def finding(fid, area, title, status, value=None, threshold=None, evidence=(), verified=None, note=""):
    evid = [e for e in evidence if e and e.get("line")]
    if not evid:
        # Spec: every finding cites the raw probe/section line it was derived from; never synthesise one.
        raise ValueError(f"finding {fid}: no evidence line")
    return {"id": fid, "area": area, "title": title, "status": status, "value": value,
            "threshold": threshold, "verified": verified, "note": note, "evidence": evid}


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
    """Parsed, shape-checked view of one run's raw probe output."""

    def __init__(self, cfg: dict, raw: dict[str, dict], baseline: dict | None):
        self.cfg, self.raw, self.baseline = cfg, raw, baseline or {}
        self.t = cfg["thresholds"]
        self.sec = {pid: P.sections((raw.get(pid) or {}).get("stdout", "")) for pid in BASH_PROBES}
        self.containers, self.unjoined = self._containers()
        self.win, self.win_line = self._windows()
        self.pg, self.ch = self._sql("postgres"), self._sql("clickhouse")

    # ---------------------------------------------------------------- sections and payloads
    def s(self, probe, name):
        return self.sec.get(probe, {}).get(name)

    def ok(self, probe, name):
        """The section, only when it exited 0 AND has the shape its command prints."""
        sec = self.s(probe, name)
        return sec if P.ok(sec) and P.well_formed(name, sec["lines"]) else None

    def malformed(self, probe):
        return sorted(n for n, s in self.sec.get(probe, {}).items() if P.ok(s) and not P.well_formed(n, s["lines"]))

    def sql(self, probe):
        return {"postgres": self.pg, "clickhouse": self.ch}[probe]

    def _sql(self, probe):
        r = self.raw.get(probe) or {}
        if r.get("rc") != 0:
            return None
        rows = P.sql_rows(r.get("stdout", ""), E.SQL_SEP[probe])
        return rows if P.sql_well_formed(probe, rows) else None

    def _containers(self):
        ps, ins = self.ok("docker_engine", "docker_ps"), self.ok("docker_engine", "docker_inspect")
        if ps is None or ins is None:
            return None, []
        by_id = {o["id"]: (ln, o) for ln, o in P.json_records(ins["lines"])}
        rows, unjoined = [], []
        for ln, o in P.json_records(ps["lines"]):
            if o["id"] not in by_id:
                unjoined.append(ln)
                continue
            iln, io = by_id[o["id"]]
            rows.append({**o, "ps_line": ln, "inspect": io, "inspect_line": iln})
        if unjoined:
            # A container listed by ps with no inspect record: its health/OOM/ports are unknown.
            return None, unjoined
        return sorted(rows, key=lambda r: r["name"]), []

    def _windows(self):
        r = self.raw.get("windows_host") or {}
        if r.get("rc") != 0:
            return None, None
        lines = P.body(P.raw_lines(r.get("stdout", "")))
        good, _ = P.json_lines(lines[:1])
        if len(lines) != 1 or not good or not P.windows_well_formed(good[0]):
            return None, None
        return good[0], lines[0]

    # ---------------------------------------------------------------- evidence
    def cite(self, probe, section, line):
        n = E.locate(self.raw, probe, section, line)
        if n is None:
            raise ValueError(f"evidence {probe}/{section}: {line[:80]!r} is not a raw line of that section")
        return {"probe": probe, "section": section, "line_no": n, "line": line}

    def cite_all(self, probe, section, lines):
        return [self.cite(probe, section, ln) for ln in P.body(lines)]

    def frame(self, probe, name):
        """The ``##rc name 0`` line: raw proof that a section ran and printed nothing."""
        return self.cite(probe, name, self.s(probe, name)["rc_line"])

    def win_ev(self):
        return self.cite("windows_host", E.JSON, self.win_line)

    def row_ev(self, probe, tag):
        return [self.cite(probe, tag, e["raw"]) for e in self.sql(probe).get(tag, [])]

    @staticmethod
    def status(probe, subject, msg):
        """No raw line exists (probe or section absent). Only ever backs UNKNOWN or CRIT."""
        return {"probe": probe, "section": E.STATUS, "subject": subject, "line_no": None, "line": msg}

    def unavailable(self, *srcs) -> list[dict]:
        """Raw evidence for every listed source that cannot be used; [] when all are usable."""
        return [e for src in srcs for e in self._why(src)]

    def _why(self, src):
        probe, name = src
        if src == CONTAINERS:
            return (self._why(("docker_engine", "docker_ps")) + self._why(("docker_engine", "docker_inspect"))
                    or [self.cite("docker_engine", "docker_ps", ln) for ln in self.unjoined])
        if probe not in self.raw:
            return [self.status(probe, name, f"probe {probe} missing from raw.json")]
        if name is None:
            return self._probe_why(probe)
        sec = self.s(probe, name)
        if sec is None:
            return [self.status(probe, name, f"section {name} missing from {probe} output (probe rc={self.raw[probe].get('rc')})")]
        if self.ok(probe, name):
            return []
        block = P.raw_lines(self.raw[probe]["stdout"])[sec["start"]:sec["end"]]
        keep = block[:4] + ([sec["rc_line"]] if "rc_line" in sec and len(block) > 4 else [])
        return [self.cite(probe, name, ln) for ln in keep if ln.strip()]

    def _probe_why(self, probe):
        if {"windows_host": self.win, "postgres": self.pg, "clickhouse": self.ch}[probe] is not None:
            return []
        r = self.raw[probe]
        ev = [self.cite(probe, E.STDERR, ln) for ln in P.body(P.raw_lines(r.get("stderr") or ""))[:3]]
        ev += [self.cite(probe, E.STDOUT, ln) for ln in P.body(P.raw_lines(r.get("stdout") or ""))[:3]]
        return ev or [self.status(probe, None, f"probe {probe} rc={r.get('rc')} printed nothing")]


def unknown(fid, area, title, evidence, note="a required source failed, is missing or is malformed"):
    return finding(fid, area, title, "UNKNOWN", note=note, evidence=evidence)


def gated(c: Ctx, fid, area, title, srcs, compute):
    """``compute(c)`` only when every required source is usable; otherwise UNKNOWN with the why."""
    bad = c.unavailable(*srcs)
    return unknown(fid, area, title, bad) if bad else compute(c)


# ------------------------------------------------------------------ collection
def _collect_bash(c: Ctx, pid: str, exp: list[str]):
    r = c.raw[pid]
    parsed = c.sec.get(pid, {})
    missing = [s for s in exp if s not in parsed]
    failed = sorted(n for n, s in parsed.items() if s.get("rc") != 0)
    malformed = c.malformed(pid)
    if r.get("rc") == 0 and not (failed or missing or malformed):
        return None
    val = {"rc": r.get("rc")}
    for k, v in (("failed_sections", failed), ("missing_sections", missing), ("malformed_sections", malformed)):
        if v:
            val[k] = v
    evid = [e for n in failed + malformed for e in c.unavailable((pid, n))]
    evid += [c.status(pid, n, f"section {n} missing from {pid} output") for n in missing]
    evid += [c.cite(pid, E.STDERR, ln) for ln in P.body(P.raw_lines(r.get("stderr") or ""))[:3]]
    evid = evid or [c.status(pid, None, f"probe {pid} exited rc={r.get('rc')}")]
    return finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} incomplete", "UNKNOWN", value=val, evidence=evid)


def check_collection(c: Ctx) -> list[dict]:
    out = []
    exp_sec = expected_sections(c.cfg)
    for pid in PROBES:
        if pid not in c.raw:
            out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} missing from run", "UNKNOWN",
                               value={"rc": None, "missing_probe": True},
                               evidence=[c.status(pid, None, f"probe {pid} missing from raw.json")]))
        elif pid in exp_sec:
            f = _collect_bash(c, pid, exp_sec[pid])
            if f:
                out.append(f)
        else:
            bad = c.unavailable((pid, None))
            if bad:
                out.append(finding(f"COLLECT-{pid.upper()}", "collection", f"Probe {pid} incomplete", "UNKNOWN",
                                   value={"rc": c.raw[pid].get("rc")}, evidence=bad))
    return out


# ------------------------------------------------------------------ docker
def _cite_rows(c: Ctx, rows, kind):
    """The raw ``docker ps`` (kind="ps") or ``docker inspect`` line of each row; all rows when none is flagged."""
    rows = rows or c.containers
    if not rows:
        return [c.frame("docker_engine", "docker_ps"), c.frame("docker_engine", "docker_inspect")]
    section = "docker_ps" if kind == "ps" else "docker_inspect"
    return [c.cite("docker_engine", section, r[f"{kind}_line"]) for r in rows]


def _liveness(c):
    down = [r for r in c.containers if r["state"] != "running"]
    running = len(c.containers) - len(down)
    line = P.body(c.ok(*DOCKER_INFO)["lines"])[0]
    info = json.loads(line)
    verified = info["running"] == running and info["total"] == len(c.containers)
    return finding("DOCKER-LIVENESS", "docker", "Containers not running", "CRIT" if down else "OK",
                   value={"running": running, "total": len(c.containers), "down": [r["name"] for r in down]},
                   evidence=_cite_rows(c, down, "ps") + [c.cite(*DOCKER_INFO, line)], verified=verified,
                   note=f"docker info running={info['running']} total={info['total']}; "
                        f"ps running={running} total={len(c.containers)}")


def _flagged(c, fid, area, title, pred, bad_status, value):
    bad = [r for r in c.containers if pred(r["inspect"])]
    return finding(fid, area, title, bad_status if bad else "OK", value=value(bad), evidence=_cite_rows(c, bad, "inspect"))


def _health(c):
    return _flagged(c, "DOCKER-HEALTH", "docker", "Healthcheck reports unhealthy",
                    lambda i: i["health"] == "unhealthy", "WARN", lambda bad: [r["name"] for r in bad])


def _oom(c):
    return _flagged(c, "DOCKER-OOM", "docker", "Container last exit was OOM kill",
                    lambda i: i["oom_killed"], "CRIT", lambda bad: [r["name"] for r in bad])


def _log_rotation(c):
    return _flagged(c, "DOCKER-LOG-ROTATION", "docker", "json-file logs without max-size",
                    lambda i: i["log_type"] == "json-file" and "max-size" not in (i["log_opts"] or {}), "WARN",
                    lambda bad: {"count": len(bad), "containers": [r["name"] for r in bad]})


def _sock(i):
    return any(m["source"] in ("/var/run/docker.sock", "/run/docker.sock") for m in i["mounts"])


def _privileged(c):
    return _flagged(c, "SEC-DOCKER-PRIV", "security", "Privileged container or docker.sock mount",
                    lambda i: i["privileged"] or _sock(i), "WARN",
                    lambda bad: [f"{r['name']} privileged={r['inspect']['privileged']} docker_sock={_sock(r['inspect'])}" for r in bad])


def _exposed(r):
    return [f"{r['name']} {port} -> {b['HostIp'] or '*'}:{b['HostPort']}"
            for port, binds in sorted((r["inspect"]["ports"] or {}).items()) for b in binds or []
            if port.split("/", 1)[0] in DB_PORTS and b["HostIp"] in ("", "0.0.0.0", "::")]


def _db_ports(c):
    bad = [r for r in c.containers if _exposed(r)]
    return finding("SEC-DB-PORTS", "security", "Datastore port published on all interfaces", "WARN" if bad else "OK",
                   value=[e for r in bad for e in _exposed(r)], evidence=_cite_rows(c, bad, "inspect"))


def _restarts(c):
    cur = {r["name"]: r["inspect"]["restart_count"] for r in c.containers}
    prev = c.baseline.get("restart_counts")
    if prev is None:
        return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "INFO",
                       value={n: v for n, v in cur.items() if v}, evidence=_cite_rows(c, [], "inspect"),
                       note="no baseline: cumulative RestartCount shown; weekly delta starts next run")
    # RestartCount resets when a container is re-created, so a smaller value counts as all-new restarts.
    deltas = {n: (v - prev[n] if v >= prev[n] else v) for n, v in cur.items() if n in prev}
    deltas = {n: d for n, d in deltas.items() if d > 0}
    flagged = [r for r in c.containers if r["name"] in deltas]
    return finding("DOCKER-RESTARTS", "docker", "Restarts since baseline", "WARN" if deltas else "OK",
                   value=deltas, evidence=_cite_rows(c, flagged, "inspect"),
                   note="baseline restart_counts from the previous run's baseline.json")


def _rw_layers(c):
    limit = c.t["container_rw_mb_warn"] * 1e6
    big = [r for r in c.containers if (P.human_bytes(r["size"].split(" ", 1)[0]) or 0) >= limit]
    return finding("DOCKER-RW-LAYER", "storage", "Container writable layer over threshold", "WARN" if big else "OK",
                   value={r["name"]: gb(P.human_bytes(r["size"].split(" ", 1)[0])) for r in big},
                   threshold=f">= {c.t['container_rw_mb_warn']} MB", evidence=_cite_rows(c, big, "ps"))


def _log_sizes(c):
    sec = c.ok("docker_vm", "container_logs")
    by_file = {posixpath.basename(p): v for p, v in P.du_rows(sec["lines"]).items()}
    limit = c.t["container_log_mb_warn"] * 1e6
    big = [(r, by_file[posixpath.basename(r["inspect"]["log_path"])]) for r in c.containers
           if by_file.get(posixpath.basename(r["inspect"]["log_path"]), {}).get("bytes", 0) >= limit]
    total = sum(v["bytes"] for v in by_file.values())
    if big:
        evid = [c.cite("docker_vm", "container_logs", row["raw"]) for _, row in big] + _cite_rows(c, [r for r, _ in big], "inspect")
    else:
        evid = c.cite_all("docker_vm", "container_logs", sec["lines"])
    return finding("DOCKER-LOG-SIZE", "storage", "Container json log over threshold", "WARN" if big else "OK",
                   value={"total_gb": gb(total), "over_gb": {r["name"]: gb(row["bytes"]) for r, row in big}},
                   threshold=f">= {c.t['container_log_mb_warn']} MB", evidence=evid)


DOCKER_CHECKS = (
    ("DOCKER-LIVENESS", "docker", "Containers not running", (CONTAINERS, DOCKER_INFO), _liveness),
    ("DOCKER-HEALTH", "docker", "Healthcheck reports unhealthy", (CONTAINERS,), _health),
    ("DOCKER-OOM", "docker", "Container last exit was OOM kill", (CONTAINERS,), _oom),
    ("DOCKER-RESTARTS", "docker", "Restarts since baseline", (CONTAINERS,), _restarts),
    ("DOCKER-LOG-ROTATION", "docker", "json-file logs without max-size", (CONTAINERS,), _log_rotation),
    ("DOCKER-RW-LAYER", "storage", "Container writable layer over threshold", (CONTAINERS,), _rw_layers),
    ("DOCKER-LOG-SIZE", "storage", "Container json log size", (CONTAINERS, ("docker_vm", "container_logs")), _log_sizes),
    ("SEC-DOCKER-PRIV", "security", "Privileged container or docker.sock mount", (CONTAINERS,), _privileged),
    ("SEC-DB-PORTS", "security", "Datastore port published on all interfaces", (CONTAINERS,), _db_ports),
)


def check_docker(c: Ctx) -> list[dict]:
    return [gated(c, fid, area, title, srcs, fn) for fid, area, title, srcs, fn in DOCKER_CHECKS]
