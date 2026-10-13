"""Storage, datastore, host-resource and security checks (same contract as sentinel_checks).

Each check declares its required sources and runs through ``gated``: a failed, missing or
malformed primary OR cross-check source makes the finding UNKNOWN, never OK/INFO unverified.
"""
from __future__ import annotations

import datetime as dt

import sentinel_parse as P
from sentinel_checks import CH, CONTAINERS, PG, WIN, Ctx, finding, gated, gb, level, unknown

LDF, VHDX_STAT, DATA_DU = ("linux_host", "df"), ("linux_host", "vhdx_stat"), ("ubuntu_root", "data_du")
INNER_DF = {"docker_disk": ("docker_vm", "df"), "ubuntu_root": ("ubuntu_root", "df")}


def _pct(used, size):
    return round(100.0 * used / size, 1) if size else None


def unknown_ev(c: Ctx, src, sec):
    """Every raw line of a usable section (or its ``##rc`` frame when it printed nothing)."""
    return c.cite_all(*src, sec["lines"]) or [c.frame(*src)]


# ------------------------------------------------------------------ storage
def check_storage(c: Ctx) -> list[dict]:
    out = [_disk(c, mount) for mount in c.cfg["linux_fs"]]
    out += [_vhdx(c, v) for v in c.cfg["vhdx"]]
    out.append(gated(c, "STORAGE-ORPHANS", "storage", "Unmounted dirs under volume root",
                     (("ubuntu_root", "volumes_du"), CONTAINERS), _orphans))
    out.append(gated(c, "DB-ZK-AUTOPURGE", "db", "Zookeeper autopurge",
                     (("docker_engine", "zookeeper_env"), DATA_DU), _zookeeper))
    return out


def _disk(c: Ctx, mount: str) -> dict:
    fid = "STORAGE-DISK-" + (mount.strip("/").replace("/", "-").upper() or "ROOT")
    title = f"Disk usage {mount}"
    drive = c.cfg.get("drive_cross_check", {}).get(mount)
    srcs = (LDF, WIN) if drive else (LDF,)

    def compute(c):
        row = P.df_rows(c.ok(*LDF)["lines"]).get(mount)
        if row is None:
            return unknown(fid, "storage", title, unknown_ev(c, LDF, c.ok(*LDF)), note=f"df has no row for {mount}")
        pct = _pct(row["used"], row["size"])
        evid, verified, note = [c.cite(*LDF, row["raw"])], None, ""
        if drive:
            disk = next((d for d in c.win["disks"] if d["id"] == drive), None)
            if disk is None:
                return unknown(fid, "storage", title, evid + [c.win_ev()],
                               note=f"cross-check source Win32_LogicalDisk has no {drive}")
            # Two reads seconds apart on a live disk: agree within 2 GB.
            diff = abs(disk["free"] - row["avail"])
            verified, evid = diff <= 2e9, evid + [c.win_ev()]
            note = f"Windows {drive} free={gb(disk['free'])} GB vs df avail={gb(row['avail'])} GB (diff {gb(diff)} GB)"
        return finding(fid, "storage", title, level(pct, c.t["disk_used_pct_warn"], c.t["disk_used_pct_crit"]),
                       value={"used_pct": pct, "free_gb": gb(row["avail"]), "size_gb": gb(row["size"])},
                       threshold=f"warn {c.t['disk_used_pct_warn']}% / crit {c.t['disk_used_pct_crit']}%",
                       evidence=evid, verified=verified, note=note)
    return gated(c, fid, "storage", title, srcs, compute)


