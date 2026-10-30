# Independent Proposal Review — Desktop Installer Synology Release Push

## Verdict: **REJECTED**

The OpenSpec schema is valid, but the proposal is not implementation-ready. The artifact set does not yet provide a complete, coherent, publicly testable contract for its principal release-safety controls. No source, test, or OpenSpec planning artifact was modified by this reviewer.

## Scope and evidence

Reviewed:

- `proposal.md`
- `specs/desktop-release-publishing/spec.md`
- `design.md`
- `tasks.md`
- Exploration: `openspec/workspace/explorations/2026-10-29-desktop-installer-synology-release-push-exploration.md`
- Existing process seam: `O:/workspaces/_config/agent4070/hermes/services/build-desktop-installer.ps1`
- Sibling invariant: `openspec/specs/installer-version-integrity/spec.md`

Preflight / Zero-Checkout: `git branch --show-current` reported `main` for `O:/workspaces`, `oss/crew`, `oss/hermes-agent`, and `oss/hermes-nerve`; no checkout or switch was performed. The primary Hermes checkout contains concurrent untracked work, so this audit treats only the named change artifacts as its delta.

Strict schema execution:

```text
C:/nvm4w/nodejs/openspec.exe validate desktop-installer-synology-release-push --strict
Change 'desktop-installer-synology-release-push' is valid
OPEN_SPEC_RC=0
```

Schema validity is necessary but does not prove cross-artifact semantic fidelity.

## 3D specification and seam audit

| Lens | Result | Evidence |
|---|---|---|
| Completeness | **FAIL** | The required default endpoint ladder cannot be demonstrated through the stated public loopback seam; update-feed freshness and concrete SynoToken negotiation are not normatively guarded. |
| Correctness | **FAIL** | `-PushOnly -IncludeUpdateFeed` can satisfy the current byte-size-only contract while publishing a stale `latest.yml`, contradicting the stated active-version safety objective. |
| Coherence | **FAIL** | The four artifacts prescribe mutually inconsistent public flag names and incompatible ladder test behavior. |
| Scope | PASS | The change is explicitly out-of-tree (`_config/.../services`) and promises zero `apps/desktop` edits, consistent with sibling `installer-version-integrity` scope protection. |

## Blocking findings

### F-01 — CRITICAL: Seam A cannot prove the required default endpoint-ladder failover

**Contract evidence**

- The delta spec requires the fixed default ladder and selection of the first successful `SYNO.API.Info` probe (`spec.md:21-34`).
- The design says Seam A uses a loopback `127.0.0.1:0` Fake DSM (`design.md:50-53`) and the exploration says this seam exercises endpoint-ladder failover (`exploration.md:104-108`).
- But the only specified public endpoint override disables the default ladder rather than supplying candidates (`spec.md:32-34`).

**Defect**: A loopback endpoint supplied through `SYNOLOGY_DSM_URL` can test only override behavior; it cannot cause the published default candidates `10.8.0.60:5001 → 10.8.0.60:5000 → dsm16.ptdev.vip` to fail then select the local Fake DSM. The proposed public seam therefore cannot prove the normative ladder/failover behavior without a private monkeypatch or an unspecified injection surface.

**Required remediation**: Specify one narrow, public test configuration seam for the ordered candidate list (or a transport adapter) that production defaults to the three required endpoints. Require Seam A tests for first-rung selection, fallback order, and override-only fail-closed behavior through that seam.

### F-02 — HIGH: Update-feed publication is not bound to the active installer version

**Contract evidence**

- The stated reason is to prevent stale cached release material (`proposal.md:4-5`; `exploration.md:23-32`).
- Default scoping protects the versioned installer, while `-IncludeUpdateFeed` adds the unversioned `latest.yml` (`spec.md:9-20`; `tasks.md:9`).
- Remote verification checks only existence and byte size (`spec.md:58-69`).

**Defect**: `latest.yml` has no version in its filename. In `-PushOnly -IncludeUpdateFeed`, an old but non-empty `latest.yml` can be uploaded and pass remote byte-size verification, even if it describes a different version than `Hermes-$InstallerVersion-win-x64.exe`. This recreates the stale-release hazard for the update feed.

**Required remediation**: Require the build script/publisher to parse `latest.yml`, assert its `version` and referenced installer/blockmap match `$InstallerVersion`, and fail before any upload when they do not. Add a Seam B negative test with a current executable plus stale `latest.yml`.

### F-03 — HIGH: SynoToken session contract is underspecified

**Contract evidence**

- The functional requirement calls for FileStation authentication “with `SynoToken` support” (`spec.md:47-56`).
- The wire design requires both `_sid` and `X-SYNO-TOKEN` for uploads (`design.md:41-48`), and the Fake DSM is expected to validate that token (`design.md:50-53`).

**Defect**: The normative requirement does not specify how login requests SynoToken issuance, how absence of a returned token fails closed, or that every FileStation request must carry the returned token. A test can therefore satisfy the literal spec with a permissive fake while production fails CSRF enforcement.

**Required remediation**: Make login token negotiation, token-presence failure behavior, and `_sid`/cookie plus `X-SYNO-TOKEN` request requirements explicit in the delta spec. Require the Fake DSM to reject omitted/mismatched tokens and add logout-after-tokenized-upload-failure coverage.

### F-04 — MEDIUM: Public CLI flag names conflict across artifacts

- Proposal and task contracts require `-SkipPush`, `-PushOnly`, `-IncludeUpdateFeed`, and `-SynologyDestPath` (`proposal.md:14,28`; `tasks.md:9`).
- The design’s advertised small interface instead names `-RemoteDir` and `-SkipUpload` (`design.md:69-71`).

A caller, PowerShell test, and implementation cannot simultaneously conform to both names. Use the proposal/task names consistently or explicitly declare one set as deprecated aliases; tests must exercise the selected public names.

### F-05 — WARNING: Non-secret endpoint configuration is proposed for `.env`

`proposal.md:31-32` places optional `SYNOLOGY_DSM_URL` alongside credentials in `.env`. Root `AGENTS.md:41-42` reserves `.env` for secrets and directs behavior/configuration elsewhere. Keep credentials in the active Hermes `.env`; make endpoint selection a PowerShell argument/process environment configuration with documented precedence. Optional static OTP is also a speculative surface: design itself recommends a non-2FA service account (`design.md:58-61`), so retain it only if a real unattended OTP provider is specified and tested.

## Isolated two-axis review

### Standards

Independent Standards review found no blocker to the intended out-of-tree, deep-module placement or the two intended public seams. It confirmed the documented `.env` configuration tension above and labelled `SYNOLOGY_DSM_OTP` / mixed build-plus-publish responsibility as non-blocking Fowler-smell judgement calls.

### Spec fidelity

Independent Spec review reached **FAIL**: it independently identified missing concrete SynoToken negotiation, the lack of a testable default-ladder mechanism, the unbound update-feed safety risk, and the CLI-name contradiction. Its assessment agrees with F-01 through F-04.

## Required re-review gates

1. Revise all four artifacts so their endpoint test seam, token lifecycle, active-version update-feed checks, and exact public flag names agree.
2. Re-run `C:/nvm4w/nodejs/openspec.exe validate desktop-installer-synology-release-push --strict`.
3. Submit a new independent proposal review before any implementation card is released.

## Review boundary

No secrets were read or printed. No implementation, test, task checkbox, commit, checkout/switch, service restart, or NAS operation was performed by the reviewer.
