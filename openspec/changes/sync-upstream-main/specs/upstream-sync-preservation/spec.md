## ADDED Requirements

### Requirement: Isolated and revision-pinned reconciliation (SYNC-1)
The upstream reconciliation SHALL use recorded local and upstream commit identities and SHALL remain isolated from the live main checkout, production state, and running services until explicit Commander approval.

#### Scenario: Candidate preparation
- **WHEN** Coder begins reconciliation
- **THEN** the workspace and branch MUST be the authorized integration worktree and `sync/upstream-main`
- **AND** local baseline and upstream target MUST match the recorded revisions or receive a documented scope update before merge
- **AND** validation MUST use disposable state and dependency directories that cannot modify the live checkout through junctions or links

#### Scenario: Failed candidate verification
- **WHEN** any required invariant or independent verification fails
- **THEN** the candidate MUST NOT be deployed, integrated into main, or used to restart live services
- **AND** failure evidence and remaining work MUST remain visible in the handoff

### Requirement: Complete conflict and semantic-risk accounting (SYNC-2)
The integration SHALL retain a resolution ledger covering all supplied paths, all conflicts observed at the pinned revisions, dependency manifests and locks, and invariant-critical auto-merged dependencies. An absent or moved supplied path SHALL be explicitly accounted for rather than silently omitted.

#### Scenario: Supplied and observed inventories differ
- **WHEN** a merge preview reports paths not present in the supplied conflict list
- **THEN** both inventories MUST be preserved with their provenance
- **AND** the new paths MUST receive a documented strategy and test scope
- **AND** Coder MUST obtain Leader scope confirmation before resolving newly discovered areas outside the supplied file list

#### Scenario: Upstream replaces a protected implementation
- **WHEN** an upstream implementation is proposed to supersede a protected local fix
- **THEN** the ledger MUST identify local and upstream line ranges, the required behavior, the replacement rationale, and reproducible before/after test evidence
- **AND** textual similarity, a clean auto-merge, or passing unrelated tests MUST NOT constitute equivalence proof
- **AND** unresolved equivalence MUST block acceptance rather than justify dropping the fix

### Requirement: Process-home authentication ownership survives reconciliation (SYNC-3)
The reconciled dashboard SHALL preserve the host-auth ownership, persistent-token resolution, and startup-grace requirements of `dashboard-auth` without routing host authentication through a request-selected profile's store.

#### Scenario: Root process receives profile-scoped requests
- **WHEN** a root-launched server processes named-profile requests or concurrent plugin rediscovery
- **THEN** the original process-owned provider and authentication store MUST remain active
- **AND** valid root refresh sessions MUST continue to work without creating an authentication database under the request-selected profile

#### Scenario: Named-profile process stays isolated
- **WHEN** the server is launched explicitly under a named profile
- **THEN** authentication MUST bind to that process profile rather than being forced to the root profile
- **AND** another process profile's sessions MUST NOT become valid solely through profile routing

#### Scenario: Restart and initialization preserve credential semantics
- **WHEN** an isolated server restarts using an existing persistent session token and then receives requests during and after initialization
- **THEN** the token MUST remain usable after initialization under the existing precedence and permission contract
- **AND** initialization responses MUST use 503 with `Retry-After: 3`, not a false session-expired 401
- **AND** genuinely invalid credentials MUST remain rejected after initialization

### Requirement: Native bearer recovery is bounded and generation-safe (SYNC-4)
The reconciled Desktop SHALL preserve forced refresh despite unexpired local token metadata, no more than one replay per original request, shared refresh for the same rejected bearer at the same gateway, and protection of newer login credentials. It SHALL NOT stack independent local and upstream refresh/replay loops.

#### Scenario: Valid refresh token recovers an early rejection
- **WHEN** an authoritative native-bearer 401 is received before local expiration
- **THEN** the client MUST force refresh once and replay once with a changed bearer when available
- **AND** a replayed 401 MUST propagate without another refresh or replay

#### Scenario: Concurrent and staggered failures share rotation
- **WHEN** several requests sent with the same bearer receive 401s concurrently or after one rotation has completed
- **THEN** they MUST share the in-flight rotation or reuse the already stored replacement bearer
- **AND** the rejected old bearer MUST NOT trigger another rotation of that replacement

#### Scenario: Old failure arrives after successful new login
- **WHEN** a response or refresh failure from an old session arrives after new credentials are stored
- **THEN** it MUST NOT overwrite, rotate due solely to that old rejection, or clear the newer login
- **AND** the new authenticated state MUST remain usable

#### Scenario: Non-authentication failures are not credential loss
- **WHEN** a request times out, fails at transport, returns 5xx, or receives a policy 403 rather than an authoritative 401
- **THEN** the client MUST NOT invoke the native 401 force-refresh mechanism or erase credentials solely for that failure
- **AND** actual HTTP status provenance MUST be preserved across transport and IPC boundaries

