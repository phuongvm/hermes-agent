"""Pure parsers: raw probe output -> facts. No I/O, no network.

Each fact keeps the exact raw line it came from so a finding can cite it by locator
(``probe``, ``section``, ``line_no`` into ``raw_lines(stdout)``) and ``verify`` can re-read it.
"""
from __future__ import annotations

import json
import re

_RC = re.compile(r"^##rc (\S+) (-?\d+)$")
_HEAD = re.compile(r"^## (\S+)$")
_SIZE = re.compile(r"^([\d.]+)\s*([kKMGT]?B)$")
_UNITS = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


def raw_lines(text: str) -> list[str]:
    """The line numbering every evidence locator (``line_no``) refers to."""
    return [ln.rstrip("\r") for ln in (text or "").splitlines()]


def sections(text: str) -> dict[str, dict]:
    """Split ``## name`` ... ``##rc name N`` blocks. A block with no rc line keeps rc=None.

    ``start`` is the header's index in ``raw_lines(text)``; ``end`` is exclusive and includes the
    ``##rc`` frame line when present, so ``raw_lines(text)[start:end]`` is the whole block.
    """
    out: dict[str, dict] = {}
    lines = raw_lines(text)
    current = None
    for i, line in enumerate(lines):
        rc = _RC.match(line)
        if rc and rc.group(1) in out:
            out[rc.group(1)].update(rc=int(rc.group(2)), rc_line=line, end=i + 1)
            current = None
            continue
        head = _HEAD.match(line)
        if head:
            if current is not None:
                out[current]["end"] = i
            current = head.group(1)
            out[current] = {"rc": None, "lines": [], "start": i, "end": len(lines)}
            continue
        if current is not None:
            out[current]["lines"].append(line)
    return out


def ok(sec: dict | None) -> bool:
    return bool(sec) and sec.get("rc") == 0


def body(lines: list[str]) -> list[str]:
    return [ln for ln in lines if ln.strip()]


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
        if len(parts) < 6 or not all(p.isdigit() for p in parts[1:4]):
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


def kv_line(lines: list[str], key: str, sep: str = ":") -> str | None:
    """The exact raw line that carries ``key`` (for evidence), or None."""
    return next((ln for ln in lines if sep in ln and ln.split(sep, 1)[0].strip() == key), None)


def json_lines(lines: list[str]) -> tuple[list, list[str]]:
    """Parse one JSON value per line; unparsable lines are returned, never dropped silently."""
    good, bad = [], []
    for line in lines:
        if not line.strip():
            continue
        try:
            good.append(json.loads(line))
        except json.JSONDecodeError:
            bad.append(line)
    return good, bad


def json_records(lines: list[str]) -> list[tuple[str, dict]]:
    """(raw line, object) for every parseable non-empty line; callers shape-check first."""
    out = []
    for ln in body(lines):
        good, _ = json_lines([ln])
        out += [(ln, obj) for obj in good]
    return out


def sql_rows(text: str, sep: str) -> dict[str, list[dict]]:
    """psql ``-At -F '|'`` / clickhouse TSV rows whose first column is a tag -> {tag: [{cols, raw}]}."""
    out: dict[str, list[dict]] = {}
    for line in raw_lines(text):
        if not line:
            continue
        cols = line.split(sep)
        out.setdefault(cols[0], []).append({"cols": cols[1:], "raw": line})
    return out


def meminfo(lines: list[str]) -> dict[str, dict]:
    """``/proc/meminfo`` -> {key: {bytes, raw}}."""
    out = {}
    for line in lines:
        m = re.match(r"^(\w+):\s+(\d+) kB$", line)
        if m:
            out[m.group(1)] = {"bytes": int(m.group(2)) * 1024, "raw": line}
    return out


def stat_rows(lines: list[str]) -> dict[str, dict]:
    """``stat -c '%s %n'`` -> {path: {bytes, raw}}."""
    out = {}
    for line in lines:
        size, _, path = line.partition(" ")
        if size.isdigit() and path:
            out[path] = {"bytes": int(size), "raw": line}
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


