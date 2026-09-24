# Independent Code Re-review — `sync-upstream-main` v2

## Verdict

**APPROVED FOR DOWNSTREAM QA** on exact candidate `895a2838539492539647df52735ac6b70f64cabf` (parent `eee752a1d6da8c76b4b620c219034443f24e98d9`).

The remediation satisfies this card's isolation scope. The Buzz and dashboard behavioral variables are neutralized by the shared autouse fixture; tests that intentionally exercise those variables still set them explicitly after isolation; mocked dashboard startup tests no longer depend on whether host port `9119` is occupied; and the status endpoint tests no longer discover host gateway platforms. Independent runs passed with deliberately contaminated shell variables, and the dashboard-auth matrix also passed while a real listener occupied `127.0.0.1:9119`.

No implementation source was modified by Reviewer. The only Reviewer outputs are this report and the append-only coordination update.

## Candidate identity and scope

```text
git rev-parse HEAD
=> 895a2838539492539647df52735ac6b70f64cabf

git show --no-patch --format='%H %P %s' HEAD
=> 895a2838539492539647df52735ac6b70f64cabf
   eee752a1d6da8c76b4b620c219034443f24e98d9
   fix(test): isolate BUZZ and DASHBOARD env vars and port conflicts in test fixtures

git diff --name-status eee752a1d6..HEAD
=> M openspec/changes/sync-upstream-main/reviews/resolution-ledger.md
   M tests/conftest.py
   M tests/hermes_cli/test_dashboard_auth_gate.py
   M tests/hermes_cli/test_dashboard_auth_status_endpoint.py

git diff --check eee752a1d6..HEAD
=> exit 0, no findings
```

The parent handoff metadata names `67a5bf54a50d244907a9ee0ea675127057774d75`, but that object does not exist in this repository (`git cat-file -t ...` exits 128). The durable candidate branch `sync/upstream-main`, this review branch, the remediation branch, and the downstream QA v2 branch all resolve to `895a283853...`. This is a handoff metadata typo, not an unreviewed tree; all approval and downstream verification MUST use `895a283853...`.

## Static review

### 1. Host variable neutralization — PASS

`tests/conftest.py:328-337` includes dashboard OAuth/public URL/port variables in `_HERMES_BEHAVIORAL_VARS`, and `tests/conftest.py:433-456` includes the platform mention flags plus:

- `BUZZ_REPLY_TO_MODE`
- `BUZZ_REPLY_IN_THREAD`
- `BUZZ_REQUIRE_MENTION`

The autouse fixture deletes every listed variable before each test at `tests/conftest.py:460-475`. This preserves production behavior: only the test process is sanitized, while tests can and do set explicit values afterward. Negative controls remain present, including explicit Buzz opt-out behavior in `tests/gateway/test_buzz_thread_topology.py:251-275` and public URL auth-gate behavior in `tests/hermes_cli/test_dashboard_auth_gate.py:336-486`.

### 2. Dashboard bind isolation — PASS

`tests/hermes_cli/test_dashboard_auth_gate.py:90-147` stubs both public aliases of `_port_bind_conflict` only inside `_stub_uvicorn_run`. The production helper is not changed. This is appropriate because these tests replace Uvicorn with a non-binding fake and are testing auth/startup configuration, not socket ownership.

Independent production-helper control:

```text
held_port 61705 conflict True
free_port 61706 conflict False
```

The separate real helper tests remain in `tests/hermes_cli/test_serve_port_in_use.py:41-68`; on this Windows host that file is platform-skipped (`9 skipped`) by its existing markers, so the direct held/free socket probe above supplies the live control.

`test_start_server_loopback_sets_auth_required_false` also replaces config loading with `{}` at `tests/hermes_cli/test_dashboard_auth_gate.py:161-172`, preventing an operator's `dashboard.public_url` config from changing this local-only unit test. Explicit public URL gate tests remain and pass.

### 3. Status endpoint isolation — PASS

`tests/hermes_cli/test_dashboard_auth_status_endpoint.py:24-27` replaces `_load_configured_gateway_platforms` with an empty set for that module. This prevents host gateway configuration from changing status output while preserving assertions for gated and loopback response shapes.

### 4. Startup readiness reset — PASS WITH BOUNDED NOTE

`tests/conftest.py` resets imported `hermes_cli.web_server` startup readiness to true per test. This removes cross-test leakage from tests that deliberately force initialization state. It does not eliminate startup-grace coverage: `tests/hermes_cli/test_dashboard_session_resilience.py:122-228` explicitly sets readiness false/true and checks the 503/Retry-After contract.

## Independent execution evidence

All commands below ran from the clean review worktree on candidate `895a283853...` using the repository's shared Python 3.11.14 development environment. No `env -u` or manual variable deletion was used.

