"""Fail-closed allowlist for probe scripts.

The denylist in ``sentinel_probes`` catches known mutating spellings; this module is the
primary gate. A probe passes only when every command it would run is one this module knows to
be read-only, so a command nobody thought of is refused instead of executed:

- bash transports: the script must be a sequence of ``_section`` blocks, and every simple
  command inside a block must use an allowlisted executable with allowlisted arguments.
- SQL transports: every statement must be a ``SELECT`` that calls only allowlisted functions.
- powershell: every command-position word must be an allowlisted ``Get-*``/pipeline cmdlet,
  and every type literal, static member and method call must be allowlisted.
"""
from __future__ import annotations

import re
import shlex


class ReadOnlyViolation(ValueError):
    """A probe or remote command line is not provably read-only."""


# ------------------------------------------------------------------ helpers
def _scan_unquoted(text: str, where: str, forbidden: str, *, quotes: str = "'\"", dq_forbidden: str = "") -> str:
    """Return ``text`` with quoted spans blanked; refuse any ``forbidden`` char outside quotes.

    Only ASCII is accepted: PowerShell also honours typographic quotes, which would desync this
    scanner from the real parser. ``dq_forbidden``: chars the target still expands inside
    double quotes (bash ``$``, backtick, backslash).
    """
    if not text.isascii():
        raise ReadOnlyViolation(f"{where}: refused, non-ASCII text")
    out, quote = [], None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
                out.append(ch)
            elif quote == '"' and ch in dq_forbidden:
                raise ReadOnlyViolation(f"{where}: refused, {ch!r} inside double quotes")
            else:
                out.append(" ")
            continue
        if ch in quotes:
            quote = ch
            out.append(ch)
            continue
        if ch in forbidden:
            raise ReadOnlyViolation(f"{where}: refused, {ch!r} outside quotes")
        out.append(ch)
    if quote:
        raise ReadOnlyViolation(f"{where}: refused, unbalanced quote")
    return "".join(out)


# ------------------------------------------------------------------ bash
_SECTION = re.compile(
    r"echo '## (?P<name>[A-Za-z0-9_:.-]+)'\n\{ (?P<cmd>[^\n]+) ; \} 2>&1\necho \"##rc (?P=name) \$\?\"\n")
_STDERR_NULL = re.compile(r"(?<!\S)2>/dev/null(?!\S)")
_SEPARATORS = {"|", "||", "&&", ";"}
_SHELL_KEYWORDS = {"if", "then", "else", "fi"}
_REDIS_READ = {"PING", "INFO"}


def _bash_segments(cmd: str, where: str) -> list[list[str]]:
    cmd = _STDERR_NULL.sub(" ", cmd)
    _scan_unquoted(cmd, where, "$`\\#", dq_forbidden="$`\\")
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    segments, cur = [], []
    for tok in lex:
        if tok in _SEPARATORS:
            segments.append(cur)
            cur = []
        elif tok and set(tok) <= set("();<>|&"):
            raise ReadOnlyViolation(f"{where}: refused, shell operator {tok!r}")
        elif not cur and tok in _SHELL_KEYWORDS:
            continue
        else:
            cur.append(tok)
    segments.append(cur)
    return [s for s in segments if s]


def _check_docker(args: list[str], where: str) -> None:
    if args[:1] in (["ps"], ["info"], ["inspect"]) or args[:2] == ["system", "df"]:
        return
    if args[:1] == ["exec"] and len(args) >= 3 and not args[1].startswith("-"):
        inner = args[2:]
        if inner[0] == "redis-cli" and 2 <= len(inner) <= 3 and inner[1].upper() in _REDIS_READ:
            return
        if inner[:2] == ["sh", "-c"] and len(inner) == 3:
            # `sh -c` payload: only echo of parameter expansions, never command substitution.
            payload = inner[2]
            if "$(" in payload or "`" in payload:
                raise ReadOnlyViolation(f"{where}: refused, command substitution in sh -c payload")
            for seg in _bash_segments(payload.replace("$", ""), where):
                if seg[0] != "echo":
                    raise ReadOnlyViolation(f"{where}: refused, sh -c payload runs {seg[0]!r}")
            return
    raise ReadOnlyViolation(f"{where}: refused, docker {' '.join(args[:3])!r} is not an allowlisted read")


