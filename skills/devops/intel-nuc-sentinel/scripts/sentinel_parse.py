"""Pure parsers: raw probe output -> facts. No I/O, no network.

Each fact keeps the raw line it came from so the report can quote its evidence.
"""
from __future__ import annotations

import json
import re

_RC = re.compile(r"^##rc (\S+) (-?\d+)$")
_HEAD = re.compile(r"^## (\S+)$")
_SIZE = re.compile(r"^([\d.]+)\s*([kKMGT]?B)$")
_UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def sections(text: str) -> dict[str, dict]:
    """Split ``## name`` ... ``##rc name N`` blocks. A block with no rc line keeps rc=None."""
    out: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        line = line.rstrip("\r")
        rc = _RC.match(line)
        if rc and rc.group(1) in out:
            out[rc.group(1)]["rc"] = int(rc.group(2))
            current = None
            continue
        head = _HEAD.match(line)
        if head:
            current = head.group(1)
            out[current] = {"rc": None, "lines": []}
            continue
        if current is not None:
            out[current]["lines"].append(line)
    return out


def ok(sec: dict | None) -> bool:
    return bool(sec) and sec.get("rc") == 0


def human_bytes(text: str) -> int | None:
    """``4.1kB`` / ``2.8GB`` (docker's decimal units) -> bytes."""
    m = _SIZE.match(text.strip())
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def df_rows(lines: list[str]) -> dict[str, dict]:
    """``df -B1 -P`` -> {mount: {size, used, avail, raw}} (header skipped)."""
    rows = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        rows[parts[-1]] = {"size": int(parts[1]), "used": int(parts[2]), "avail": int(parts[3]), "raw": line}
    return rows


def du_rows(lines: list[str]) -> dict[str, dict]:
    """``du -sk`` -> {path: {bytes, raw}}."""
    rows = {}
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows[parts[1]] = {"bytes": int(parts[0]) * 1024, "raw": line}
    return rows


def kv_rows(lines: list[str], sep: str = ":") -> dict[str, str]:
    out = {}
    for line in lines:
        if sep in line and not line.startswith("#"):
            k, v = line.split(sep, 1)
            out[k.strip()] = v.strip()
    return out


def json_lines(lines: list[str]) -> tuple[list[dict], list[str]]:
    """Parse one JSON object per line; unparsable lines are returned, never dropped silently."""
    good, bad = [], []
    for line in lines:
        if not line.strip():
            continue
        try:
            good.append(json.loads(line))
        except json.JSONDecodeError:
            bad.append(line)
    return good, bad


def tagged_rows(text: str, sep: str) -> dict[str, list[list[str]]]:
    """psql ``-At -F '|'`` / clickhouse TSV rows whose first column is a tag."""
    out: dict[str, list[list[str]]] = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line:
            continue
        cols = line.split(sep)
        out.setdefault(cols[0], []).append(cols[1:])
    return out