### Deliberately contaminated direct pytest runs

```text
BUZZ_REPLY_TO_MODE=off \
BUZZ_REPLY_IN_THREAD=false \
BUZZ_REQUIRE_MENTION=true \
HERMES_DASHBOARD_PUBLIC_URL=https://contamination.invalid \
HERMES_DASHBOARD_PORT=9119 \
O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe \
  -m pytest tests/gateway/test_buzz_*.py -q
=> 266 passed in 40.87s; exit 0
```

```text
[same contaminated environment] python -m pytest \
  tests/hermes_cli/test_dashboard_auth_*.py -q
=> 200 passed, 8 deprecation warnings in 16.35s; exit 0
```

The eight warnings are dependency deprecations (`audioop` and Starlette per-request cookies), not test failures or candidate regressions.

```text
[same contaminated environment] python -m pytest \
  tests/plugins/dashboard_auth/ -q
=> 142 passed in 20.90s; exit 0
```

### Real occupied-port control

Reviewer started a disposable `python -m http.server 9119 --bind 127.0.0.1`, verified it remained running, and reran the complete dashboard-auth matrix under the contaminated environment:

```text
python -m pytest tests/hermes_cli/test_dashboard_auth_*.py -q
=> 200 passed, 8 warnings in 15.86s; exit 0
```

The disposable listener was then terminated. This directly proves the dashboard-auth suite no longer fails merely because `9119` is occupied.

### Negative controls proving the fixture does not mask behavior

```text
BUZZ_REPLY_TO_MODE=reply ... python -m pytest \
  tests/gateway/test_buzz_thread_topology.py \
  -k 'standalone_send_honors_opt_out or apply_yaml_config_bridges_keys' -q
=> 2 passed, 15 deselected in 0.50s; exit 0
```

```text
HERMES_DASHBOARD_PUBLIC_URL=https://contamination.invalid ... python -m pytest \
  tests/hermes_cli/test_dashboard_auth_gate.py -k 'public_url' -q
=> 5 passed, 28 deselected in 1.53s; exit 0
```

### Canonical wrapper corroboration

The canonical wrapper required an explicit interpreter because this worktree has no local `.venv`. With `HERMES_PYTHON` pointed at the repository development environment:

```text
HERMES_PYTHON=O:/workspaces/oss/hermes-agent/.venv/Scripts/python.exe \
  bash scripts/run_tests.sh tests/gateway/test_buzz_*.py
=> 266 passed, 0 failed in 26.8s; exit 0

HERMES_PYTHON=... bash scripts/run_tests.sh \
  tests/hermes_cli/test_dashboard_auth_*.py
=> 200 passed, 0 failed in 14.1s; exit 0
```

The wrapper intentionally uses `env -i`, so these runs corroborate clean-CI behavior; the direct contaminated runs above are the controlling evidence for host-variable isolation.

### OpenSpec validation

```text
openspec validate sync-upstream-main --strict
=> Change 'sync-upstream-main' is valid; exit 0
```

## Acceptance map

| Card criterion | Status | Evidence |
|---|---|---|
| Neutralize `BUZZ_*` and `HERMES_DASHBOARD_*` in shared fixture | PASS | Static review of `tests/conftest.py:328-337,433-475`; contaminated direct runs |
| Dashboard auth does not conflict with live port 9119 or host public URL | PASS | Full matrix 200/200 with disposable listener on 9119 and external public URL set |
| Buzz pass rate | PASS | 266/266 direct contaminated run and 266/266 canonical wrapper run |
| Dashboard-auth pass rate | PASS | 200/200 direct contaminated run, 200/200 occupied-port run, 200/200 canonical wrapper run |
| Preserve negative behavior controls | PASS | Buzz opt-out 2/2; public URL gate 5/5; live `_port_bind_conflict` held/free probe |
| Structured reviewer-v2 report | PASS | This file |

## Findings

No blocking code-quality findings remain within this card's scope.

- **WARNING — traceability only:** Parent task metadata cites nonexistent candidate `67a5bf54a5`. The reviewed and branch-pinned candidate is `895a283853`. Downstream QA must record and verify the latter exact SHA.
- **INFO — environment setup:** `scripts/run_tests.sh` cannot discover a worktree-local venv. Reviewer used the repository development interpreter through supported `HERMES_PYTHON`; direct pytest runs used that same interpreter.
- **INFO — bounded scope:** This approval gates the isolation remediation and required Buzz/dashboard suites. The downstream QA card remains responsible for its broader Desktop/typecheck and final zero-trust report scope.

## Gate decision

**PROMOTE `895a2838539492539647df52735ac6b70f64cabf` TO DOWNSTREAM QA.**

QA should fail closed if its checked-out HEAD differs from this SHA or if the branch gains any additional code/dependency change after this review.