### Requirement: Terminal-auth suspension remains distinct from reconnect (SYNC-5)
The reconciled Desktop SHALL preserve `desktop-reconnect-resilience` requirements for disconnected background sync suppression, coalesced recovery, cached data retention, endpoint-scoped transient rejection classification, and immediate suspension upon authoritative terminal native refresh rejection.

#### Scenario: Gateway temporarily disconnects
- **WHEN** the connection is not open and focus, profile, session, config, or model background refresh would run
- **THEN** no such background network request MUST be issued
- **AND** cached data MUST remain available without uncaught promise rejections
- **AND** reopening an authenticated connection MUST coalesce deferred work to one refresh per data source

#### Scenario: Refresh endpoint authoritatively rejects the current session
- **WHEN** the current native refresh returns an authoritative terminal 401 for a normalized base URL
- **THEN** that endpoint MUST atomically enter signed-out and reauth-required state and clear only its obsolete credentials
- **AND** profile refreshes, project-tree self-healing, and background polling MUST stop without scheduling retry timers
- **AND** another endpoint or a newer session MUST remain unaffected

#### Scenario: Confirmed login restores suspended work
- **WHEN** confirmed sign-in succeeds for a terminally signed-out base URL
- **THEN** signed-out and reauth-required state MUST clear for that base URL
- **AND** suspended work MUST resume once per data source rather than as a request cascade

#### Scenario: Ordinary probe rejection is not terminal refresh rejection
- **WHEN** a single ordinary health-probe 401 occurs during a reconnect window
- **THEN** it MUST NOT be treated as an authoritative terminal refresh rejection
- **AND** the existing consecutive-rejection debounce policy MUST remain separate from immediate terminal suspension

### Requirement: Buzz liveness and non-editing delivery survive reconciliation (SYNC-6)
The reconciled gateway SHALL preserve the Buzz idle-probe contract, WAN-tolerant connection tuning, and delivery behavior for a platform that does not support message editing. It SHALL NOT change editing support for unrelated adapters to achieve this.

#### Scenario: Idle but responsive relay
- **WHEN** Buzz receives no data beyond the idle-read bound while the relay responds to ping
- **THEN** Buzz MUST stay connected without redundant authentication or reconnect churn
- **AND** configured keepalive/open timeouts MUST reach the actual WebSocket connection

#### Scenario: Dead relay
- **WHEN** the idle probe fails or times out
- **THEN** Buzz MUST enter bounded reconnect backoff rather than remain falsely connected

#### Scenario: Non-editable message delivery
- **WHEN** Buzz delivers a streaming turn and final response
- **THEN** it MUST retain `SUPPORTS_MESSAGE_EDITING = False` behavior without frozen preview cursors, attempted in-place edit dependence, or duplicate final content
- **AND** editable adapters MUST retain their own supported streaming behavior

### Requirement: Multi-provider authentication keeps invalid-token and outage semantics (SYNC-7)
The reconciled dashboard SHALL preserve the active JWKS-classification delta and canonical native-provider chooser contract without weakening PKCE, redirect validation, signature, issuer, audience, subject, or session lifetime checks.

#### Scenario: Healthy provider lacks a foreign key ID
- **WHEN** a token's key ID is absent from one provider's reachable, valid JWKS
- **THEN** that provider MUST classify the token as unverifiable for itself and allow the next provider to evaluate it
- **AND** it MUST NOT report the provider as unreachable solely because of the unmatched key ID

#### Scenario: JWKS transport or structure fails
- **WHEN** JWKS retrieval fails or the response is structurally invalid
- **THEN** the failure MUST remain a provider error under the existing availability response policy
- **AND** it MUST NOT silently become an accepted identity or a credential-clearing terminal rejection

#### Scenario: Native authorization has multiple providers
- **WHEN** valid native PKCE authorization omits provider selection and multiple brokerable non-password providers exist
- **THEN** the response MUST present a non-cacheable chooser retaining challenge, method, redirect URI, and state in provider links
- **AND** selecting a provider MUST continue to that provider; a single eligible provider MUST retain automatic selection

### Requirement: Dependencies and independent evidence match the candidate (SYNC-8)
Acceptance SHALL require reproducible reconciled manifests/locks, an exact candidate identity, affected behavioral tests, Desktop typecheck/build, independent Reviewer audit, and QA verification. A completed specification SHALL NOT be presented as a tested merge.

#### Scenario: Dependency reconciliation
- **WHEN** package or Python dependency manifests differ
- **THEN** manifests MUST be reconciled before regenerating locks using the selected toolchain
- **AND** clean isolated installation and lock consistency checks MUST pass without borrowing stale mutable production dependencies

#### Scenario: Independent acceptance
- **WHEN** Coder hands off the candidate
- **THEN** Reviewer MUST audit every resolution and protected invariant, including clean auto-merges
- **AND** QA MUST verify the same commit or exact dirty-tree manifest and record commands, outputs, exit codes, failures, skips, and environmental blockers
- **AND** a code or dependency change after verification MUST invalidate affected earlier evidence
- **AND** main integration, deployment, and archive MUST remain pending Commander approval
