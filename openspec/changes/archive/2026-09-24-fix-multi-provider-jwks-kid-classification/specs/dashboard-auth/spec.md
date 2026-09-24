# Delta Specification: Dashboard Auth Key Lookup Classification

## ADDED REQUIREMENTS

### Requirement: Foreign Key ID Must Not Trigger Provider Unreachable
The system MUST classify a PyJWT `PyJWKClientError` indicating an unmatched key ID (`Unable to find a signing key that matches`) as an `InvalidCodeError`, not a `ProviderError`.

#### Scenario: Request with Foreign OIDC Key ID evaluated by Nous Provider
- **GIVEN** a dashboard configuration with multiple providers (`nous`, `self_hosted`)
- **WHEN** an inbound Bearer token contains a `kid` that does not exist in Nous Portal's JWKS
- **AND** Nous Portal's JWKS is reachable and returns valid signing keys
- **THEN** `classify_jwks_lookup_error` MUST return `InvalidCodeError`
- **AND** `NousDashboardAuthProvider.verify_session` MUST return `None` without raising `ProviderError`
- **AND** the middleware MUST proceed to evaluate the next provider (`self_hosted`) without logging a provider unreachable warning.
