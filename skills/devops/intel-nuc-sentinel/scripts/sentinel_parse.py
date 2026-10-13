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