def meminfo(lines: list[str]) -> dict[str, int]:
    out = {}
    for line in lines:
        m = re.match(r"^(\w+):\s+(\d+) kB$", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def stat_rows(lines: list[str]) -> dict[str, int]:
    """``stat -c '%s %n'`` -> {path: bytes}."""
    out = {}
    for line in lines:
        size, _, path = line.partition(" ")
        if size.isdigit() and path:
            out[path] = int(size)
    return out


def apt_upgradable(lines: list[str]) -> dict:
    pkgs = [ln for ln in lines if "[upgradable from:" in ln]
    security = [ln for ln in pkgs if "-security" in ln.split(" ", 1)[0]]
    return {"count": len(pkgs), "security": len(security), "security_pkgs": [p.split("/", 1)[0] for p in security]}


def sshd(lines: list[str]) -> dict[str, str]:
    out = {}
    for line in lines:
        parts = line.split()
        if len(parts) == 2:
            out[parts[0].lower()] = parts[1].lower()
    return out


# ------------------------------------------------------------------ shape validation
# A section that exited 0 but does not have the shape its command prints (truncated, an error
# banner on stdout, a changed CLI format) is malformed: checks treat it like a failed section.
_F = r"\d+(?:\.\d+)?"


def _body(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.strip()]


def _every(lines: list[str], pred, allow_empty: bool = False) -> bool:
    body = _body(lines)
    return (allow_empty or bool(body)) and all(pred(ln) for ln in body)


def _json_objs(lines: list[str], keys: tuple[str, ...], allow_empty: bool = False) -> bool:
    good, bad = json_lines(lines)
    return not bad and (allow_empty or bool(good)) and all(isinstance(o, dict) and all(k in o for k in keys) for o in good)


def _single(lines: list[str], pattern: str) -> bool:
    body = _body(lines)
    return len(body) == 1 and re.fullmatch(pattern, body[0].strip()) is not None


def _df_ok(lines: list[str]) -> bool:
    body = _body(lines)
    return len(body) >= 2 and body[0].startswith("Filesystem") and len(df_rows(body[1:])) == len(body) - 1


def _du_ok(lines: list[str]) -> bool:
    return _every(lines, lambda ln: bool(du_rows([ln])))


def _redis_info(key: str):
    return lambda ls: key in kv_rows(ls) and _every(ls, lambda ln: ln.startswith("#") or ":" in ln)


_SHAPES = {
    "epoch": lambda ls: _single(ls, r"\d+"),
    "uptime": lambda ls: _single(ls, rf"{_F} {_F}"),
    "nproc": lambda ls: _single(ls, r"[1-9]\d*"),
    "loadavg": lambda ls: _single(ls, rf"{_F} {_F} {_F} \d+/\d+ \d+"),
    "meminfo": lambda ls: {"MemTotal", "MemAvailable"} <= set(meminfo(ls)),
    "df": _df_ok,
    "vhdx_stat": lambda ls: _every(ls, lambda ln: bool(stat_rows([ln]))),
    "docker_ps": lambda ls: _json_objs(ls, ("id", "name", "state", "status", "size"), allow_empty=True),
    "docker_info": lambda ls: len(_body(ls)) == 1 and _json_objs(ls, ("running", "total")),
    "docker_inspect": lambda ls: _json_objs(ls, ("id", "restart_count", "oom_killed", "log_type", "mounts"),
                                            allow_empty=True),
    "docker_df": lambda ls: _json_objs(ls, ("Type",), allow_empty=True),
    "redis_ping": lambda ls: _single(ls, r"PONG"),
    "redis_persistence": _redis_info("rdb_last_bgsave_status"),
    "redis_memory": _redis_info("used_memory"),
    "zookeeper_env": lambda ls: _every(ls, lambda ln: "=" in ln) and "purge_interval" in kv_rows(ls, "="),
    "container_logs": _du_ok,
    "volumes_du": _du_ok,
    "data_du": _du_ok,
    "sshd": lambda ls: _every(ls, lambda ln: len(ln.split()) == 2),
    "apt_upgradable": lambda ls: bool(_body(ls)) and _body(ls)[0].startswith("Listing")
    and all("[upgradable from:" in ln for ln in _body(ls)[1:]),
    "apt_cache_mtime": lambda ls: _single(ls, r"\d+"),
    "auto_upgrades": lambda ls: _every(ls, lambda ln: ln.startswith(("APT::", "//"))),
    "reboot_required": lambda ls: _single(ls, r"yes|no"),
}


def well_formed(name: str, lines: list[str]) -> bool:
    """Fail closed: a section name with no known shape is never well formed."""
    shape = _SHAPES.get(name.split(":", 1)[0])
    return bool(shape and shape(lines))


# psql / clickhouse rows: tags every healthy run prints, and tags that may legitimately be absent.
SQL_TAGS = {
    "postgres": ({"read_only", "db", "conn", "long_tx", "idle_in_tx", "xid_age", "in_recovery"}, set()),
    "clickhouse": ({"readonly", "uptime", "parts_total", "disk"}, {"db", "log_no_ttl"}),
}


def sql_well_formed(probe: str, rows: dict[str, list[list[str]]]) -> bool:
    required, optional = SQL_TAGS[probe]
    return required <= set(rows) <= required | optional


WINDOWS_FIELDS = {
    "epoch": int, "uptime_s": int, "mem_total_kb": int, "mem_free_kb": int, "reboot_pending": bool,
    "firewall": list, "disks": list, "vhdx": list,
}


def windows_well_formed(obj) -> bool:
    return isinstance(obj, dict) and all(
        isinstance(obj.get(k), t) and not (t is int and isinstance(obj.get(k), bool)) for k, t in WINDOWS_FIELDS.items())