# ------------------------------------------------------------------ typed schemas
class Map:
    """A JSON object with arbitrary string keys whose values all match ``schema``."""

    def __init__(self, schema):
        self.schema = schema


class Match:
    """A JSON string that fully matches ``pattern``."""

    def __init__(self, pattern: str):
        self.pattern = re.compile(pattern)


def conforms(value, schema) -> bool:
    """dict = required keys (extra keys allowed); [s] = list of s; tuple = any of; None = null."""
    if schema is None:
        return value is None
    if isinstance(schema, tuple):
        return any(conforms(value, s) for s in schema)
    if isinstance(schema, dict):
        return isinstance(value, dict) and all(k in value and conforms(value[k], s) for k, s in schema.items())
    if isinstance(schema, list):
        return isinstance(value, list) and all(conforms(v, schema[0]) for v in value)
    if isinstance(schema, Map):
        return isinstance(value, dict) and all(conforms(v, schema.schema) for v in value.values())
    if isinstance(schema, Match):
        return isinstance(value, str) and schema.pattern.fullmatch(value) is not None
    if schema is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, schema)


DOCKER_PS = {"id": str, "name": str, "state": str, "status": str, "size": str}
DOCKER_INSPECT = {
    "id": str, "name": str, "restart_count": int, "oom_killed": bool, "health": (None, str), "privileged": bool,
    "log_type": str, "log_opts": (None, Map(str)), "log_path": str,
    "ports": (None, Map((None, [{"HostIp": str, "HostPort": str}]))),
    "mounts": [{"type": str, "source": str, "dest": str}],
}
DOCKER_INFO = {"running": int, "total": int}
# Defender / hotfix fields are null when the cmdlet returns nothing: only that finding is UNKNOWN.
WINDOWS = {
    "epoch": int, "uptime_s": int, "mem_total_kb": int, "mem_free_kb": int, "reboot_pending": bool,
    "hotfix_id": (None, str), "hotfix_date": (None, Match(r"\d{4}-\d{2}-\d{2}")),
    "defender_av": (None, bool), "defender_rtp": (None, bool), "defender_sig_age_days": (None, int),
    "firewall": [{"name": str, "enabled": str}],
    "disks": [{"id": str, "size": int, "free": int}],
    "vhdx": [{"path": str, "bytes": int}],
}


# ------------------------------------------------------------------ shape validation
# A section that exited 0 but does not have the shape its command prints (truncated, an error
# banner on stdout, a changed CLI format) is malformed: checks treat it like a failed section.
_F = r"\d+(?:\.\d+)?"


def _every(lines: list[str], pred, allow_empty: bool = False) -> bool:
    b = body(lines)
    return (allow_empty or bool(b)) and all(pred(ln) for ln in b)


def _json_objs(lines: list[str], schema, allow_empty: bool = False) -> bool:
    good, bad = json_lines(lines)
    return not bad and (allow_empty or bool(good)) and all(conforms(o, schema) for o in good)


def _single(lines: list[str], pattern: str) -> bool:
    b = body(lines)
    return len(b) == 1 and re.fullmatch(pattern, b[0].strip()) is not None


def _df_ok(lines: list[str]) -> bool:
    b = body(lines)
    return len(b) >= 2 and b[0].startswith("Filesystem") and len(df_rows(b[1:])) == len(b) - 1


def _du_ok(lines: list[str]) -> bool:
    return _every(lines, lambda ln: bool(du_rows([ln])))


def _redis_info(*keys: str):
    def shape(ls):
        kv = kv_rows(ls)
        return all(k in kv for k in keys) and _every(ls, lambda ln: ln.startswith("#") or ":" in ln)
    return shape


def _meminfo_ok(ls: list[str]) -> bool:
    m = meminfo(ls)
    return {"MemTotal", "MemAvailable"} <= set(m) and m["MemTotal"]["bytes"] > 0