def _vhdx(c: Ctx, v: dict) -> dict:
    fid, title = f"STORAGE-VHDX-{v['name'].upper()}", f"VHDX slack {v['name']}"
    inner_src = INNER_DF[v["inner"]]
    mount = c.cfg["docker_disk_mount"] if v["inner"] == "docker_disk" else "/"

    def compute(c):
        st = P.stat_rows(c.ok(*VHDX_STAT)["lines"]).get(v["wsl_path"])
        row = P.df_rows(c.ok(*inner_src)["lines"]).get(mount)
        win = next((x for x in c.win["vhdx"] if x["path"] == v["windows_path"]), None)
        missing = ([] if st else unknown_ev(c, VHDX_STAT, c.ok(*VHDX_STAT))) + \
                  ([] if row else unknown_ev(c, inner_src, c.ok(*inner_src))) + ([] if win else [c.win_ev()])
        if missing:
            return unknown(fid, "storage", title, missing,
                           note=f"stat={'ok' if st else 'missing'} inner df={'ok' if row else 'missing'} "
                                f"Get-Item={'ok' if win else 'missing'}")
        slack = st["bytes"] - row["used"]
        return finding(fid, "storage", title, level(slack / 1e9, c.t["vhdx_slack_gb_warn"]),
                       value={"file_gb": gb(st["bytes"]), "used_inside_gb": gb(row["used"]), "slack_gb": gb(slack)},
                       threshold=f"slack >= {c.t['vhdx_slack_gb_warn']} GB",
                       evidence=[c.cite(*VHDX_STAT, st["raw"]), c.cite(*inner_src, row["raw"]), c.win_ev()],
                       verified=abs(win["bytes"] - st["bytes"]) <= 1e9,
                       note=f"slack = VHDX file length - filesystem used inside; Windows Get-Item length={win['bytes']}; "
                            "reclaim needs a maintenance window (compaction is never automated)")
    return gated(c, fid, "storage", title, (VHDX_STAT, inner_src, WIN), compute)


def _orphans(c):
    sec = c.ok("ubuntu_root", "volumes_du")
    sources = [m["source"].rstrip("/") for r in c.containers for m in r["inspect"]["mounts"] if m["source"]]
    orphans = {p: r for p, r in sorted(P.du_rows(sec["lines"]).items())
               if not any(s == p or s.startswith(p + "/") or p.startswith(s + "/") for s in sources)}
    evid = [c.cite("ubuntu_root", "volumes_du", r["raw"]) for r in orphans.values()] if orphans else \
        unknown_ev(c, ("ubuntu_root", "volumes_du"), sec)
    return finding("STORAGE-ORPHANS", "storage", "Dirs under volume root no container mounts",
                   "INFO" if orphans else "OK",
                   value={"total_gb": gb(sum(r["bytes"] for r in orphans.values())),
                          "dirs_mb": {p: round(r["bytes"] / 1e6, 1) for p, r in orphans.items()}},
                   evidence=evid, note="report only: retiring a stack is an owner decision")


def _zookeeper(c):
    src = ("docker_engine", "zookeeper_env")
    lines = c.ok(*src)["lines"]
    interval = P.kv_rows(lines, "=").get("purge_interval")
    dl = P.du_rows(c.ok(*DATA_DU)["lines"]).get(c.cfg["zookeeper"]["datalog_host_path"])
    if dl is None:
        return unknown("DB-ZK-AUTOPURGE", "db", "Zookeeper autopurge", unknown_ev(c, DATA_DU, c.ok(*DATA_DU)),
                       note=f"data_du has no row for {c.cfg['zookeeper']['datalog_host_path']}")
    off = interval in ("unset", "0", "")
    return finding("DB-ZK-AUTOPURGE", "db", "Zookeeper autopurge disabled", "WARN" if off else "OK",
                   value={"purge_interval": interval, "snap_retain": P.kv_rows(lines, "=").get("snap_retain"),
                          "datalog_gb": gb(dl["bytes"])},
                   evidence=c.cite_all(*src, lines) + [c.cite(*DATA_DU, dl["raw"])])


# ------------------------------------------------------------------ datastores
def check_db(c: Ctx) -> list[dict]:
    return [*_postgres(c), *_clickhouse(c), *_redis(c)]


def _up(c, probe, fid, title):
    """Unreachable (probe rc != 0) is CRIT; missing or malformed output is UNKNOWN."""
    bad = c.unavailable((probe, None))
    r = c.raw.get(probe)
    status = "CRIT" if r is not None and r.get("rc") != 0 else "UNKNOWN"
    return finding(fid, "db", title, status, evidence=bad, note="query failed, unreachable or output malformed")


