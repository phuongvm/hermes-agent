# Delta Specification: Stabilize Dashboard Auth Runtime Ownership

## ADDED Requirements

### Requirement: Host Auth Provider Process-Home Ownership
The dashboard host authentication provider and session database SHALL bind immutably to the host process home directory resolved at server startup. The provider and its session store SHALL remain stable across routed per-request profile operations, and plugin rediscovery SHALL NOT replace the active host-owned provider with a request-scoped or profile-scoped instance.

#### Scenario: Root-launched dashboard remains bound to root auth store after routed profile rediscovery
- **WHEN** a dashboard server is launched under the root process home
- **AND** a per-request profile override or profile-scoped plugin rediscovery is executed for a named profile
- **THEN** the host dashboard authentication provider MUST remain bound to the root process session store
- **AND** session refresh requests presenting valid root tokens MUST succeed
- **AND** no new session database file SHALL be created in the routed profile's directory by that rediscovery

#### Scenario: Dashboard launched explicitly under one named profile remains bound to that process profile store
- **WHEN** a dashboard server is launched explicitly with a named profile (`-p <name>`)
- **THEN** the host dashboard authentication provider MUST bind to that process's profile home session database
- **AND** authentication operations for that dashboard process SHALL operate exclusively against that profile's session store
- **AND** the dashboard process SHALL NOT be forced into the root process session store

#### Scenario: Concurrent or forced rediscovery cannot replace active host-auth provider with request-scoped instance
- **WHEN** a concurrent or forced plugin rediscovery occurs while a host-owned dashboard auth provider is active
- **AND** the rediscovery occurs within a request-scoped or profile-scoped context
- **THEN** the process-global auth provider registry MUST reject replacing the active host-owned provider with the request-scoped instance
- **AND** the active host-owned provider MUST continue servicing authentication requests