def _check_xargs(args: list[str], where: str) -> None:
    rest = [a for a in args if a not in ("-r", "--no-run-if-empty")]
    if not rest or rest[0].startswith("-"):
        raise ReadOnlyViolation(f"{where}: refused, xargs options {args[:3]!r}")
    _check_simple(rest, where)


_BASH_RULES = {
    "echo": lambda a, w: None,
    "test": lambda a, w: None,
    "cat": lambda a, w: _require(bool(a) and all(x.startswith("/") for x in a), w, "cat only of absolute paths"),
    "grep": lambda a, w: None,
    "nproc": lambda a, w: _require(not a, w, "nproc takes no arguments"),
    "date": lambda a, w: _require(all(x.startswith("+") for x in a), w, "date may only format (+FMT)"),
    "df": lambda a, w: _require(all(x in ("-B1", "-P") or x.startswith("/") for x in a), w, "df args"),
    "du": lambda a, w: _require(all(x in ("-sk", "-skx") or x.startswith("/") for x in a), w, "du args"),
    "stat": lambda a, w: _require(a[:1] == ["-c"] and all(x.startswith("/") for x in a[2:]), w, "stat args"),
    "sshd": lambda a, w: _require(a == ["-T"], w, "sshd only as `sshd -T`"),
    "apt": lambda a, w: _require(a == ["list", "--upgradable"], w, "apt only as `apt list --upgradable`"),
    "docker": _check_docker,
    "xargs": _check_xargs,
}


def _require(ok: bool, where: str, why: str) -> None:
    if not ok:
        raise ReadOnlyViolation(f"{where}: refused, {why}")


def _check_simple(seg: list[str], where: str) -> None:
    rule = _BASH_RULES.get(seg[0])
    if rule is None:
        raise ReadOnlyViolation(f"{where}: refused, executable {seg[0]!r} is not allowlisted")
    rule(seg[1:], where)


def check_bash_script(script: str, where: str) -> None:
    pos = 0
    for m in _SECTION.finditer(script):
        if m.start() != pos:
            raise ReadOnlyViolation(f"{where}: refused, text outside a section block at offset {pos}")
        for seg in _bash_segments(m.group("cmd"), f"{where}/{m.group('name')}"):
            _check_simple(seg, f"{where}/{m.group('name')}")
        pos = m.end()
    if pos != len(script) or not script:
        raise ReadOnlyViolation(f"{where}: refused, text outside a section block at offset {pos}")


# ------------------------------------------------------------------ sql
_SQL_FUNCS = {"current_setting", "pg_database_size", "count", "now", "age", "pg_is_in_recovery",
              "getsetting", "uptime", "sum"}
_SQL_KEYWORDS_BEFORE_PAREN = {"in", "exists", "and", "or", "not", "select", "from", "where", "as"}
# INTO OUTFILE / SELECT INTO, per-query SETTINGS overrides (readonly=0), FOR UPDATE/SHARE locks, comments.
_SQL_FORBIDDEN = re.compile(r"(?i)\binto\b|\bsettings\b|\bfor\b|--|/\*")


def check_sql(script: str, where: str) -> None:
    # ClickHouse treats backslash as an escape inside '...', Postgres (standard strings) does not,
    # so `\'` could end a literal for one and not the other. Only LIKE escapes (\_ \%) and an
    # escaped backslash pair are accepted. Backticks, double quotes and $ are identifier /
    # dollar quotes the scanner does not model.
    if re.search(r"\\(?![\\_%])", re.sub(r"\\\\", "", script)):
        raise ReadOnlyViolation(f"{where}: refused, backslash escape other than \\_ or \\% in SQL")
    bare = _scan_unquoted(script, where, "`\"$", quotes="'")
    bad = _SQL_FORBIDDEN.search(bare)
    if bad:
        raise ReadOnlyViolation(f"{where}: refused, {bad.group(0)!r} outside quotes")
    stmts = [s.strip() for s in bare.split(";") if s.strip()]
    if not stmts:
        raise ReadOnlyViolation(f"{where}: refused, empty SQL")
    for s in stmts:
        if not re.match(r"(?i)select\b", s):
            raise ReadOnlyViolation(f"{where}: refused, statement is not a SELECT: {s[:40]!r}")
        for fn in re.findall(r"\b([A-Za-z_]\w*)\s*\(", s):
            if fn.lower() not in _SQL_FUNCS | _SQL_KEYWORDS_BEFORE_PAREN:
                raise ReadOnlyViolation(f"{where}: refused, SQL function {fn!r} is not allowlisted")