def _sized(c, fid, title, probe, tag_ev, total, path, value):
    du = P.du_rows(c.ok(*DATA_DU)["lines"]).get(path)
    if du is None:
        return unknown(fid, "db", title, unknown_ev(c, DATA_DU, c.ok(*DATA_DU)), note=f"data_du has no row for {path}")
    return finding(fid, "db", title, "INFO", value=value, verified=total <= du["bytes"] * 1.05,
                   evidence=tag_ev + [c.cite(*DATA_DU, du["raw"])],
                   note="cross-check: database bytes must not exceed du of the data dir (+5%)")


def _one(c, probe, tag):
    return c.sql(probe)[tag][0]["cols"]


def _postgres(c):
    if c.pg is None:
        return [_up(c, "postgres", "DB-PG-UP", "Postgres reachable read-only"),
                *[gated(c, fid, "db", t, (PG,), None) for fid, t in (
                    ("DB-PG-CONN", "Postgres connection usage"),
                    ("DB-PG-TX", "Postgres long or idle-in-transaction sessions"),
                    ("DB-PG-XID", "Postgres transaction-ID wraparound age"),
                    ("DB-PG-SIZE", "Postgres database sizes"))]]
    ro = _one(c, "postgres", "read_only")[0]
    out = [finding("DB-PG-UP", "db", "Postgres reachable read-only", "OK" if ro == "on" else "CRIT",
                   value={"default_transaction_read_only": ro}, evidence=c.row_ev("postgres", "read_only"))]
    used, mx = map(int, _one(c, "postgres", "conn"))
    pct = _pct(used, mx)
    out.append(finding("DB-PG-CONN", "db", "Postgres connection usage", level(pct, c.t["pg_conn_pct_warn"]),
                       value={"used": used, "max": mx, "pct": pct}, evidence=c.row_ev("postgres", "conn")))
    stuck = {k: int(_one(c, "postgres", k)[0]) for k in ("long_tx", "idle_in_tx")}
    out.append(finding("DB-PG-TX", "db", "Postgres long or idle-in-transaction sessions",
                       "WARN" if any(stuck.values()) else "OK", value=stuck,
                       evidence=c.row_ev("postgres", "long_tx") + c.row_ev("postgres", "idle_in_tx")))
    db, age = _one(c, "postgres", "xid_age")
    out.append(finding("DB-PG-XID", "db", "Postgres transaction-ID wraparound age",
                       level(int(age), c.t["pg_xid_age_warn"], c.t["pg_xid_age_crit"]), value={"db": db, "age": int(age)},
                       evidence=c.row_ev("postgres", "xid_age")))
    out.append(gated(c, "DB-PG-SIZE", "db", "Postgres database sizes", (DATA_DU,), _pg_size))
    return out


def _pg_size(c):
    sizes = {e["cols"][0]: int(e["cols"][1]) for e in c.pg["db"]}
    prev = c.baseline.get("pg_sizes") or {}
    top = dict(sorted(sizes.items(), key=lambda kv: -kv[1])[:5])
    return _sized(c, "DB-PG-SIZE", "Postgres database sizes", "postgres", c.row_ev("postgres", "db"),
                  sum(sizes.values()), c.cfg["postgres"]["data_host_path"],
                  {"total_gb": gb(sum(sizes.values())), "top_gb": {k: gb(v) for k, v in top.items()},
                   "growth_mb": {d: round((s - prev[d]) / 1e6, 1) for d, s in sizes.items() if d in prev and s != prev[d]}})


