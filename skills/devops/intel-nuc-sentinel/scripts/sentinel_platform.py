"""Storage, datastore, host-resource and security checks (same contract as sentinel_checks)."""
from __future__ import annotations

from sentinel_checks import Ctx, ev, finding, gb, level, unknown
import sentinel_parse as P


def _pct(used, size):
    return round(100.0 * used / size, 1) if size else None


# ------------------------------------------------------------------ storage
def check_storage(c: Ctx) -> list[dict]:
    out = []
    df_sec = c.ok("linux_host", "df")
    rows = P.df_rows(df_sec["lines"]) if df_sec else {}
    disks = {d["id"]: d for d in (c.win or {}).get("disks", [])}
    for mount in c.cfg["linux_fs"]:
        fid = "STORAGE-DISK-" + (mount.strip("/").replace("/", "-").upper() or "ROOT")
        row = rows.get(mount)
        if not row:
            out.append(unknown(fid, "storage", f"Disk usage {mount}", "linux_host", "df", c.s("linux_host", "df")))
            continue
        pct = _pct(row["used"], row["size"])
        verified, note = None, ""
        drive = c.cfg.get("drive_cross_check", {}).get(mount)
        if drive and drive in disks:
            # Two reads seconds apart on a live disk: agree within 2 GB.
            diff = abs(disks[drive]["free"] - row["avail"])
            verified = diff <= 2e9
            note = f"Windows {drive} free={gb(disks[drive]['free'])} GB vs df avail={gb(row['avail'])} GB (diff {gb(diff)} GB)"
        out.append(finding(fid, "storage", f"Disk usage {mount}", level(pct, c.t["disk_used_pct_warn"], c.t["disk_used_pct_crit"]),
                           value={"used_pct": pct, "free_gb": gb(row["avail"]), "size_gb": gb(row["size"])},
                           threshold=f"warn {c.t['disk_used_pct_warn']}% / crit {c.t['disk_used_pct_crit']}%",
                           evidence=[ev("linux_host", "df", row["raw"])], verified=verified, note=note))
    out += _vhdx(c)
    out.append(_orphans(c))
    out.append(_zookeeper(c))
    return out


def _inner_used(c, inner):
    src = {"docker_disk": ("docker_vm", "df", c.cfg["docker_disk_mount"]), "ubuntu_root": ("ubuntu_root", "df", "/")}[inner]
    sec = c.ok(src[0], src[1])
    row = P.df_rows(sec["lines"]).get(src[2]) if sec else None
    return row, src


def _vhdx(c):
    out = []
    st = c.ok("linux_host", "vhdx_stat")
    sizes = P.stat_rows(st["lines"]) if st else {}
    win = {v["path"]: v["bytes"] for v in (c.win or {}).get("vhdx", [])}
    for v in c.cfg["vhdx"]:
        fid = f"STORAGE-VHDX-{v['name'].upper()}"
        size = sizes.get(v["wsl_path"])
        row, src = _inner_used(c, v["inner"])
        if size is None or row is None:
            out.append(finding(fid, "storage", f"VHDX slack {v['name']}", "UNKNOWN",
                               note=f"file size={size} inner df={'ok' if row else 'missing'}"))
            continue
        slack = size - row["used"]
        verified = None
        note = "slack = VHDX file length - filesystem used inside; reclaim needs a maintenance window (compaction is never automated)"
        if v["windows_path"] in win:
            verified = abs(win[v["windows_path"]] - size) <= 1e9
            note += f"; Windows Get-Item length={win[v['windows_path']]}"
        out.append(finding(fid, "storage", f"VHDX slack {v['name']}", level(slack / 1e9, c.t["vhdx_slack_gb_warn"]),
                           value={"file_gb": gb(size), "used_inside_gb": gb(row["used"]), "slack_gb": gb(slack)},
                           threshold=f"slack >= {c.t['vhdx_slack_gb_warn']} GB",
                           evidence=[ev("linux_host", "vhdx_stat", f"{size} {v['wsl_path']}"), ev(src[0], src[1], row["raw"])],
                           verified=verified, note=note))
    return out


