"""Evidence locators: every cited line is an exact line of the stored raw probe output.

An evidence item is ``{probe, section, line_no, line}``; ``raw_lines(<source>)[line_no] == line``
must hold, where the source is the probe's stdout (or stderr when ``section == "stderr"``) and
``line_no`` must sit inside the cited section. ``section == "status"`` is the one exception: it
records that a probe or section produced no output at all, so no raw line exists to quote, and
it may only back an UNKNOWN or CRIT finding.
"""
from __future__ import annotations

from functools import lru_cache

import sentinel_parse as P

BASH_PROBES = ("linux_host", "docker_engine", "docker_vm", "ubuntu_root")
SQL_SEP = {"postgres": "|", "clickhouse": "\t"}
STATUS, STDERR, STDOUT, JSON = "status", "stderr", "stdout", "json"


@lru_cache(maxsize=32)
def _lines(text: str) -> tuple[str, ...]:
    return tuple(P.raw_lines(text))


@lru_cache(maxsize=32)
def _blocks(text: str) -> dict[str, dict]:
    return P.sections(text)


def allowed(raw: dict, probe: str, section: str) -> tuple[tuple[str, ...], range | list[int]]:
    """The raw source lines a locator indexes and the indexes the section may cite."""
    r = raw.get(probe) or {}
    if section == STDERR:
        lines = _lines(r.get("stderr") or "")
        return lines, range(len(lines))
    lines = _lines(r.get("stdout") or "")
    if section == STDOUT or (section == JSON and probe == "windows_host"):
        return lines, range(len(lines))
    if probe in SQL_SEP:
        head = section + SQL_SEP[probe]
        return lines, [i for i, ln in enumerate(lines) if ln.startswith(head)]
    if probe in BASH_PROBES:
        blk = _blocks(r.get("stdout") or "").get(section)
        return lines, (range(blk["start"], blk["end"]) if blk else range(0))
    return lines, range(0)


def locate(raw: dict, probe: str, section: str, line: str) -> int | None:
    lines, idx = allowed(raw, probe, section)
    return next((i for i in idx if lines[i] == line), None)


def evidence_problems(raw: dict, findings: list[dict]) -> list[str]:
    """Every cited line must be the raw line at its locator; status-only evidence cannot back OK/INFO/WARN."""
    problems = []
    for f in findings:
        evid = f.get("evidence") or []
        if not evid:
            problems.append(f"{f['id']}: no evidence")
        for e in evid:
            where = f"{f['id']}: {e.get('probe')}/{e.get('section')}"
            if e.get("section") == STATUS:
                if f["status"] not in ("UNKNOWN", "CRIT"):
                    problems.append(f"{where}: status-only evidence on a {f['status']} finding")
                continue
            lines, idx = allowed(raw, e.get("probe"), e.get("section"))
            n = e.get("line_no")
            if not isinstance(n, int) or n not in idx or lines[n] != e.get("line"):
                problems.append(f"{where}: line {n} is not the cited raw line {str(e.get('line'))[:80]!r}")
    return problems
