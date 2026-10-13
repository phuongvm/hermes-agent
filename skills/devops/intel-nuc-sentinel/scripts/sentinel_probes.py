"""Probe catalog for the intel-nuc sentinel.

Every remote command is built here from fixed templates plus config values that must match
SAFE_TOKEN, and every script and remote command line is checked by ``assert_read_only``
before anything leaves the machine. Nothing in this module talks to the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_./:+@-]+$")
SAFE_WIN_PATH = re.compile(r"^[A-Za-z]:\\[A-Za-z0-9_.\\ ()-]+$")
# ssh -o values; never ProxyCommand/LocalCommand (those execute commands locally).
SAFE_SSH_OPT = re.compile(r"^(?!.*(?i:proxycommand|localcommand|permitlocalcommand))-?[A-Za-z0-9_.=:/@-]+$")

# Accidental-mutation guard. A probe that matches any of these is refused before it runs.
_MUTATING = [
    r"(?:^|[\s;&|({])(?:rm|rmdir|mv|cp|chmod|chown|chattr|truncate|dd|mkfs\S*|shutdown|reboot"
    r"|poweroff|halt|kill|pkill|killall|tee|fstrim|ln|touch|mkdir)\s",
    r"\bsed\s+-i",
    r"\bdocker\s+(?:rm|rmi|stop|start|restart|kill|pause|unpause|update|prune|compose|run|create"
    r"|cp|commit|tag|push|pull|build|network|volume|image|container|builder|system\s+prune)\b",
    r"\bsystemctl\s+(?!is-active\b|is-failed\b|status\b|--failed\b|list-)\S+",
    r"\bapt(?:-get)?\s+(?:install|remove|purge|update|upgrade|full-upgrade|dist-upgrade"
    r"|autoremove|clean)\b",
    r"\bwsl(?:\.exe)?\b[^\n]*\s(?:--shutdown|--terminate|-t|--unregister|--manage|--set-\S+"
    r"|--import|--export|--mount|--unmount)\b",
    r">>?\s*(?!/dev/null|&)\S",
    r"(?i)\b(?:insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+\w+|alter\s+\w+"
    r"|create\s+\w+|truncate\s+table|optimize\s+table|vacuum|reindex|grant|revoke"
    r"|attach\s+\w+|detach\s+\w+|kill\s+query|system\s+(?:drop|flush|stop|start|reload|restart))\b",
    r"(?i)\b(?:flushall|flushdb|config\s+set|bgrewriteaof|bgsave|debug\s+\w+)\b",
    r"\b(?:Set|Remove|New|Stop|Restart|Start|Optimize|Clear|Move|Rename|Invoke|Mount|Dismount"
    r"|Resize|Disable|Enable|Install|Uninstall|Update|Add|Register|Unregister|Suspend|Resume"
    r"|Write|Out|Copy|Reset|Repair)-[A-Za-z]\w*",
]
MUTATING_PATTERNS = [re.compile(p, re.MULTILINE) for p in _MUTATING]

DB_PORTS = {"5432", "6379", "6479", "8123", "9000", "7687", "7474", "2181", "9092", "6333", "6334", "8529"}


class ReadOnlyViolation(ValueError):
    """A probe or remote command line matched a mutating pattern."""


class ConfigError(ValueError):
    """A config value is unsafe to interpolate into a remote command."""


@dataclass(frozen=True)
class Probe:
    id: str
    transport: str
    script: str
    container: str = ""


def assert_read_only(text: str, where: str) -> None:
    for pat in MUTATING_PATTERNS:
        m = pat.search(text)
        if m:
            raise ReadOnlyViolation(f"{where}: refused, mutating pattern {m.group(0).strip()!r}")


def token(value: str, key: str) -> str:
    if not isinstance(value, str) or not SAFE_TOKEN.match(value):
        raise ConfigError(f"config {key}={value!r} is not a safe token")
    return value


def win_path(value: str, key: str) -> str:
    if not isinstance(value, str) or not SAFE_WIN_PATH.match(value):
        raise ConfigError(f"config {key}={value!r} is not a safe Windows path")
    return value


def _section(name: str, cmd: str) -> str:
    return f"echo '## {name}'\n{{ {cmd} ; }} 2>&1\necho \"##rc {name} $?\"\n"


_DOCKER_PS_FMT = (
    '{"id":{{json .ID}},"name":{{json .Names}},"state":{{json .State}},'
    '"status":{{json .Status}},"size":{{json .Size}},"image":{{json .Image}}}'
)
# Selected fields only: a raw `docker inspect` dumps Config.Env, which carries secrets.
_DOCKER_INSPECT_FMT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"restart_count":{{json .RestartCount}},'
    '"oom_killed":{{json .State.OOMKilled}},'
    # `index` returns nil for a missing key; `.State.Health` errors on containers without a healthcheck.
    '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},'
    '"privileged":{{json .HostConfig.Privileged}},"log_type":{{json .HostConfig.LogConfig.Type}},'
    '"log_opts":{{json .HostConfig.LogConfig.Config}},"log_path":{{json .LogPath}},"ports":{{json .HostConfig.PortBindings}},'
    '"mounts":[{{range $i, $m := .Mounts}}{{if $i}},{{end}}{"type":{{json $m.Type}},'
    '"source":{{json $m.Source}},"dest":{{json $m.Destination}}}{{end}}]}'
)
_DOCKER_INFO_FMT = (
    '{"running":{{json .ContainersRunning}},"total":{{json .Containers}},'
    '"server_version":{{json .ServerVersion}},"driver":{{json .Driver}}}'
)


def _linux_host(cfg: dict) -> Probe:
    mounts = " ".join(token(m, "linux_fs") for m in cfg["linux_fs"])
    vhdx = " ".join(token(v["wsl_path"], "vhdx.wsl_path") for v in cfg["vhdx"])
    script = "".join([
        _section("epoch", "date +%s"),
        _section("uptime", "cat /proc/uptime"),
        _section("nproc", "nproc"),
        _section("loadavg", "cat /proc/loadavg"),
        _section("meminfo", "cat /proc/meminfo"),
        _section("df", f"df -B1 -P {mounts}"),
        _section("vhdx_stat", f"stat -c '%s %n' {vhdx}"),
    ])
    return Probe("linux_host", "bash", script)


def _docker_engine(cfg: dict) -> Probe:
    parts = [
        _section("docker_ps", f"docker ps -a --size --no-trunc --format '{_DOCKER_PS_FMT}'"),
        _section("docker_info", f"docker info --format '{_DOCKER_INFO_FMT}'"),
        _section("docker_inspect",
                 f"docker ps -aq --no-trunc | xargs -r docker inspect --format '{_DOCKER_INSPECT_FMT}'"),
        _section("docker_df", "docker system df --format '{{json .}}'"),
    ]
    for name in cfg["redis"]:
        c = token(name, "redis")
        parts.append(_section(f"redis_ping:{c}", f"docker exec {c} redis-cli PING"))
        parts.append(_section(f"redis_persistence:{c}", f"docker exec {c} redis-cli INFO persistence"))
        parts.append(_section(f"redis_memory:{c}", f"docker exec {c} redis-cli INFO memory"))
    zk = token(cfg["zookeeper"]["container"], "zookeeper.container")
    parts.append(_section(
        "zookeeper_env",
        f"docker exec {zk} sh -c 'echo purge_interval=${{ZOO_AUTOPURGE_PURGEINTERVAL-unset}}; "
        f"echo snap_retain=${{ZOO_AUTOPURGE_SNAPRETAINCOUNT-unset}}'"))
    return Probe("docker_engine", "bash", "".join(parts))


def _docker_vm(cfg: dict) -> Probe:
    mnt = token(cfg["docker_disk_mount"], "docker_disk_mount")
    script = "".join([
        _section("df", f"df -B1 -P {mnt}"),
        _section("container_logs", f"du -sk {mnt}/data/docker/containers/*/*-json.log"),
    ])
    return Probe("docker_vm", "wsl_docker", script)


def _ubuntu_root(cfg: dict) -> Probe:
    root = token(cfg["volume_root"], "volume_root")
    data = " ".join([
        token(cfg["postgres"]["data_host_path"], "postgres.data_host_path"),
        token(cfg["clickhouse"]["data_host_path"], "clickhouse.data_host_path"),
        token(cfg["zookeeper"]["datalog_host_path"], "zookeeper.datalog_host_path"),
    ])
    sshd_keys = "passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication|permitemptypasswords"
    script = "".join([
        _section("df", "df -B1 -P /"),
        _section("volumes_du", f"du -skx {root}/*"),
        _section("data_du", f"du -skx {data}"),
        _section("sshd", f"sshd -T 2>/dev/null | grep -Ei '^({sshd_keys}) '"),
        _section("apt_upgradable", "apt list --upgradable 2>/dev/null"),
        _section("apt_cache_mtime", "stat -c %Y /var/cache/apt/pkgcache.bin"),
        _section("auto_upgrades", "cat /etc/apt/apt.conf.d/20auto-upgrades"),
        _section("reboot_required", "if test -e /var/run/reboot-required; then echo yes; else echo no; fi"),
    ])
    return Probe("ubuntu_root", "wsl_ubuntu_root", script)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_host(cfg: dict) -> Probe:
    vhdx = ",".join(_ps_quote(win_path(v["windows_path"], "vhdx.windows_path")) for v in cfg["vhdx"])
    reboot_keys = [
        r"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
        r"HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
    ]
    lines = [
        "$os = Get-CimInstance Win32_OperatingSystem",
        "$hf = Get-HotFix | Where-Object { $_.InstalledOn } | Sort-Object InstalledOn -Descending | Select-Object -First 1",
        "$mp = Get-MpComputerStatus",
        "$fw = @(Get-NetFirewallProfile | ForEach-Object { [pscustomobject]@{name=[string]$_.Name; enabled=[string]$_.Enabled} })",
        "$disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { [pscustomobject]@{id=$_.DeviceID; size=[int64]$_.Size; free=[int64]$_.FreeSpace} })",
        f"$vhdx = @({vhdx} | ForEach-Object {{ [pscustomobject]@{{path=$_; bytes=[int64](Get-Item -LiteralPath $_).Length}} }})",
        "$reboot = " + " -or ".join(f"(Test-Path {_ps_quote(k)})" for k in reboot_keys),
        "[pscustomobject]@{epoch=[int64]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()); "
        "uptime_s=[int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds; "
        "mem_total_kb=[int64]$os.TotalVisibleMemorySize; mem_free_kb=[int64]$os.FreePhysicalMemory; "
        "hotfix_id=[string]$hf.HotFixID; hotfix_date=$(if ($hf) { $hf.InstalledOn.ToString('yyyy-MM-dd') } else { $null }); "
        "defender_av=$mp.AntivirusEnabled; defender_rtp=$mp.RealTimeProtectionEnabled; "
        "defender_sig_age_days=$mp.AntivirusSignatureAge; firewall=$fw; disks=$disks; vhdx=$vhdx; "
        "reboot_pending=[bool]$reboot} | ConvertTo-Json -Compress -Depth 4",
    ]
    return Probe("windows_host", "powershell", "\n".join(lines) + "\n")


_PG_SQL = """\
SELECT 'read_only', current_setting('default_transaction_read_only');
SELECT 'db', datname, pg_database_size(datname) FROM pg_database ORDER BY datname;
SELECT 'conn', (SELECT count(*) FROM pg_stat_activity), current_setting('max_connections');
SELECT 'long_tx', count(*) FROM pg_stat_activity WHERE xact_start < now() - interval '1 hour';
SELECT 'idle_in_tx', count(*) FROM pg_stat_activity WHERE state LIKE 'idle in transaction%';
SELECT 'xid_age', datname, age(datfrozenxid) FROM pg_database ORDER BY age(datfrozenxid) DESC LIMIT 1;
SELECT 'in_recovery', pg_is_in_recovery();
"""

_CH_SQL = """\
SELECT 'readonly', getSetting('readonly') FORMAT TSV;
SELECT 'uptime', uptime() FORMAT TSV;
SELECT 'db', database, sum(bytes_on_disk) FROM system.parts WHERE active GROUP BY database ORDER BY database FORMAT TSV;
SELECT 'parts_total', sum(bytes_on_disk) FROM system.parts WHERE active FORMAT TSV;
SELECT 'log_no_ttl', name, total_bytes FROM system.tables WHERE database = 'system' AND name LIKE '%\\_log%' AND engine_full NOT LIKE '%TTL%' ORDER BY name FORMAT TSV;
SELECT 'disk', name, free_space, total_space FROM system.disks ORDER BY name FORMAT TSV;
"""


def build_probes(cfg: dict) -> list[Probe]:
    probes = [
        _linux_host(cfg),
        _windows_host(cfg),
        _docker_engine(cfg),
        _docker_vm(cfg),
        _ubuntu_root(cfg),
        Probe("postgres", "psql", _PG_SQL, token(cfg["postgres"]["container"], "postgres.container")),
        Probe("clickhouse", "clickhouse", _CH_SQL, token(cfg["clickhouse"]["container"], "clickhouse.container")),
    ]
    for p in probes:
        assert_read_only(p.script, f"probe {p.id}")
    return probes


def remote_command(probe: Probe, cfg: dict) -> str:
    wsl = token(cfg["wsl_exe"], "wsl_exe")
    builders = {
        "bash": lambda: "bash --noprofile --norc -s",
        "wsl_ubuntu_root": lambda: f"cd /mnt/c && {wsl} -d {token(cfg['ubuntu_distro'], 'ubuntu_distro')} -u root -e sh -s",
        "wsl_docker": lambda: f"cd /mnt/c && {wsl} -d {token(cfg['docker_distro'], 'docker_distro')} -e sh -s",
        "powershell": lambda: f"cd /mnt/c && {token(cfg['powershell_exe'], 'powershell_exe')} -NoProfile -NonInteractive -Command -",
        "psql": lambda: (f"docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' {probe.container} "
                         "psql -X -q -At -F '|' -d postgres -v ON_ERROR_STOP=1"),
        "clickhouse": lambda: f"docker exec -i {probe.container} clickhouse-client --readonly=1 --multiquery",
    }
    if probe.transport not in builders:
        raise ConfigError(f"probe {probe.id}: unknown transport {probe.transport!r}")
    cmd = builders[probe.transport]()
    assert_read_only(cmd, f"remote command for {probe.id}")
    return cmd


def ssh_argv(cfg: dict, remote: str) -> list[str]:
    host = token(cfg["host"], "host")
    opts = []
    for o in cfg.get("ssh_options", []):
        if not isinstance(o, str) or not SAFE_SSH_OPT.match(o):
            raise ConfigError(f"config ssh_options={o!r} is not a safe ssh option")
        opts.append(o)
    return [token(cfg.get("ssh_bin", "ssh"), "ssh_bin"), *opts, host, remote]