def _orphans(c):
    sec = c.ok("ubuntu_root", "volumes_du")
    if sec is None or c.containers is None:
        return unknown("STORAGE-ORPHANS", "storage", "Unmounted dirs under volume root", "ubuntu_root", "volumes_du",
                       c.s("ubuntu_root", "volumes_du"))
    sources = [m["source"].rstrip("/") for r in c.containers for m in r["inspect"].get("mounts", []) if m.get("source")]
    orphans = {}
    for path, row in sorted(P.du_rows(sec["lines"]).items()):
        if not any(s == path or s.startswith(path + "/") or path.startswith(s + "/") for s in sources):
            orphans[path] = row
    total = sum(r["bytes"] for r in orphans.values())
    return finding("STORAGE-ORPHANS", "storage", "Dirs under volume root no container mounts",
                   "INFO" if orphans else "OK", value={"total_gb": gb(total), "dirs_mb": {p: round(r["bytes"] / 1e6, 1) for p, r in orphans.items()}},
                   evidence=[ev("ubuntu_root", "volumes_du", r["raw"]) for r in orphans.values()],
                   note="report only: retiring a stack is an owner decision")


def _zookeeper(c):
    sec = c.ok("docker_engine", "zookeeper_env")
    if sec is None:
        return unknown("DB-ZK-AUTOPURGE", "db", "Zookeeper autopurge", "docker_engine", "zookeeper_env", c.s("docker_engine", "zookeeper_env"))
    kv = P.kv_rows(sec["lines"], "=")
    interval = kv.get("purge_interval")
    du = c.ok("ubuntu_root", "data_du")
    dl = P.du_rows(du["lines"]).get(c.cfg["zookeeper"]["datalog_host_path"]) if du else None
    off = interval in (None, "unset", "0")
    return finding("DB-ZK-AUTOPURGE", "db", "Zookeeper autopurge disabled", "WARN" if off else "OK",
                   value={"purge_interval": interval, "snap_retain": kv.get("snap_retain"),
                          "datalog_gb": gb(dl["bytes"]) if dl else None},
                   evidence=[ev("docker_engine", "zookeeper_env", " ".join(sec["lines"])), ev("ubuntu_root", "data_du", dl["raw"] if dl else "")])


# ------------------------------------------------------------------ datastores
def check_db(c: Ctx) -> list[dict]:
    return [*_postgres(c), *_clickhouse(c), *_redis(c)]


def _du(c, path):
    sec = c.ok("ubuntu_root", "data_du")
    return P.du_rows(sec["lines"]).get(path) if sec else None


def _postgres(c):
    rows = c.sql("postgres", "|")
    if rows is None:
        r = c.raw.get("postgres") or {}
        return [finding("DB-PG-UP", "db", "Postgres reachable read-only", "CRIT", note=(r.get("stderr") or "")[:300],
                        evidence=[ev("postgres", "stderr", (r.get("stderr") or "")[:300])])]
    ro = (rows.get("read_only") or [["?"]])[0][0]
    out = [finding("DB-PG-UP", "db", "Postgres reachable read-only", "OK" if ro == "on" else "CRIT",
                   value={"default_transaction_read_only": ro}, evidence=[ev("postgres", "read_only", ro)])]
    conn = rows.get("conn", [[None, None]])[0]
    pct = _pct(int(conn[0]), int(conn[1])) if conn[0] else None
    out.append(finding("DB-PG-CONN", "db", "Postgres connection usage", level(pct, c.t["pg_conn_pct_warn"]),
                       value={"used": conn[0], "max": conn[1], "pct": pct}, evidence=[ev("postgres", "conn", "|".join(conn))]))
    stuck = {k: int(rows.get(k, [["0"]])[0][0]) for k in ("long_tx", "idle_in_tx")}
    out.append(finding("DB-PG-TX", "db", "Postgres long or idle-in-transaction sessions",
                       "WARN" if any(stuck.values()) else "OK", value=stuck,
                       evidence=[ev("postgres", k, v) for k, v in stuck.items()]))
    xid = rows.get("xid_age", [[None, None]])[0]
    age = int(xid[1]) if xid[1] else None
    out.append(finding("DB-PG-XID", "db", "Postgres transaction-ID wraparound age",
                       level(age, c.t["pg_xid_age_warn"], c.t["pg_xid_age_crit"]), value={"db": xid[0], "age": age},
                       evidence=[ev("postgres", "xid_age", "|".join(map(str, xid)))]))
    sizes = {r[0]: int(r[1]) for r in rows.get("db", [])}
    du = _du(c, c.cfg["postgres"]["data_host_path"])
    verified = (sum(sizes.values()) <= du["bytes"] * 1.05) if du else None
    prev = c.baseline.get("pg_sizes") or {}
    growth = {d: round((s - prev[d]) / 1e6, 1) for d, s in sizes.items() if d in prev and s != prev[d]}
    top = dict(sorted(sizes.items(), key=lambda kv: -kv[1])[:5])
    out.append(finding("DB-PG-SIZE", "db", "Postgres database sizes", "INFO",
                       value={"total_gb": gb(sum(sizes.values())), "top_gb": {k: gb(v) for k, v in top.items()}, "growth_mb": growth},
                       evidence=[ev("ubuntu_root", "data_du", du["raw"] if du else "")], verified=verified,
                       note="cross-check: sum(pg_database_size) must not exceed du of PGDATA (+5%)"))
    return out