_SHAPES = {
    "epoch": lambda ls: _single(ls, r"\d+"),
    "uptime": lambda ls: _single(ls, rf"{_F} {_F}"),
    "nproc": lambda ls: _single(ls, r"[1-9]\d*"),
    "loadavg": lambda ls: _single(ls, rf"{_F} {_F} {_F} \d+/\d+ \d+"),
    "meminfo": _meminfo_ok,
    "df": _df_ok,
    "vhdx_stat": lambda ls: _every(ls, lambda ln: bool(stat_rows([ln]))),
    "docker_ps": lambda ls: _json_objs(ls, DOCKER_PS, allow_empty=True),
    "docker_info": lambda ls: len(body(ls)) == 1 and _json_objs(ls, DOCKER_INFO),
    "docker_inspect": lambda ls: _json_objs(ls, DOCKER_INSPECT, allow_empty=True),
    "docker_df": lambda ls: _json_objs(ls, {"Type": str}, allow_empty=True),
    "redis_ping": lambda ls: _single(ls, r"PONG"),
    "redis_persistence": _redis_info("rdb_last_bgsave_status"),
    "redis_memory": _redis_info("used_memory", "maxmemory"),
    "zookeeper_env": lambda ls: _every(ls, lambda ln: "=" in ln) and "purge_interval" in kv_rows(ls, "="),
    "container_logs": _du_ok,
    "volumes_du": _du_ok,
    "data_du": _du_ok,
    "sshd": lambda ls: _every(ls, lambda ln: len(ln.split()) == 2),
    "apt_upgradable": lambda ls: bool(body(ls)) and body(ls)[0].startswith("Listing")
    and all("[upgradable from:" in ln for ln in body(ls)[1:]),
    "apt_cache_mtime": lambda ls: _single(ls, r"\d+"),
    "auto_upgrades": lambda ls: _every(ls, lambda ln: ln.startswith(("APT::", "//"))),
    "reboot_required": lambda ls: _single(ls, r"yes|no"),
}


def well_formed(name: str, lines: list[str]) -> bool:
    """Fail closed: a section name with no known shape is never well formed."""
    shape = _SHAPES.get(name.split(":", 1)[0])
    return bool(shape and shape(lines))


# psql / clickhouse rows: per tag, the column types and how many rows a healthy run prints
# ("1" exactly one, "+" one or more, "*" any). A tag outside the schema is malformed too.
_COL = {"int": r"-?\d+", "str": r".+", "int?": r"\d+|\\N", "onoff": r"on|off", "tf": r"t|f"}
SQL_SCHEMA = {
    "postgres": {
        "read_only": (("onoff",), "1"), "db": (("str", "int"), "+"), "conn": (("int", "int"), "1"),
        "long_tx": (("int",), "1"), "idle_in_tx": (("int",), "1"), "xid_age": (("str", "int"), "1"),
        "in_recovery": (("tf",), "1"),
    },
    "clickhouse": {
        "readonly": (("int",), "1"), "uptime": (("int",), "1"), "db": (("str", "int"), "*"),
        "parts_total": (("int",), "1"), "log_no_ttl": (("str", "int?"), "*"),
        "disk": (("str", "int", "int"), "+"),
    },
}


def _row_ok(cols: list[str], types: tuple[str, ...]) -> bool:
    return len(cols) == len(types) and all(re.fullmatch(_COL[t], v) for t, v in zip(cols, types))


def sql_well_formed(probe: str, rows: dict[str, list[dict]]) -> bool:
    schema = SQL_SCHEMA[probe]
    if set(rows) - set(schema):
        return False
    for tag, (types, card) in schema.items():
        got = rows.get(tag, [])
        if (card == "1" and len(got) != 1) or (card == "+" and not got):
            return False
        if not all(_row_ok(e["cols"], types) for e in got):
            return False
    return True


def windows_well_formed(obj) -> bool:
    return conforms(obj, WINDOWS)
