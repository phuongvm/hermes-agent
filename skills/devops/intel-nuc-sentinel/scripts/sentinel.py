#!/usr/bin/env python3
"""intel-nuc weekly sentinel: read-only collection, deterministic evaluation, evidence report.

  sentinel.py plan     [--config F]                 print every remote command; touch nothing
  sentinel.py collect  [--config F] [--out-dir D]   run probes over ssh -> <run>/raw.json
  sentinel.py evaluate --run <run dir>              raw.json -> findings.json + report.md
  sentinel.py run      [--config F] [--out-dir D]   collect + evaluate, print the report
  sentinel.py verify   --run <run dir>              hashes match + re-evaluation is identical

Exit codes: 0 report produced, 2 config refused (unsafe value or mutating probe), 3 host
unreachable, 4 verify mismatch. Severity lives in the report, not the exit code, so a cron
pre-run script still hands the report to the agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sentinel_checks as C
import sentinel_platform as PL
from sentinel_probes import ConfigError, ReadOnlyViolation, assert_read_only, build_probes, remote_command, ssh_argv

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_DIR / "templates" / "intel-nuc.json"
SCHEMA = 1


def default_out_dir() -> Path:
    home = os.environ.get("HERMES_HOME")
    return (Path(home) if home else Path.cwd()) / "sentinel" / "intel-nuc"


def load_config(path: Path) -> dict:
    cfg = load_json(path)
    # Validate every token, the read-only guard and the ssh argv before anything runs.
    for p in build_probes(cfg):
        assert_read_only(p.script, f"probe {p.id}")
        ssh_argv(cfg, remote_command(p, cfg))
    return cfg


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ collect
def run_probe(probe, cfg: dict, runner=subprocess.run) -> dict:
    argv = ssh_argv(cfg, remote_command(probe, cfg))
    started = time.time()
    timeout = cfg.get("probe_timeout_s", 600)
    try:
        # Bytes, not text mode: on Windows text-mode stdin turns "\n" into "\r\n" and the remote
        # shell then reads `2>&1\r` as a bad file descriptor.
        proc = runner(argv, input=probe.script.encode("utf-8"), capture_output=True, timeout=timeout)
        rc = proc.returncode
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timeout after {timeout}s"
    except OSError as exc:
        rc, out, err = 127, "", f"cannot start ssh: {exc}"
    return {"rc": rc, "stdout": out.replace("\r\n", "\n"), "stderr": err[-4000:],
            "argv": argv, "script_sha256": hashlib.sha256(probe.script.encode()).hexdigest(),
            "started": round(started, 3), "seconds": round(time.time() - started, 2)}


def collect(cfg: dict, out_dir: Path, runner=subprocess.run) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = out_dir / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    raw = {p.id: run_probe(p, cfg, runner) for p in build_probes(cfg)}
    dump(run_dir / "raw.json", raw)
    dump(run_dir / "config.json", cfg)
    baseline = out_dir / "baseline.json"
    dump(run_dir / "baseline.json", load_json(baseline) if baseline.exists() else {})
    return run_dir


# ------------------------------------------------------------------ evaluate
def evaluate_raw(cfg: dict, raw: dict, baseline: dict) -> list[dict]:
    ctx = C.Ctx(cfg, raw, baseline)
    findings = [*C.check_collection(ctx), *C.check_docker(ctx), *PL.check_storage(ctx),
                *PL.check_db(ctx), *PL.check_host(ctx), *PL.check_security(ctx)]
    return sorted(findings, key=lambda f: (C.ORDER[f["status"]], f["area"], f["id"]))


def next_baseline(cfg: dict, raw: dict, prev: dict) -> dict | None:
    """State the next run diffs against. A section this run could not read keeps the previous
    value, so one failed probe never resets week-over-week deltas. None: nothing new to record."""
    ctx = C.Ctx(cfg, raw, {})
    pg, ch = ctx.sql("postgres", "|"), ctx.sql("clickhouse", "\t")
    new = dict(prev)
    if ctx.containers is not None:
        new["restart_counts"] = {r["name"]: r["inspect"].get("restart_count", 0) for r in ctx.containers}
    if pg and pg.get("db"):
        new["pg_sizes"] = {r[0]: int(r[1]) for r in pg["db"]}
    if ch and ch.get("db"):
        new["ch_sizes"] = {r[0]: int(r[1]) for r in ch["db"]}
    return new if new != prev else None


def render(findings: list[dict], meta: dict) -> str:
    counts = {s: sum(f["status"] == s for f in findings) for s in C.ORDER}
    lines = [f"# intel-nuc sentinel report {meta['run']}", "",
             f"Host {meta['host']}. Read-only probes: {meta['probes']}. Raw evidence: `raw.json` sha256 {meta['raw_sha256'][:16]}.",
             "Counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()), "",
             "| Status | ID | Finding | Value | Cross-check |", "|---|---|---|---|---|"]
    for f in findings:
        xc = {True: "agrees", False: "MISMATCH", None: "-"}[f["verified"]]
        val = json.dumps(f["value"], sort_keys=True, ensure_ascii=False)
        lines.append(f"| {f['status']} | {f['id']} | {f['title']} | `{val[:160]}` | {xc} |")
    lines += ["", "## Evidence (non-OK findings)", ""]
    for f in findings:
        if f["status"] == "OK":
            continue
        lines.append(f"- **{f['id']}** {f['note']}".rstrip())
        lines += [f"  - `{e['probe']}/{e['section']}`: `{e['line'].splitlines()[0][:200] if e['line'] else ''}`" for e in f["evidence"][:6]]
    lines += ["", "No action was taken on the host. Every remediation needs owner approval."]
    return "\n".join(lines) + "\n"


def evaluate(run_dir: Path, out_dir: Path | None = None) -> tuple[list[dict], str]:
    cfg = load_json(run_dir / "config.json")
    raw = load_json(run_dir / "raw.json")
    baseline = load_json(run_dir / "baseline.json")
    findings = evaluate_raw(cfg, raw, baseline)
    meta = {"run": run_dir.name, "host": cfg["host"], "probes": len(raw), "raw_sha256": sha256(run_dir / "raw.json")}
    dump(run_dir / "findings.json", {"schema": SCHEMA, "meta": meta, "findings": findings})
    report = render(findings, meta)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    dump(run_dir / "manifest.json", {n: sha256(run_dir / n) for n in
                                     ("raw.json", "config.json", "baseline.json", "findings.json", "report.md")})
    nb = next_baseline(cfg, raw, baseline)
    if out_dir is not None and nb is not None:
        dump(out_dir / "baseline.json", nb)
    return findings, report


def verify(run_dir: Path) -> list[str]:
    problems = []
    manifest = load_json(run_dir / "manifest.json")
    for name, digest in sorted(manifest.items()):
        if sha256(run_dir / name) != digest:
            problems.append(f"{name}: sha256 differs from manifest")
    cfg = load_json(run_dir / "config.json")
    raw = load_json(run_dir / "raw.json")
    baseline = load_json(run_dir / "baseline.json")
    stored = load_json(run_dir / "findings.json")["findings"]
    if evaluate_raw(cfg, raw, baseline) != stored:
        problems.append("findings.json differs from a fresh evaluation of raw.json")
    for pid, r in sorted(raw.items()):
        probe = next((p for p in build_probes(cfg) if p.id == pid), None)
        if probe is None or hashlib.sha256(probe.script.encode()).hexdigest() != r["script_sha256"]:
            problems.append(f"{pid}: probe script differs from the catalog that config builds")
    return problems


# ------------------------------------------------------------------ cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "collect", "evaluate", "run", "verify"])
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--run", type=Path, help="run directory for evaluate/verify")
    a = ap.parse_args(argv)
    if a.command in ("evaluate", "verify"):
        if not a.run:
            ap.error(f"{a.command} needs --run")
        if a.command == "verify":
            problems = verify(a.run)
            print("\n".join(problems) if problems else f"VERIFY OK {a.run}")
            return 4 if problems else 0
        print(evaluate(a.run)[1])
        return 0
    try:
        cfg = load_config(a.config)
    except (ConfigError, ReadOnlyViolation, KeyError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if a.command == "plan":
        for p in build_probes(cfg):
            print(f"### {p.id}: {' '.join(ssh_argv(cfg, remote_command(p, cfg)))}\n{p.script}")
        return 0
    out_dir = a.out_dir or default_out_dir()
    run_dir = collect(cfg, out_dir)
    raw = load_json(run_dir / "raw.json")
    if all(r["rc"] == 255 for r in raw.values()):
        print(f"UNREACHABLE: ssh to {cfg['host']} failed for every probe; see {run_dir / 'raw.json'}", file=sys.stderr)
        return 3
    if a.command == "collect":
        print(run_dir)
        return 0
    print(evaluate(run_dir, out_dir)[1])
    print(f"Run directory: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