def _clickhouse(c):
    rows = c.sql("clickhouse", "\t")
    if rows is None:
        r = c.raw.get("clickhouse") or {}
        return [finding("DB-CH-UP", "db", "ClickHouse reachable read-only", "CRIT",
                        evidence=[ev("clickhouse", "stderr", (r.get("stderr") or "")[:300])])]
    ro = (rows.get("readonly") or [["?"]])[0][0]
    out = [finding("DB-CH-UP", "db", "ClickHouse reachable read-only", "OK" if ro == "1" else "CRIT",
                   value={"readonly": ro, "uptime_s": (rows.get("uptime") or [[None]])[0][0]}, evidence=[ev("clickhouse", "readonly", ro)])]
    no_ttl = {r[0]: int(r[1] or 0) for r in rows.get("log_no_ttl", []) if r[1] not in ("", "\\N")}
    big = sum(no_ttl.values())
    status = "WARN" if big >= c.t["ch_log_no_ttl_gb_warn"] * 1e9 else ("INFO" if no_ttl else "OK")
    out.append(finding("DB-CH-LOG-TTL", "db", "ClickHouse system log tables without TTL", status,
                       threshold=f"no-TTL bytes >= {c.t['ch_log_no_ttl_gb_warn']} GB",
                       value={"tables": len(no_ttl), "total_gb": gb(big), "top": dict(sorted(no_ttl.items(), key=lambda kv: -kv[1])[:5])},
                       evidence=[ev("clickhouse", "log_no_ttl", f"{k}\t{v}") for k, v in sorted(no_ttl.items(), key=lambda kv: -kv[1])[:5]]))
    parts = int((rows.get("parts_total") or [["0"]])[0][0] or 0)
    du = _du(c, c.cfg["clickhouse"]["data_host_path"])
    dbs = {r[0]: int(r[1]) for r in rows.get("db", [])}
    prev = c.baseline.get("ch_sizes") or {}
    out.append(finding("DB-CH-SIZE", "db", "ClickHouse database sizes", "INFO",
                       value={"parts_gb": gb(parts), "dbs_gb": {k: gb(v) for k, v in dbs.items()},
                              "growth_mb": {d: round((s - prev[d]) / 1e6, 1) for d, s in dbs.items() if d in prev and s != prev[d]}},
                       evidence=[ev("ubuntu_root", "data_du", du["raw"] if du else "")],
                       verified=(parts <= du["bytes"] * 1.05) if du else None,
                       note="cross-check: active parts bytes must not exceed du of the data dir (+5%)"))
    return out


