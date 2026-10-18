# Investigation: Hermes Desktop stop and late daily-sync cron runs, 2026-10-15

- **Card:** t_290f7b13 (crew board) · **Author:** coder · **For:** Commander
- **Host:** AGENT-4070 (Windows 11 26200, HERMES_HOME `O:/workspaces/_config/agent4070/hermes`)
- **Mode:** read-only log analysis. No service, config, process or git state was changed.
- **Times:** local is ICT (UTC+07:00). Windows Event Log timestamps are UTC and marked `Z`.

---

## 1. Summary

1. **Hermes Desktop did not crash.** No Hermes, Electron or Python app crash was recorded: Application log providers *Application Error*, *Application Hang* and *WER* show no entry for those processes, and `%LOCALAPPDATA%\CrashDumps` holds no Hermes dump. Desktop and the gateway were **killed by an automatic Windows Update reboot at 00:29:23** (event 1074, `MoUsoCoreWorker.exe`, NT AUTHORITY\SYSTEM, "Service pack (Planned)", restart). A second servicing reboot followed at 00:36:13 (`TrustedInstaller.exe`, "Upgrade (Planned)").
2. **Nothing restarted Hermes after the reboot.** The gateway, which runs the cron ticker, was down for **9 h 54 min** (00:29:20 → 10:23:41). Its only logon autostart, the Startup-folder shim `Hermes_Gateway_914e929b.vbs`, is **disabled in `StartupApproved`**: flag `0x01` with a disable timestamp of 2026-10-05 10:51:30Z. No scheduled task or Windows service starts the gateway.
3. **At 08:58 the host lost power unexpectedly** (event 6008 plus Kernel-Power 41 with `BugcheckCode=0` and `PowerButtonTimestamp=0`). It came back up at 09:58. No Hermes process was running at that time, so this reset did not cause the outage, but it is an open hardware/driver risk (§5.3).
4. **The cron jobs ran late because the scheduler was down, not because the scheduler was faulty.** When a user session launched the gateway at 10:23:41, the built-in scheduler's catch-up started **all 12 missed jobs in one 1.2 s burst** (10:24:15.85–10:24:16.07). The daily *Hermes - Daily Upstream Sync Watchdog* (due 03:00) started at **10:24:16, 7 h 24 m late**, and finished OK at 10:36:26.
5. **Later on the same day:** two host sleeps were requested through `shutdown.exe` (11:10 and 11:28). The gateway then exited uncleanly at 13:11 (cause not determined). Since 13:29:45 the **live gateway runs from the merge worktree `O:/workspaces/.worktrees/wt-hermes-sync-daily`**, which has a staged, unfinished upstream merge (§4.4).

---

## 2. Evidence sources

