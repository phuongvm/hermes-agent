"""Behaviour contracts for skills/devops/intel-nuc-sentinel/scripts (no network: ssh is faked)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills" / "devops" / "intel-nuc-sentinel"
sys.path.insert(0, str(SKILL / "scripts"))

import sentinel
import sentinel_parse as P
from sentinel_probes import ConfigError, ReadOnlyViolation, assert_read_only, build_probes, remote_command, ssh_argv

CFG = json.loads((SKILL / "templates" / "intel-nuc.json").read_text(encoding="utf-8-sig"))


def sec(name, lines, rc=0):
    return f"## {name}\n" + "".join(f"{ln}\n" for ln in lines) + f"##rc {name} {rc}\n"


def container(name, state="running", restarts=0, health=None, ports=None, log_opts=None, size="4.1kB (virtual 1GB)"):
    cid = name * 3
    ps = {"id": cid, "name": name, "state": state, "status": "Up 1 hour", "size": size, "image": "img"}
    ins = {"id": cid, "name": "/" + name, "restart_count": restarts, "oom_killed": False, "health": health,
           "privileged": False, "log_type": "json-file", "log_opts": {"max-size": "50m"} if log_opts is None else log_opts,
           "log_path": f"/var/lib/docker/containers/{cid}/{cid}-json.log", "ports": ports or {},
           "mounts": [{"type": "bind", "source": f"/home/ubuntu/volumes/{name}", "dest": "/data"}]}
    return ps, ins


def healthy_raw(containers=None, c_free_win=200_000_000_000):
    containers = containers or [container("web"), container("db")]
    ps = [json.dumps(p) for p, _ in containers]
    ins = [json.dumps(i) for _, i in containers]
    running = sum(p["state"] == "running" for p, _ in containers)
    vh = CFG["vhdx"]
    linux = "".join([
        sec("epoch", ["1791885000"]), sec("uptime", ["100.0 50.0"]), sec("nproc", ["12"]),
        sec("loadavg", ["1.0 1.2 1.1 1/100 5"]),
        sec("meminfo", ["MemTotal:       32000000 kB", "MemAvailable:   16000000 kB"]),
        sec("df", ["Filesystem 1B-blocks Used Available Capacity Mounted on",
                   "/dev/sdc 1000000000000 100000000000 900000000000 10% /",
                   f"C:\\ 500000000000 300000000000 {c_free_win} 60% /mnt/c",
                   "D:\\ 900000000000 70000000000 830000000000 8% /mnt/d"]),
        sec("vhdx_stat", [f"40000000000 {vh[0]['wsl_path']}", f"160000000000 {vh[1]['wsl_path']}"]),
    ])
    engine = "".join([
        sec("docker_ps", ps),
        sec("docker_info", [json.dumps({"running": running, "total": len(containers), "server_version": "29", "driver": "overlayfs"})]),
        sec("docker_inspect", ins), sec("docker_df", []),
        *[sec(f"redis_ping:{r}", ["PONG"]) + sec(f"redis_persistence:{r}", ["rdb_last_bgsave_status:ok", "aof_enabled:1", "aof_last_write_status:ok"])
          + sec(f"redis_memory:{r}", ["used_memory:1000000", "maxmemory:0"]) for r in CFG["redis"]],
        sec("zookeeper_env", ["purge_interval=24", "snap_retain=3"]),
    ])
    vm = sec("df", ["Filesystem 1-blocks Used Available Capacity Mounted on",
                    "/dev/sdd 1000000000000 30000000000 970000000000 3% /mnt/docker-desktop-disk"]) + \
        sec("container_logs", [f"1024\t/mnt/docker-desktop-disk/data/docker/containers/{i['id']}/{i['id']}-json.log" for _, i in containers])
    ubuntu = "".join([
        sec("df", ["Filesystem 1-blocks Used Available Capacity Mounted on", "/dev/sdc 1000000000000 150000000000 850000000000 15% /"]),
        sec("volumes_du", [f"1000\t/home/ubuntu/volumes/{p['name']}" for p, _ in containers]),
        sec("data_du", [f"6000000\t{CFG['postgres']['data_host_path']}", f"6000000\t{CFG['clickhouse']['data_host_path']}",
                        f"100\t{CFG['zookeeper']['datalog_host_path']}"]),
        sec("sshd", ["passwordauthentication no", "permitrootlogin no", "pubkeyauthentication yes"]),
        sec("apt_upgradable", ["Listing..."]), sec("apt_cache_mtime", ["1791880000"]),
        sec("auto_upgrades", ['APT::Periodic::Unattended-Upgrade "1";']), sec("reboot_required", ["no"]),
    ])
    win = {"epoch": 1791885000, "uptime_s": 1000, "mem_total_kb": 64000000, "mem_free_kb": 30000000,
           "hotfix_id": "KB1", "hotfix_date": "2026-10-01", "defender_av": True, "defender_rtp": True,
           "defender_sig_age_days": 0, "firewall": [{"name": "Public", "enabled": "True"}],
           "disks": [{"id": "C:", "size": 500000000000, "free": c_free_win}, {"id": "D:", "size": 900000000000, "free": 830000000000}],
           "vhdx": [{"path": vh[0]["windows_path"], "bytes": 40000000000}, {"path": vh[1]["windows_path"], "bytes": 160000000000}],
           "reboot_pending": False}
    pg = "read_only|on\ndb|honcho|4000000000\ndb|dify|200000000\nconn|10|100\nlong_tx|0\nidle_in_tx|0\nxid_age|postgres|1000\nin_recovery|f\n"
    ch = "readonly\t1\nuptime\t100\ndb\tlangfuse\t3000000000\nparts_total\t3000000000\ndisk\tdefault\t1\t2\n"
    ok = lambda out: {"rc": 0, "stdout": out, "stderr": ""}
    return {"linux_host": ok(linux), "windows_host": ok(json.dumps(win)), "docker_engine": ok(engine),
            "docker_vm": ok(vm), "ubuntu_root": ok(ubuntu), "postgres": ok(pg), "clickhouse": ok(ch)}


def by_id(findings):
    return {f["id"]: f for f in findings}


# ------------------------------------------------------------------ read-only guard
def test_shipped_catalog_is_read_only_and_valid():
    probes = build_probes(CFG)
    assert {p.id for p in probes} == {"linux_host", "windows_host", "docker_engine", "docker_vm",
                                      "ubuntu_root", "postgres", "clickhouse"}
    for p in probes:
        argv = ssh_argv(CFG, remote_command(p, CFG))
        assert argv[:2] == ["ssh", "-o"] and argv[-2] == CFG["host"]


@pytest.mark.parametrize("text", [
    "rm -rf /home/ubuntu/volumes/x", "docker restart grafana", "docker system prune -af", "docker compose up -d",
    "echo 1 > /etc/docker/daemon.json", "apt update", "systemctl restart docker", "wsl.exe --shutdown",
    "DROP TABLE system.query_log", "TRUNCATE TABLE t", "ALTER TABLE x MODIFY TTL d", "VACUUM FULL",
    "redis-cli FLUSHALL", "redis-cli CONFIG SET maxmemory 1", "Optimize-VHD -Path C:\\x.vhdx",
    "Remove-Item C:\\x", "kill 1", "fstrim -v /", "sed -i s/a/b/ f",
])
def test_mutating_commands_are_refused(text):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(text, "t")


@pytest.mark.parametrize("text", ["cmd 2>&1", "cmd >/dev/null", "SELECT 1", "df -B1 -P /", "Get-HotFix"])
def test_reads_pass_the_guard(text):
    assert_read_only(text, "t")


@pytest.mark.parametrize("key,value", [
    ("host", "192.168.100.110; rm -rf /"), ("volume_root", "/home/$(id)"),
    ("ssh_options", ["-o", "ProxyCommand=sh -c x"]), ("ssh_options", ["-o", "LocalCommand=x"]),
])
def test_unsafe_config_values_are_refused(key, value):
    cfg = json.loads(json.dumps(CFG))
    cfg[key] = value
    with pytest.raises(ConfigError):
        for p in build_probes(cfg):
            ssh_argv(cfg, remote_command(p, cfg))


# ------------------------------------------------------------------ evaluation
def test_healthy_host_has_no_problem_findings_and_cross_checks_agree():
    f = by_id(sentinel.evaluate_raw(CFG, healthy_raw(), {}))
    assert {x["area"] for x in f.values()} >= {"docker", "storage", "db", "host", "security"}
    assert not [x for x in f.values() if x["status"] in ("CRIT", "WARN", "UNKNOWN")], \
        [(x["id"], x["status"]) for x in f.values() if x["status"] in ("CRIT", "WARN", "UNKNOWN")]
    for fid in ("DOCKER-LIVENESS", "STORAGE-DISK-MNT-C", "STORAGE-VHDX-DOCKER_DATA", "DB-PG-SIZE", "DB-CH-SIZE"):
        assert f[fid]["verified"] is True, fid


def test_stopped_container_is_critical_with_evidence():
    raw = healthy_raw([container("web"), container("db", state="exited")])
    f = by_id(sentinel.evaluate_raw(CFG, raw, {}))["DOCKER-LIVENESS"]
    assert f["status"] == "CRIT" and f["value"]["down"] == ["db"]
    assert "db|exited" in f["evidence"][0]["line"]


def test_failed_probe_section_reports_unknown_never_ok():
    raw = healthy_raw()
    raw["linux_host"]["stdout"] = raw["linux_host"]["stdout"].replace("##rc df 0", "##rc df 1")
    f = by_id(sentinel.evaluate_raw(CFG, raw, {}))
    assert f["STORAGE-DISK-ROOT"]["status"] == "UNKNOWN"
    assert f["COLLECT-LINUX_HOST"]["value"]["failed_sections"] == ["df"]


def test_unreachable_database_is_critical():
    raw = healthy_raw()
    raw["postgres"] = {"rc": 1, "stdout": "", "stderr": "connection refused"}
    f = by_id(sentinel.evaluate_raw(CFG, raw, {}))
    assert f["DB-PG-UP"]["status"] == "CRIT"
    assert "connection refused" in f["DB-PG-UP"]["evidence"][0]["line"]


def test_disagreeing_sources_are_flagged_not_reconciled():
    raw = healthy_raw()
    win = json.loads(raw["windows_host"]["stdout"])
    win["disks"][0]["free"] = 1_000_000_000  # Windows says 1 GB free, df says 200 GB
    raw["windows_host"]["stdout"] = json.dumps(win)
    f = by_id(sentinel.evaluate_raw(CFG, raw, {}))["STORAGE-DISK-MNT-C"]
    assert f["verified"] is False and f["value"]["free_gb"] == 200.0


def test_risky_posture_is_warned():
    db = container("db", ports={"5432/tcp": [{"HostIp": "", "HostPort": "5432"}]}, log_opts={})
    raw = healthy_raw([container("web"), db])
    raw["ubuntu_root"]["stdout"] = raw["ubuntu_root"]["stdout"].replace("passwordauthentication no", "passwordauthentication yes")
    raw["docker_engine"]["stdout"] = raw["docker_engine"]["stdout"].replace("purge_interval=24", "purge_interval=0")
    f = by_id(sentinel.evaluate_raw(CFG, raw, {}))
    assert f["SEC-DB-PORTS"]["status"] == "WARN" and "db 5432/tcp -> *:5432" in f["SEC-DB-PORTS"]["value"]
    assert f["SEC-SSHD"]["status"] == "WARN"
    assert f["DOCKER-LOG-ROTATION"]["value"]["containers"] == ["db"]
    assert f["DB-ZK-AUTOPURGE"]["status"] == "WARN"


def test_restart_delta_uses_baseline_and_survives_recreate():
    raw = healthy_raw([container("web", restarts=5), container("db", restarts=1)])
    f = by_id(sentinel.evaluate_raw(CFG, raw, {"restart_counts": {"web": 2, "db": 4}}))["DOCKER-RESTARTS"]
    assert f["status"] == "WARN" and f["value"] == {"web": 3, "db": 1}
    first = by_id(sentinel.evaluate_raw(CFG, raw, {}))["DOCKER-RESTARTS"]
    assert first["status"] == "INFO"


def test_evaluation_is_deterministic():
    raw = healthy_raw()
    assert sentinel.evaluate_raw(CFG, raw, {}) == sentinel.evaluate_raw(CFG, json.loads(json.dumps(raw)), {})


# ------------------------------------------------------------------ collection + evidence chain
class FakeSSH:
    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def __call__(self, argv, input, capture_output, timeout):
        self.calls.append((argv, input))
        probe = next(p.id for p in build_probes(CFG) if p.script.encode() == input)
        r = self.raw[probe]
        return subprocess.CompletedProcess(argv, r["rc"], r["stdout"].encode(), r["stderr"].encode())


def test_probe_scripts_are_sent_as_lf_bytes():
    # Text-mode stdin on Windows turned "\n" into "\r\n"; the remote sh then failed on `2>&1\r`.
    fake = FakeSSH(healthy_raw())
    for p in build_probes(CFG):
        sentinel.run_probe(p, CFG, runner=fake)
    for _, payload in fake.calls:
        assert isinstance(payload, bytes) and b"\r" not in payload


def test_timeout_is_recorded_as_failure():
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    r = sentinel.run_probe(build_probes(CFG)[0], CFG, runner=hang)
    assert r["rc"] == 124 and "timeout" in r["stderr"]


def test_run_dir_verifies_and_detects_tampering(tmp_path):
    run_dir = sentinel.collect(CFG, tmp_path, runner=FakeSSH(healthy_raw()))
    sentinel.evaluate(run_dir, tmp_path)
    assert sentinel.verify(run_dir) == []
    raw = json.loads((run_dir / "raw.json").read_text(encoding="utf-8-sig"))
    raw["linux_host"]["stdout"] = raw["linux_host"]["stdout"].replace("10% /", "99% /")
    (run_dir / "raw.json").write_text(json.dumps(raw), encoding="utf-8")
    problems = sentinel.verify(run_dir)
    assert any("raw.json" in p for p in problems)


def test_baseline_keeps_sections_a_failed_run_could_not_read(tmp_path):
    raw = healthy_raw([container("web", restarts=7)])
    prev = {"restart_counts": {"web": 1}, "pg_sizes": {"honcho": 1}, "ch_sizes": {"langfuse": 1}}
    raw["postgres"] = {"rc": 1, "stdout": "", "stderr": "down"}
    nb = sentinel.next_baseline(CFG, raw, prev)
    assert nb["restart_counts"] == {"web": 7} and nb["pg_sizes"] == {"honcho": 1}


def test_parsers():
    s = P.sections("## a\nx\n##rc a 0\n## b\ny\n")
    assert s["a"] == {"rc": 0, "lines": ["x"]} and s["b"]["rc"] is None
    assert P.human_bytes("2.8GB") == 2_800_000_000 and P.human_bytes("4.1kB") == 4100
    apt = P.apt_upgradable(["Listing...", "libx/noble-security 1 amd64 [upgradable from: 0]",
                            "vim/noble-updates 2 amd64 [upgradable from: 1]"])
    assert apt == {"count": 2, "security": 1, "security_pkgs": ["libx"]}