def _redis(c):
    out = []
    for name in c.cfg["redis"]:
        ping_sec = c.s("docker_engine", f"redis_ping:{name}")
        if ping_sec is None:
            out.append(unknown(f"DB-REDIS-{name}", "db", f"Redis {name} health", "docker_engine", f"redis_ping:{name}", None))
            continue
        ping = c.ok("docker_engine", f"redis_ping:{name}")
        pers = c.ok("docker_engine", f"redis_persistence:{name}")
        mem = c.ok("docker_engine", f"redis_memory:{name}")
        if not (ping and pers):
            out.append(finding(f"DB-REDIS-{name}", "db", f"Redis {name} health", "CRIT", note="PING or INFO exited non-zero",
                               evidence=[ev("docker_engine", f"redis_ping:{name}", " ".join(ping_sec["lines"][:3]))]))
            continue
        kv = P.kv_rows(pers["lines"])
        mkv = P.kv_rows(mem["lines"]) if mem else {}
        bad = [k for k in ("rdb_last_bgsave_status", "aof_last_write_status") if kv.get(k, "ok") != "ok"]
        pong = ping["lines"][0].strip() if ping["lines"] else ""
        out.append(finding(f"DB-REDIS-{name}", "db", f"Redis {name} health", "WARN" if bad or pong != "PONG" else "OK",
                           value={"ping": pong, "aof_enabled": kv.get("aof_enabled"), "failed": bad,
                                  "used_memory_mb": round(int(mkv.get("used_memory", 0)) / 1e6, 1), "maxmemory": mkv.get("maxmemory")},
                           evidence=[ev("docker_engine", f"redis_persistence:{name}", f"{k}:{kv.get(k)}") for k in ("rdb_last_bgsave_status", "aof_last_write_status")]))
    return out


# ------------------------------------------------------------------ host + security
def check_host(c: Ctx) -> list[dict]:
    out = []
    mi = c.ok("linux_host", "meminfo")
    m = P.meminfo(mi["lines"]) if mi else {}
    pct = _pct(m.get("MemAvailable", 0), m.get("MemTotal", 0)) if m else None
    out.append(finding("HOST-MEM-WSL", "host", "WSL memory available", "UNKNOWN" if pct is None else
                       ("WARN" if pct <= c.t["mem_available_pct_warn"] else "OK"),
                       value={"available_pct": pct, "total_gb": gb(m.get("MemTotal", 0))}, threshold=f"<= {c.t['mem_available_pct_warn']}%",
                       evidence=[ev("linux_host", "meminfo", f"MemTotal={m.get('MemTotal')} MemAvailable={m.get('MemAvailable')}")]))
    la, npr = c.ok("linux_host", "loadavg"), c.ok("linux_host", "nproc")
    if la and npr:
        load5 = float(la["lines"][0].split()[1])
        per = round(load5 / int(npr["lines"][0]), 2)
        out.append(finding("HOST-LOAD", "host", "5-min load per CPU", "WARN" if per >= c.t.get("load_per_cpu_warn", 1.0) else "OK",
                           value={"load5": load5, "cpus": int(npr["lines"][0]), "per_cpu": per}, evidence=[ev("linux_host", "loadavg", la["lines"][0])]))
    w = c.win
    if w is None:
        r = c.raw.get("windows_host") or {}
        return out + [finding("HOST-WINDOWS", "host", "Windows host facts", "UNKNOWN",
                              evidence=[ev("windows_host", "stderr", (r.get("stderr") or "")[:300])])]
    wpct = _pct(w["mem_free_kb"], w["mem_total_kb"])
    out.append(finding("HOST-MEM-WIN", "host", "Windows physical memory free", "WARN" if wpct <= c.t["mem_available_pct_warn"] else "OK",
                       value={"free_pct": wpct, "total_gb": gb(w["mem_total_kb"] * 1024)}, evidence=[ev("windows_host", "json", f"free_kb={w['mem_free_kb']} total_kb={w['mem_total_kb']}")]))
    out.append(finding("HOST-REBOOT-WIN", "host", "Windows reboot pending", "WARN" if w.get("reboot_pending") else "OK",
                       value={"reboot_pending": w.get("reboot_pending"), "uptime_days": round(w["uptime_s"] / 86400, 1)},
                       evidence=[ev("windows_host", "json", f"reboot_pending={w.get('reboot_pending')} uptime_s={w['uptime_s']}")]))
    return out