def _clickhouse(c):
    if c.ch is None:
        return [_up(c, "clickhouse", "DB-CH-UP", "ClickHouse reachable read-only"),
                *[gated(c, fid, "db", t, (CH,), None) for fid, t in (
                    ("DB-CH-LOG-TTL", "ClickHouse system log tables without TTL"),
                    ("DB-CH-SIZE", "ClickHouse database sizes"))]]
    ro = _one(c, "clickhouse", "readonly")[0]
    out = [finding("DB-CH-UP", "db", "ClickHouse reachable read-only", "OK" if ro == "1" else "CRIT",
                   value={"readonly": ro, "uptime_s": int(_one(c, "clickhouse", "uptime")[0])},
                   evidence=c.row_ev("clickhouse", "readonly") + c.row_ev("clickhouse", "uptime"))]
    rows = [e for e in c.ch.get("log_no_ttl", []) if e["cols"][1] != "\\N"]
    no_ttl = {e["cols"][0]: int(e["cols"][1]) for e in rows}
    big = sum(no_ttl.values())
    status = "WARN" if big >= c.t["ch_log_no_ttl_gb_warn"] * 1e9 else ("INFO" if no_ttl else "OK")
    # No log_no_ttl rows at all: the parts_total row proves the query batch ran to completion.
    evid = [c.cite("clickhouse", "log_no_ttl", e["raw"]) for e in rows] or c.row_ev("clickhouse", "parts_total")
    out.append(finding("DB-CH-LOG-TTL", "db", "ClickHouse system log tables without TTL", status,
                       threshold=f"no-TTL bytes >= {c.t['ch_log_no_ttl_gb_warn']} GB",
                       value={"tables": len(no_ttl), "total_gb": gb(big),
                              "top": dict(sorted(no_ttl.items(), key=lambda kv: -kv[1])[:5])},
                       evidence=evid))
    out.append(gated(c, "DB-CH-SIZE", "db", "ClickHouse database sizes", (DATA_DU,), _ch_size))
    return out


def _ch_size(c):
    parts = int(_one(c, "clickhouse", "parts_total")[0])
    dbs = {e["cols"][0]: int(e["cols"][1]) for e in c.ch.get("db", [])}
    prev = c.baseline.get("ch_sizes") or {}
    return _sized(c, "DB-CH-SIZE", "ClickHouse database sizes", "clickhouse",
                  c.row_ev("clickhouse", "parts_total") + c.row_ev("clickhouse", "db"),
                  parts, c.cfg["clickhouse"]["data_host_path"],
                  {"parts_gb": gb(parts), "dbs_gb": {k: gb(v) for k, v in dbs.items()},
                   "growth_mb": {d: round((s - prev[d]) / 1e6, 1) for d, s in dbs.items() if d in prev and s != prev[d]}})


def _redis(c):
    out = []
    for name in c.cfg["redis"]:
        fid, title = f"DB-REDIS-{name}", f"Redis {name} health"
        ping_src, pers_src, mem_src = (("docker_engine", f"redis_{k}:{name}") for k in ("ping", "persistence", "memory"))
        ping = c.s(*ping_src)
        if ping is not None and ping.get("rc") not in (0, None):
            out.append(finding(fid, "db", title, "CRIT", note="PING failed", evidence=c.unavailable(ping_src)))
            continue
        out.append(gated(c, fid, "db", title, (ping_src, pers_src, mem_src),
                         lambda c, n=name, s=(ping_src, pers_src, mem_src): _redis_ok(c, n, *s)))
    return out


def _redis_ok(c, name, ping_src, pers_src, mem_src):
    pers, mem = c.ok(*pers_src)["lines"], c.ok(*mem_src)["lines"]
    kv, mkv = P.kv_rows(pers), P.kv_rows(mem)
    keys = [k for k in ("rdb_last_bgsave_status", "aof_last_write_status") if k in kv]
    bad = [k for k in keys if kv[k] != "ok"]
    evid = c.cite_all(*ping_src, c.ok(*ping_src)["lines"])
    evid += [c.cite(*pers_src, P.kv_line(pers, k)) for k in keys]
    evid += [c.cite(*mem_src, P.kv_line(mem, k)) for k in ("used_memory", "maxmemory")]
    return finding(f"DB-REDIS-{name}", "db", f"Redis {name} health", "WARN" if bad else "OK",
                   value={"ping": "PONG", "aof_enabled": kv.get("aof_enabled"), "failed": bad,
                          "used_memory_mb": round(int(mkv["used_memory"]) / 1e6, 1), "maxmemory": mkv["maxmemory"]},
                   evidence=evid)