# ------------------------------------------------------------------ powershell
_PS_CMDLETS = {"Get-CimInstance", "Get-HotFix", "Get-MpComputerStatus", "Get-NetFirewallProfile", "Get-Item",
               "Get-Date", "Test-Path", "Where-Object", "Sort-Object", "Select-Object", "ForEach-Object",
               "ConvertTo-Json"}
_PS_KEYWORDS = {"if", "else"}
_PS_BLOCK_ONLY = {"ForEach-Object", "Where-Object"}
_PS_TYPES = {"pscustomobject", "string", "int64", "bool", "DateTimeOffset"}
_PS_STATIC = {"UtcNow"}
_PS_METHODS = {"ToString", "ToUnixTimeSeconds"}
# `name=` is a hashtable key inside @{...} but a command plus an "=" argument at statement
# start, so only these known output keys may be followed by "=".
_PS_KEYS = {"id", "size", "free", "path", "bytes", "name", "enabled", "epoch", "uptime_s", "mem_total_kb",
            "mem_free_kb", "hotfix_id", "hotfix_date", "defender_av", "defender_rtp", "defender_sig_age_days",
            "firewall", "disks", "vhdx", "reboot_pending"}


def check_powershell(script: str, where: str) -> None:
    if "@'" in script or "'@" in script:
        raise ReadOnlyViolation(f"{where}: refused, here-string")
    if re.search(r"\$\{|\$\w+:", script):
        # ${C:\f} = 'x' and $function:x = ... write through PowerShell providers.
        raise ReadOnlyViolation(f"{where}: refused, provider-qualified variable")
    # & and . invoke, backtick escapes, > redirects, # comments could hide a quote from the scanner.
    bare = _scan_unquoted(script, where, "&`<>#\"", quotes="'")
    for t in re.findall(r"\[([A-Za-z_][\w.]*)\]", bare):
        _require(t in _PS_TYPES, where, f"type literal [{t}] is not allowlisted")
    for s in re.findall(r"::\s*([A-Za-z_]\w*)", bare):
        _require(s in _PS_STATIC, where, f"static member ::{s} is not allowlisted")
    for m in re.findall(r"\.([A-Za-z_]\w*)\s*\(", bare):
        _require(m in _PS_METHODS, where, f"method .{m}() is not allowlisted")
    # Dynamic member names (.'Delete'(), .$m(), .(expr)) hide the method from the check above.
    _require(not re.search(r"\.\s*['$(]", bare), where, "dynamic member access")
    # Command position: line start or after | ; { ( =. Whatever starts there is either an
    # expression ($var, [type], (, @, ', {, }, )) or a word that must be allowlisted; aliases
    # such as `%` or `iex` are words too, so they are refused.
    # Lookaheads only, so the `=` after a hashtable key still starts a new command position
    # (`@{a=Remove-Item x}` runs Remove-Item).
    for m in re.finditer(r"(?:^|[|;{(=])[ \t]*([^\s$\[(@'{})][^\s|;{}()=,]*)(?=(\s*=(?!=))?)(?=[ \t]*(\S?))",
                         bare, re.MULTILINE):
        word, is_key, nxt = m.group(1), m.group(2), m.group(3)
        if is_key:
            _require(word in _PS_KEYS, where, f"{word!r} followed by '=' is not an allowlisted hashtable key")
            continue
        _require(word in _PS_CMDLETS or word in _PS_KEYWORDS, where, f"command {word!r} is not allowlisted")
        if word in _PS_BLOCK_ONLY:
            # `ForEach-Object Delete` invokes a method by name; only a script block is allowed.
            _require(nxt == "{", where, f"{word} must take a script block")


CHECKERS = {
    "bash": check_bash_script,
    "wsl_ubuntu_root": check_bash_script,
    "wsl_docker": check_bash_script,
    "psql": check_sql,
    "clickhouse": check_sql,
    "powershell": check_powershell,
}


def check_probe_script(transport: str, script: str, where: str) -> None:
    checker = CHECKERS.get(transport)
    if checker is None:
        raise ReadOnlyViolation(f"{where}: refused, no allowlist for transport {transport!r}")
    checker(script, where)