def check_security(c: Ctx) -> list[dict]:
    out = []
    sec = c.ok("ubuntu_root", "sshd")
    if sec:
        s = P.sshd(sec["lines"])
        status = "CRIT" if s.get("permitemptypasswords") == "yes" else \
            "WARN" if s.get("passwordauthentication") == "yes" or s.get("permitrootlogin") == "yes" else "OK"
        out.append(finding("SEC-SSHD", "security", "sshd effective auth settings (WSL Ubuntu)", status, value=s,
                           evidence=[ev("ubuntu_root", "sshd", ln) for ln in sec["lines"]]))
    else:
        out.append(unknown("SEC-SSHD", "security", "sshd effective auth settings", "ubuntu_root", "sshd", c.s("ubuntu_root", "sshd")))
    apt = c.ok("ubuntu_root", "apt_upgradable")
    if apt:
        a = P.apt_upgradable(apt["lines"])
        out.append(finding("SEC-APT-UPDATES", "security", "Pending Ubuntu updates", "WARN" if a["security"] else ("INFO" if a["count"] else "OK"),
                           value=a, evidence=[ev("ubuntu_root", "apt_upgradable", f"{a['count']} upgradable, {a['security']} from -security")]))
    mt, epoch = c.ok("ubuntu_root", "apt_cache_mtime"), c.ok("linux_host", "epoch")
    if mt and epoch:
        age = round((int(epoch["lines"][0]) - int(mt["lines"][0])) / 86400, 1)
        out.append(finding("SEC-APT-LISTS-AGE", "security", "apt package lists age", "WARN" if age > c.t["apt_lists_age_days_warn"] else "OK",
                           value={"days": age}, note="pending-update counts are only as fresh as these lists",
                           evidence=[ev("ubuntu_root", "apt_cache_mtime", mt["lines"][0])]))
    uu = c.ok("ubuntu_root", "auto_upgrades")
    if uu is None:
        out.append(unknown("SEC-UNATTENDED", "security", "Ubuntu unattended-upgrades enabled", "ubuntu_root",
                           "auto_upgrades", c.s("ubuntu_root", "auto_upgrades")))
    else:
        on = any('Unattended-Upgrade "1"' in ln for ln in uu["lines"])
        out.append(finding("SEC-UNATTENDED", "security", "Ubuntu unattended-upgrades enabled", "OK" if on else "WARN",
                           value={"enabled": on}, evidence=[ev("ubuntu_root", "auto_upgrades", " ".join(uu["lines"]))]))
    rr = c.ok("ubuntu_root", "reboot_required")
    if rr:
        out.append(finding("SEC-WSL-REBOOT", "security", "Ubuntu reboot-required flag", "WARN" if rr["lines"][:1] == ["yes"] else "OK",
                           value=rr["lines"][:1], evidence=[ev("ubuntu_root", "reboot_required", " ".join(rr["lines"]))]))
    return out + _windows_security(c)


def _windows_security(c):
    w = c.win
    if w is None:
        return []
    out = []
    av_ok = w.get("defender_av") and w.get("defender_rtp") and (w.get("defender_sig_age_days") or 0) <= c.t["defender_sig_age_days_warn"]
    out.append(finding("SEC-WIN-DEFENDER", "security", "Defender AV, real-time protection, signature age", "OK" if av_ok else "WARN",
                       value={k: w.get(k) for k in ("defender_av", "defender_rtp", "defender_sig_age_days")},
                       evidence=[ev("windows_host", "json", f"av={w.get('defender_av')} rtp={w.get('defender_rtp')} sig_age={w.get('defender_sig_age_days')}")]))
    off = [f["name"] for f in w.get("firewall", []) if str(f.get("enabled")).lower() not in ("true", "1")]
    out.append(finding("SEC-WIN-FIREWALL", "security", "Windows firewall profiles enabled", "WARN" if off else "OK",
                       value={"disabled": off}, evidence=[ev("windows_host", "json", str(w.get("firewall")))]))
    hf_date, epoch = w.get("hotfix_date"), w.get("epoch")
    if hf_date and epoch:
        import datetime as dt
        d = dt.datetime.strptime(hf_date, "%Y-%m-%d").replace(tzinfo=dt.UTC)
        age = int((epoch - d.timestamp()) // 86400)
        out.append(finding("SEC-WIN-HOTFIX", "security", "Days since last Windows update", "WARN" if age > c.t["hotfix_age_days_warn"] else "OK",
                           value={"hotfix": w.get("hotfix_id"), "installed": hf_date, "age_days": age},
                           evidence=[ev("windows_host", "json", f"{w.get('hotfix_id')} {hf_date}")]))
    return out