# ------------------------------------------------------------------ host
def check_host(c: Ctx) -> list[dict]:
    return [
        gated(c, "HOST-MEM-WSL", "host", "WSL memory available", (("linux_host", "meminfo"),), _mem_wsl),
        gated(c, "HOST-LOAD", "host", "5-min load per CPU", (("linux_host", "loadavg"), ("linux_host", "nproc")), _load),
        *([gated(c, "HOST-WINDOWS", "host", "Windows host facts", (WIN,), None)] if c.win is None else []),
        gated(c, "HOST-MEM-WIN", "host", "Windows physical memory free", (WIN,), _mem_win),
        gated(c, "HOST-REBOOT-WIN", "host", "Windows reboot pending", (WIN,), _reboot_win),
    ]


def _mem_wsl(c):
    src = ("linux_host", "meminfo")
    m = P.meminfo(c.ok(*src)["lines"])
    pct = _pct(m["MemAvailable"]["bytes"], m["MemTotal"]["bytes"])
    return finding("HOST-MEM-WSL", "host", "WSL memory available",
                   "WARN" if pct <= c.t["mem_available_pct_warn"] else "OK",
                   value={"available_pct": pct, "total_gb": gb(m["MemTotal"]["bytes"])},
                   threshold=f"<= {c.t['mem_available_pct_warn']}%",
                   evidence=[c.cite(*src, m["MemTotal"]["raw"]), c.cite(*src, m["MemAvailable"]["raw"])])


def _load(c):
    la, npr = c.ok("linux_host", "loadavg")["lines"], c.ok("linux_host", "nproc")["lines"]
    la, npr = P.body(la)[0], P.body(npr)[0]
    load5, cpus = float(la.split()[1]), int(npr.strip())
    per = round(load5 / cpus, 2)
    return finding("HOST-LOAD", "host", "5-min load per CPU", "WARN" if per >= c.t.get("load_per_cpu_warn", 1.0) else "OK",
                   value={"load5": load5, "cpus": cpus, "per_cpu": per},
                   evidence=[c.cite("linux_host", "loadavg", la), c.cite("linux_host", "nproc", npr)])


def _mem_win(c):
    w = c.win
    if w["mem_total_kb"] <= 0:
        return unknown("HOST-MEM-WIN", "host", "Windows physical memory free", [c.win_ev()], note="mem_total_kb is 0")
    pct = _pct(w["mem_free_kb"], w["mem_total_kb"])
    return finding("HOST-MEM-WIN", "host", "Windows physical memory free",
                   "WARN" if pct <= c.t["mem_available_pct_warn"] else "OK",
                   value={"free_pct": pct, "total_gb": gb(w["mem_total_kb"] * 1024)}, evidence=[c.win_ev()])


def _reboot_win(c):
    w = c.win
    return finding("HOST-REBOOT-WIN", "host", "Windows reboot pending", "WARN" if w["reboot_pending"] else "OK",
                   value={"reboot_pending": w["reboot_pending"], "uptime_days": round(w["uptime_s"] / 86400, 1)},
                   evidence=[c.win_ev()])


# ------------------------------------------------------------------ security
def check_security(c: Ctx) -> list[dict]:
    def ub(name):
        return ("ubuntu_root", name)
    return [
        gated(c, "SEC-SSHD", "security", "sshd effective auth settings (WSL Ubuntu)", (ub("sshd"),), _sshd),
        gated(c, "SEC-APT-UPDATES", "security", "Pending Ubuntu updates", (ub("apt_upgradable"),), _apt),
        gated(c, "SEC-APT-LISTS-AGE", "security", "apt package lists age",
              (ub("apt_cache_mtime"), ("linux_host", "epoch")), _apt_age),
        gated(c, "SEC-UNATTENDED", "security", "Ubuntu unattended-upgrades enabled", (ub("auto_upgrades"),), _unattended),
        gated(c, "SEC-WSL-REBOOT", "security", "Ubuntu reboot-required flag", (ub("reboot_required"),), _wsl_reboot),
        gated(c, "SEC-WIN-DEFENDER", "security", "Defender AV, real-time protection, signature age", (WIN,), _defender),
        gated(c, "SEC-WIN-FIREWALL", "security", "Windows firewall profiles enabled", (WIN,), _firewall),
        gated(c, "SEC-WIN-HOTFIX", "security", "Days since last Windows update", (WIN,), _hotfix),
    ]