| ID | Source | Query / location |
|---|---|---|
| E1 | Windows System log | `wevtutil qe System` (events 1074, 6005/6006/6008, 41, 42, 107, 187, 7001, 7023, 43/19 WindowsUpdateClient, 1 Power-Troubleshooter) |
| E2 | Windows Application log | `wevtutil qe Application` (providers Application Error, Windows Error Reporting) |
| E3 | Gateway lifecycle ledger | `logs/gateway-exit-diag.log` (JSONL tags `gateway.start`, `gateway.previous_unclean_exit`, `gateway.exit_clean`) |
| E4 | Gateway start stamps | `gateway-starts.log` (epoch seconds) |
| E5 | Gateway / agent logs | `logs/gateway.log`, `logs/agent.log`, `logs/errors.log` |
| E6 | Desktop (Electron main) log | `logs/desktop.log` |
| E7 | Dashboard/web-server log | `logs/gui.log`, `logs/dashboard-auth.log` |
| E8 | Cron execution history | `cron/executions.db` table `executions` (opened read-only, `mode=ro`) |
| E9 | Cron job definitions | `cron/jobs.json` |
| E10 | Live gateway state | `gateway_state.json` |
| E11 | Autostart config | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Hermes_Gateway_914e929b.vbs`; `HKCU\...\Explorer\StartupApproved\StartupFolder`; `schtasks /query`; Shell-Core/Operational events 9705–9708 |
| E12 | Session DB | `state.db` table `messages` (read-only scan for shutdown/kill/hibernate tool calls) |
| E13 | Crash dumps | `%LOCALAPPDATA%\CrashDumps` |
| E14 | WU policy | `HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings` |

---

## 3. Verified timeline

| Local time (ICT) | UTC | Event | Evidence |
|---|---|---|---|
| 10-14 12:29:38 | 05:29:38Z | Gateway pid 27416 starts (`-c gateway run --replace`) | E3, E4 |
| 10-15 00:15–00:25 | 17:15Z | *Hermes daily issue snapshot* runs normally (pid 27416) | E8 |
| 00:25:27 | 17:25Z | Last cron claim before the outage (*Spark Mail Triage Sentinel*) | E8 |
| 00:29:20 | 17:29:20Z | **Last gateway heartbeat of pid 27416** | E3 `previous_unclean_exit.last_heartbeat_at=2026-10-14T17:29:20.397521+00:00` |
| 00:29:21–28 | — | Buzz WebSocket drops, then HTTP 502 (network going down with the host) | E5 gateway.log:31870-31873 |
| **00:29:23** | **17:29:23Z** | **Event 1074: `C:\WINDOWS\uus\AMD64\MoUsoCoreWorker.exe` – Operating System: Service pack (Planned) 0x80020010 – restart – NT AUTHORITY\SYSTEM** | E1 |
| 00:29:51 | 17:29:51Z | WU installs *NVIDIA Display Driver Update (32.0.16.1088)* (43 → 19 at 17:30:43Z) | E1 |
| 00:34:57 / 00:35:44 | 17:34:57Z / 17:35:44Z | Event log stop (6006) / boot (6005) | E1 |
| **00:36:13** | **17:36:13Z** | **Event 1074: `TrustedInstaller.exe` – Upgrade (Planned) 0x80020003 – restart** (second reboot) | E1 |
| 00:36:54 / 00:37:15 | 17:36:54Z / 17:37:15Z | Boot, then user logon (Winlogon 7001) | E1 |
| 00:37 → 10:23 | — | **No gateway start and no Desktop boot.** `gateway-starts.log` jumps from `1791955778` (10-14 12:29) to `1792034621` (10-15 10:23). desktop.log has no lines between 10-14 20:11:43 and 10-15 10:23:58. | E4, E6 |
| 01:00 → 09:00 | — | **Zero cron claims**: `SELECT count(*) FROM executions WHERE claimed_at BETWEEN '2026-10-15T00:27' AND '2026-10-15T10:24'` → `0` | E8 |
| 08:56:54 | 01:56:54Z | Cloudflare WARP.exe crash (flutter_windows.dll, c0000005, then c000041d); dumps `Cloudflare WARP.exe.12080.dmp` at 08:56:58 | E2, E13 |
| **≈08:58:01** | ≈01:58Z | **Unexpected power-off.** 6008: "previous system shutdown at 8:58:01 AM on 10/15/2026 was unexpected". Kernel-Power 41 at next boot: BugcheckCode 0, PowerButtonTimestamp 0, LongPowerButtonPressDetected false. | E1 |
| 09:58:08 / 09:58:26 | 02:58Z | Boot, then user logon. WireGuard tunnel `agent4070` fails to start (7023/7011). | E1 |
| **10:23:41** | 03:23:41Z | **Gateway pid 32084 starts** (`-c gateway run --replace`, the Desktop/dashboard launcher form from `hermes_cli/_launchers.py:71`). The ledger records 27416 as `previous_unclean_exit`. | E3, E4, E5 gateway.log:31875 |
| 10:23:58 | — | Hermes Desktop boots ("Remote Hermes backend is ready" at 10:23:59) | E6 desktop.log:2623-2628 |
| **10:24:15.85–10:24:16.07** | — | **Catch-up burst: 12 missed jobs claimed within 1.2 s** (pid 32084) | E8 |
| 10:24:16 → 10:36:26 | — | *Hermes - Daily Upstream Sync Watchdog* runs, `completed`, `delivered` | E8, E9 |
| 11:10:31 → 11:12:30 | 04:10:31Z → 04:12:30Z | **Host sleep** requested by `\Windows\System32\shutdown.exe` (Kernel-Power 187 → 42 → 107; sleep time 114 148 ms) | E1 |
| 11:10:33 / 11:12:30 | — | Desktop `net::ERR_NETWORK_IO_SUSPENDED`; "main-process event loop lagged 116090ms" | E6 desktop.log:2637-2643 |
| 11:28:48 → 11:31:24 | 04:28:48Z → 04:31:24Z | **Second host sleep** through `shutdown.exe` (sleep time 150 429 ms); Desktop loop lag 150479 ms; web_server "event loop stalled 150.4s" | E1, E6:2655, E7 gui.log:40924 |
| 12:59:41 | — | Cron claim *Spark Mail Triage Sentinel* (pid 32084); never finishes | E8 |
| 13:10:58 | 06:10:58Z | Last heartbeat of pid 32084 | E3 |
| **13:11:06** | — | `gateway.run: Shutdown context: signal=UNKNOWN … parent_pid=4272`. No exit path completed; the next start flags it as unclean. | E5 agent.log:40064, E3 |
| 13:26:57 | 06:26:57Z | Gateway pid 65960 starts **from `O:\workspaces\.worktrees\wt-hermes-sync-daily\hermes_cli\main.py gateway run --quiet --idle-exit`** and exits cleanly at 13:27:13 | E3 |
| 13:29:45 | 06:29:45Z | **Gateway pid 53724 starts from the same worktree; it is the live gateway now** (`gateway_state.json` argv and `code_sha d3228474`) | E3, E10 |
| 13:29:58 | — | Scheduler marks the 12:59 execution `unknown` ("owner exited before a durable terminal state") | E8, agent.log:40108 |
| 13:35:01 | — | Hermes Desktop relaunched | E6 desktop.log:2670 |

### 3.1 Cron delay per job (catch-up at 10:24)

| Job (jobs.json id) | Schedule | Due (10-15) | Started | Delay | Result |
|---|---|---|---|---|---|
| Hermes S3 Daily Backup (`68da1abdecf6`) | `0 2 * * *` | 02:00 | 10:24:17 | 8 h 24 m | completed / delivered |
| **Hermes - Daily Upstream Sync Watchdog** (`a23531e9d8d6`) | `0 3 * * *` | 03:00 | 10:24:16 | **7 h 24 m** | completed 10:36:26 / delivered |
| DeskRPG - Daily Upstream Sync Watchdog (`13185bb8904a`) | `45 3 * * *` | 03:45 | 10:24:16 | 6 h 39 m | completed / delivered |
| Buzz - Daily Upstream Sync Watchdog (`0e0d30c9a142`) | `20 4 * * *` | 04:20 | 10:24:16 | 6 h 04 m | completed / delivered |
| Crew - Daily Upstream Sync Watchdog (`a44837ca85b3`) | `40 4 * * *` | 04:40 | 10:24:17 | 5 h 44 m | completed / delivered |
| Headroom - Daily Upstream Sync Watchdog (`3febadca9dcc`) | `0 5 * * *` | 05:00 | 10:24:17 | 5 h 24 m | completed / delivered |
| 9Router - Daily Upstream Sync Watchdog (`a31c795cd15e`) | `30 5 * * *` | 05:30 | 10:24:17 | 4 h 54 m | completed / delivered |
| Paperclip - Daily Upstream Sync Watchdog (`2afd43ecb5a9`) | `40 5 * * *` | 05:40 | 10:24:16 | 4 h 44 m | completed / delivered |
| Daily briefing (`16781b69f6d4`) | `0 8 * * *` | 08:00 | 10:24:15 | 2 h 24 m | completed / delivered |
| nhac-dang-ky-tam-tru | — | 09:00 | 10:24:15 | 1 h 24 m | completed / delivered |
| Hermes pending skill review and remediation | interval | 01:09 | 10:24:15 | ≈9 h 15 m | completed / delivered |
| Spark Mail Triage Sentinel | interval | 00:41 | 10:24:17 | ≈9 h 43 m | completed / suppressed |

"Started" is the `started_at` value in E8. Every catch-up run reached `status=completed`, and E8 has no `cron_incidents` rows for 10-14/10-15. The scheduler therefore worked as designed once it was running.

---

## 4. Root cause analysis

### 4.1 Trigger: automatic Windows Update reboot at 00:29

- Event 1074 shows SYSTEM-initiated restarts by `MoUsoCoreWorker.exe` (Update Orchestrator) and then `TrustedInstaller.exe`, both marked *Planned*.
- Active hours are `ActiveHoursStart=6`, `ActiveHoursEnd=0` (E14), so 06:00–24:00. 00:29 is outside that window, which is why Windows was allowed to reboot without asking.
- The gateway's last heartbeat (17:29:20Z) is 3 s before the 1074 event (17:29:23Z). The next gateway start classifies the exit as `UNCLEANLY (no exit path ran — SIGKILL / OOM / VM death …)` with `suspected_oom=False` (gateway.log:31875). That fits a process terminated by OS shutdown.
- The same update set included an NVIDIA display driver (32.0.16.1088), installed at 17:29:51Z.

### 4.2 Why the outage lasted ~10 hours: no gateway autostart

- **Startup-folder shim disabled.** `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder` → `Hermes_Gateway_914e929b.vbs = 01000000 C38C9C7AB754DD01`. The odd first byte means disabled, and the FILETIME decodes to **2026-10-05 10:51:30 UTC** (when it was turned off, for example from Task Manager → Startup). For comparison, the enabled item `Synology Drive Client.lnk` has flag `02`. Shell-Core events 9705–9708 for both 10-15 logons list only Run-key items such as `Everything.exe`. No Hermes launch appears.
- **No other supervisor.** `schtasks /query` returns only `\HermesChromeCDP` (Disabled), and no task for the gateway. Service Control Manager events since 2026-09-20 contain no service whose name matches hermes/gateway/dashboard.
- **Pattern.** Over the last 20 days every `gateway.start` in E3 was launched by a person or by Desktop: `main.py gateway run [--replace]` or `-c gateway run --replace`. None was started by an unattended boot. So any reboot leaves cron dead until somebody opens Desktop or the dashboard.
- **Result.** On the 10-15 boots (00:37 and 09:58) cron stayed down until the user's session launched the gateway at 10:23:41.

### 4.3 Why all late jobs fired at once

- The built-in scheduler (`cron.provider: ""` in config.yaml) catches up due jobs on its first tick. The external-provider misfire sweep `fire_overdue_jobs` is a no-op for `InProcessCronScheduler` (`cron/scheduler_provider.py:313-314`). Every job whose `next_run_at` was in the past was therefore claimed at the same time, 12 claims in 1.2 s (E8).
- This caused no failures this time, but it is a load spike: 12 concurrent agent runs, each loading skills and LLM context, at the same moment Desktop was booting.

### 4.4 Secondary findings, same day

1. **08:58 unexpected power loss (cause not determined).** Kernel-Power 41 records no bugcheck, no power-button press, and no dump. That points to a power cut, a hard hang plus reset, or a PSU problem. Correlated but unproven: the NVIDIA driver was updated 8.5 h earlier; Cloudflare WARP crashed at 08:56:54, 67 s before; and WER keeps re-submitting `LiveKernelEvent` 0x141 (VIDEO_ENGINE_TIMEOUT_DETECTED) and 0x193 (watchdog live dumps) with **identical parameters since at least 2026-09-25**. Those look like queued re-reports of an old GPU timeout, not new events. Hermes was not running then, so this did not cause the cron delay.
2. **Host sleeps at 11:10 and 11:28 through `shutdown.exe`.** Kernel-Power 187 names `\Windows\System32\shutdown.exe` as the requester. A scan of `state.db` for agent tool calls in 11:00–11:32 that mention shutdown/hibernate/powercfg/SetSuspendState found **0 hits** (E12), so no Hermes agent issued them. The same 187/107 pattern appears on 09-25, 09-27 and 10-10. Desktop's `keep-awake.json` is `{"mode":"off"}`. Effects: Desktop network I/O suspended, event-loop stalls of 114 s and 150 s, and web_server "GIL pressure suspected" warnings that are really sleep gaps.
3. **13:11:06 unclean gateway exit (cause not determined).** The gateway logged a shutdown context (`signal=UNKNOWN`, parent pid 4272, now gone) but never reached an exit path. No kill/stop/restart command was found in `state.db` for the active sessions at 13:08–13:12 (`20261015_130531_e16503` "Continue resolving merge conflicts" and `20261015_104049_8e6f14`). The in-flight cron execution (12:59 Spark triage) was later marked `unknown`. Cron was down for ~18 min (13:11 → 13:29).
4. **Live gateway is running from a merge worktree.** Since 13:26:57 every gateway start uses `O:\workspaces\.worktrees\wt-hermes-sync-daily\hermes_cli\main.py`, including the live pid 53724 (`gateway_state.json` `code_sha d3228474fc…`, `--idle-exit`). That worktree holds a staged upstream merge, and conflict markers are still present in `tests/test_audit_old_updater_imports.py`. Production cron and messaging now depend on unreviewed merge state. With `--idle-exit` the gateway may also stop on its own when idle, which would stop cron again.

---

## 5. Remediation and prevention

These are recommendations only. The Commander has reserved gateway/dashboard restarts, so none of these steps were executed.

### 5.1 Keep cron alive across reboots (fixes RC 4.2, highest priority)
1. Give the gateway an unattended supervisor that starts at **boot** (not only logon):
   - Preferred: the Hermes-native Windows gateway service install (`hermes gateway install`, `hermes_cli/gateway_windows.py`), or the project's existing NSSM pattern (`services/setup-hermes-nssm-service.bat`). Configure *Automatic (Delayed Start)* and restart-on-failure.
   - Minimum: re-enable the Startup item (Task Manager → Startup apps → `Hermes_Gateway_914e929b.vbs` → Enable). This only covers logon, not boot.
2. Verify after the next reboot: `gateway-starts.log` gets a new stamp within 2 min of boot, and `cron/ticker_heartbeat` advances without anyone opening Desktop.

### 5.2 Stop unattended reboots during the cron window (fixes RC 4.1)
1. Cover the nightly job window (00:00–09:00) by either:
   - Group Policy *Configure Automatic Updates* → "Notify to schedule restart", or `NoAutoRebootWithLoggedOnUsers=1` under `HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU`; or
   - a fixed restart time **after** the last daily job (after 09:30) through *Scheduled install time*.
2. Watch event 1074 from `MoUsoCoreWorker.exe`/`TrustedInstaller.exe` in the weekly host audit.

### 5.3 Host stability (RC 4.4.1)
1. Check power: UPS or wall power around 08:58, PSU health, BIOS power-loss settings.
2. GPU: open `C:\WINDOWS\LiveKernelReports\WATCHDOG\*` in WinDbg. If 0x141 TDRs persist on 32.0.16.1088, pin or roll back the NVIDIA driver and exclude drivers from WU (`ExcludeWUDriversInQualityUpdate=1`).
3. Cloudflare WARP crashes repeatedly (dumps on 10-13, 10-14, 10-15): update or reinstall, since it sits on the network path at crash time.

### 5.4 Sleep transitions (RC 4.4.2)
1. Find the caller of `shutdown.exe` (hotkey, script or scheduled task). Turn on process-creation auditing (Security 4688 with command line) to capture it next time.
2. If the host must stay awake for cron: `powercfg /change standby-timeout-ac 0` and/or set Desktop keep-awake to on.

### 5.5 Gateway provenance (RC 4.4.4)
1. Once the `wt-hermes-sync-daily` merge is finished and reviewed, the Commander should relaunch the gateway from the production checkout `O:/workspaces/oss/hermes-agent` (branch `main`), **without** `--idle-exit`.
2. Add a check to the daily health audit: alert when `gateway_state.json.argv[0]` is not under the production checkout.

### 5.6 Detection
1. External staleness alarm, run off-host (e.g. intel-nuc) or from a SYSTEM scheduled task: alert when `cron/ticker_heartbeat` or `gateway_state.json.updated_at` is more than 10 min old. On 10-15 that would have alerted at ~00:40 instead of the outage being found at 10:24.
2. Cron catch-up: consider staggering catch-up dispatch (e.g. N at a time) so a long outage does not start 12 agent runs in the same second.

---

## 6. Open questions

| # | Question | Evidence gap |
|---|---|---|
| Q1 | What caused the 08:58 power loss? | No bugcheck/dump; needs physical/UPS check and WinDbg on LiveKernelReports. |
| Q2 | Who invoked `shutdown.exe` for the 11:10 and 11:28 sleeps? | Process-creation auditing not enabled; not found in Hermes `state.db`. |
| Q3 | What ended gateway pid 32084 at 13:11:06 (parent pid 4272)? | Parent is gone; no kill command in `state.db`; no Windows crash event. |
| Q4 | Who disabled the Startup-folder shim on 2026-10-05 10:51:30Z? | `StartupApproved` stores only the time. |

---

## 7. Reproduction commands (read-only)

```sh
# Reboots / power events (UTC)
wevtutil qe System /q:"*[System[(EventID=1074 or EventID=6008 or EventID=41 or EventID=187 or EventID=107) and TimeCreated[@SystemTime>='2026-10-14T17:00:00Z' and @SystemTime<='2026-10-15T06:40:00Z']]]" /f:text
# Gateway lifecycle
grep -E '"ts": "2026-10-1[45]' "$HERMES_HOME/logs/gateway-exit-diag.log"
# Cron claims during the outage (expect 0)
python -c "import sqlite3;c=sqlite3.connect('file:'+r'$HERMES_HOME/cron/executions.db'+'?mode=ro',uri=True);print(c.execute(\"select count(*) from executions where claimed_at between '2026-10-15T00:27' and '2026-10-15T10:24'\").fetchone())"
# Startup shim state
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
```
