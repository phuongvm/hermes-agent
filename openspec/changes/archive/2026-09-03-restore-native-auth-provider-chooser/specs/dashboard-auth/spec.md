## ADDED Requirements

### Requirement: Native Authorization Multi-Provider Chooser
The `/auth/native/authorize` route MUST render an interactive chooser page when the `provider` query parameter is omitted and more than one brokerable, non-password authentication provider is registered.

#### Scenario: Multiple OAuth providers render chooser page
- **WHEN** a client makes a `GET` request to `/auth/native/authorize` with valid PKCE parameters (`code_challenge`, `code_challenge_method=S256`, loopback `redirect_uri`, `state`) and no `provider` query parameter
- **AND** more than one brokerable session provider (`supports_password` is false or not set) is registered
- **THEN** the server MUST respond with HTTP status 200
- **AND** the response `Content-Type` MUST be `text/html; charset=utf-8`
- **AND** the response `Cache-Control` header MUST contain `no-store, no-cache, must-revalidate`
- **AND** the response body MUST contain links for each registered brokerable provider
- **AND** each link MUST point to `/auth/native/authorize` preserving the client's `code_challenge`, `code_challenge_method`, `redirect_uri`, and `state`, with `provider` set to the specific provider's name

#### Scenario: Single OAuth provider auto-selects without chooser
- **WHEN** a client makes a `GET` request to `/auth/native/authorize` with valid PKCE parameters and no `provider` query parameter
- **AND** exactly one brokerable session provider is registered
- **THEN** the server MUST redirect (HTTP 302) directly to that provider's authorization endpoint, bypassing the chooser page

#### Scenario: Selection of provider from chooser proceeds to IdP
- **WHEN** a user follows a link from the chooser page containing `?provider=<name>&code_challenge=...`
- **THEN** the server MUST register the pending broker authorization and redirect (HTTP 302) to that provider's upstream authorization URL