def _section_finding(c, src, fid, title, status, value):
    return finding(fid, "security", title, status, value=value, evidence=unknown_ev(c, src, c.ok(*src)))


def _sshd(c):
    src = ("ubuntu_root", "sshd")
    s = P.sshd(c.ok(*src)["lines"])
    status = "CRIT" if s.get("permitemptypasswords") == "yes" else \
        "WARN" if s.get("passwordauthentication") == "yes" or s.get("permitrootlogin") == "yes" else "OK"
    return _section_finding(c, src, "SEC-SSHD", "sshd effective auth settings (WSL Ubuntu)", status, s)


def _apt(c):
    src = ("ubuntu_root", "apt_upgradable")
    a = P.apt_upgradable(c.ok(*src)["lines"])
    return _section_finding(c, src, "SEC-APT-UPDATES", "Pending Ubuntu updates",
                            "WARN" if a["security"] else ("INFO" if a["count"] else "OK"), a)


def _apt_age(c):
    mt, ep = P.body(c.ok("ubuntu_root", "apt_cache_mtime")["lines"])[0], P.body(c.ok("linux_host", "epoch")["lines"])[0]
    age = round((int(ep) - int(mt)) / 86400, 1)
    return finding("SEC-APT-LISTS-AGE", "security", "apt package lists age",
                   "WARN" if age > c.t["apt_lists_age_days_warn"] else "OK", value={"days": age},
                   note="pending-update counts are only as fresh as these lists",
                   evidence=[c.cite("ubuntu_root", "apt_cache_mtime", mt), c.cite("linux_host", "epoch", ep)])


def _unattended(c):
    src = ("ubuntu_root", "auto_upgrades")
    on = any('Unattended-Upgrade "1"' in ln for ln in c.ok(*src)["lines"])
    return _section_finding(c, src, "SEC-UNATTENDED", "Ubuntu unattended-upgrades enabled", "OK" if on else "WARN",
                            {"enabled": on})


def _wsl_reboot(c):
    src = ("ubuntu_root", "reboot_required")
    flag = P.body(c.ok(*src)["lines"])[0].strip()
    return _section_finding(c, src, "SEC-WSL-REBOOT", "Ubuntu reboot-required flag", "WARN" if flag == "yes" else "OK", [flag])


def _defender(c):
    w, title = c.win, "Defender AV, real-time protection, signature age"
    vals = {k: w[k] for k in ("defender_av", "defender_rtp", "defender_sig_age_days")}
    if None in vals.values():
        return unknown("SEC-WIN-DEFENDER", "security", title, [c.win_ev()], note="Get-MpComputerStatus returned no value")
    ok = vals["defender_av"] and vals["defender_rtp"] and vals["defender_sig_age_days"] <= c.t["defender_sig_age_days_warn"]
    return finding("SEC-WIN-DEFENDER", "security", title, "OK" if ok else "WARN", value=vals, evidence=[c.win_ev()])


def _firewall(c):
    fw = c.win["firewall"]
    if not fw:
        return unknown("SEC-WIN-FIREWALL", "security", "Windows firewall profiles enabled", [c.win_ev()],
                       note="Get-NetFirewallProfile returned no profiles")
    off = [f["name"] for f in fw if f["enabled"].lower() not in ("true", "1")]
    return finding("SEC-WIN-FIREWALL", "security", "Windows firewall profiles enabled", "WARN" if off else "OK",
                   value={"disabled": off}, evidence=[c.win_ev()])


def _hotfix(c):
    w = c.win
    if not w["hotfix_date"]:
        return unknown("SEC-WIN-HOTFIX", "security", "Days since last Windows update", [c.win_ev()],
                       note="Get-HotFix returned no dated update")
    d = dt.datetime.strptime(w["hotfix_date"], "%Y-%m-%d").replace(tzinfo=dt.UTC)
    age = int((w["epoch"] - d.timestamp()) // 86400)
    return finding("SEC-WIN-HOTFIX", "security", "Days since last Windows update",
                   "WARN" if age > c.t["hotfix_age_days_warn"] else "OK",
                   value={"hotfix": w["hotfix_id"], "installed": w["hotfix_date"], "age_days": age}, evidence=[c.win_ev()])

