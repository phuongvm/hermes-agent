import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, test } from 'vitest'

import {
  checkCredentialFreeEligibility,
  constructAllowlistRegistry,
  extractDashboardPublicUrl,
  probeGatewayAuthRequired,
  resolveConnectionToPackage,
  validateAndNormalizeUrl
} from './stage-gateway-connection.mjs'

describe('5.1: Staging Script Unit Tests', () => {
  test('(a) Rung 1 with a single remote entry — verifies correct output', async () => {
    const registry = {
      version: 2,
      primary: 'rem-1',
      launchMode: 'primary',
      lastUsed: 'rem-1',
      connections: [
        { id: 'local', kind: 'local', label: 'Local' },
        { id: 'rem-1', kind: 'remote', label: 'Team Gateway', url: 'https://gateway.example.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: (p) => p === path.join('/mock/appdata', 'connections.json'),
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal(res.rung, 1)
    assert.equal(res.registry.version, 2)
    assert.equal(res.registry.primary, 'rem-1')
    assert.equal(res.registry.launchMode, 'primary')
    assert.equal(res.registry.lastUsed, 'rem-1')
    assert.equal(res.registry.connections.length, 2)
    assert.deepEqual(res.registry.connections[0], { id: 'local', kind: 'local', label: 'Local' })
    assert.deepEqual(res.registry.connections[1], {
      id: 'rem-1',
      kind: 'remote',
      label: 'Team Gateway',
      url: 'https://gateway.example.com',
      authMode: 'oauth'
    })
  })

  test('(b) Rung 1 with multiple gateways — verifies primary -> lastUsed -> array-order priority', async () => {
    const regWithPrimary = {
      version: 2,
      primary: 'gw-2',
      lastUsed: 'gw-1',
      connections: [
        { id: 'gw-1', kind: 'remote', label: 'GW 1', url: 'https://gw1.com', authMode: 'oauth' },
        { id: 'gw-2', kind: 'cloud', label: 'GW 2', url: 'https://gw2.com', authMode: 'oauth' },
        { id: 'gw-3', kind: 'remote', label: 'GW 3', url: 'https://gw3.com', authMode: 'oauth' }
      ]
    }

    // 1. primary is selected
    const res1 = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(regWithPrimary),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res1.selectedGateway.id, 'gw-2')

    // 2. primary missing -> lastUsed is selected
    const regWithLastUsed = {
      ...regWithPrimary,
      primary: undefined,
      lastUsed: 'gw-3'
    }
    const res2 = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(regWithLastUsed),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res2.selectedGateway.id, 'gw-3')

    // 3. primary and lastUsed missing -> first array entry
    const regArrayOrder = {
      ...regWithPrimary,
      primary: undefined,
      lastUsed: undefined
    }
    const res3 = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(regArrayOrder),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res3.selectedGateway.id, 'gw-1')
  })

  test('(c) Rung 1 where primary points to local or ssh entry — verifies fallthrough to lastUsed/array order', async () => {
    const registry = {
      version: 2,
      primary: 'local',
      lastUsed: 'ssh-box',
      connections: [
        { id: 'local', kind: 'local', label: 'Local' },
        { id: 'ssh-box', kind: 'ssh', label: 'SSH Box', host: 'example.com' },
        { id: 'remote-gw', kind: 'remote', label: 'Remote GW', url: 'https://remote.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res.success, true)
    assert.equal(res.selectedGateway.id, 'remote-gw')
  })

  test('(d) Rung 1 with dangling primary/lastUsed IDs — verifies graceful fallthrough', async () => {
    const registry = {
      version: 2,
      primary: 'non-existent-1',
      lastUsed: 'non-existent-2',
      connections: [
        { id: 'local', kind: 'local', label: 'Local' },
        { id: 'valid-gw', kind: 'remote', label: 'Valid GW', url: 'https://valid.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res.success, true)
    assert.equal(res.selectedGateway.id, 'valid-gw')
  })

  test('(e) Rung 1 with only local/ssh entries — verifies fallthrough to Rung 2', async () => {
    const registry = {
      version: 2,
      primary: 'local',
      connections: [
        { id: 'local', kind: 'local', label: 'Local' },
        { id: 'ssh-box', kind: 'ssh', label: 'SSH', host: 'box.internal' }
      ]
    }

    const yaml = `
dashboard:
  public_url: https://rung2.example.com
`
    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      hermesHome: '/mock/hermes',
      fileExistsFn: (p) => p.includes('connections.json') || p.includes('config.yaml'),
      readFileFn: (p) => (p.includes('connections.json') ? JSON.stringify(registry) : yaml),
      probeFn: async () => ({ success: true, auth_required: true }),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal(res.rung, 2)
    assert.equal(res.selectedGateway.url, 'https://rung2.example.com')
  })

  test('(f) Rung 2 with dashboard.public_url in config.yaml — verifies synthesized output', async () => {
    const yaml = `
dashboard:
  port: 9119
  public_url: "https://my-team.hermes.ai:8443"
`
    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      hermesHome: '/mock/hermes',
      fileExistsFn: (p) => p.includes('config.yaml'),
      readFileFn: () => yaml,
      probeFn: async () => ({ success: true, auth_required: true }),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal(res.rung, 2)
    assert.equal(res.selectedGateway.url, 'https://my-team.hermes.ai:8443')
    assert.equal(res.selectedGateway.label, 'my-team.hermes.ai Gateway')
    assert.equal(res.selectedGateway.authMode, 'oauth')
    assert.equal(res.registry.primary, res.selectedGateway.id)
    assert.equal(res.registry.launchMode, 'primary')
  })

  test('(g) Neither rung resolves — verifies exit code 1 / error message listing both paths', async () => {
    const res = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      hermesHome: '/mock/hermes',
      fileExistsFn: () => false,
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, false)
    assert.ok(res.error.includes('Failed to resolve a gateway connection'))
    assert.ok(res.error.includes('Rung 1 (AppData connections.json)'))
    assert.ok(res.error.includes('Rung 2 (config.yaml)'))
  })

  test('(h) Legacy v1 or malformed registry — verifies graceful handling and fallthrough', async () => {
    // Malformed JSON
    const res1 = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      hermesHome: '/mock/hermes',
      fileExistsFn: (p) => p.includes('connections.json'),
      readFileFn: () => '{ invalid json',
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res1.success, false)

    // Legacy v1 shape (connections is not an array)
    const res2 = await resolveConnectionToPackage({
      appDataPath: '/mock/appdata',
      hermesHome: '/mock/hermes',
      fileExistsFn: (p) => p.includes('connections.json') || p.includes('config.yaml'),
      readFileFn: (p) =>
        p.includes('connections.json')
          ? JSON.stringify({ version: 1, remote: { url: 'https://legacy.com' } })
          : 'dashboard:\n  public_url: https://fallback.com',
      probeFn: async () => ({ success: true, auth_required: true }),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res2.success, true)
    assert.equal(res2.rung, 2)
  })
})

describe('5.2: Allowlist Security Tests', () => {
  test('(a) Source registry with token and headers fields — verifies they do not appear in output', async () => {
    const rawEntry = {
      id: 'gw-sec',
      kind: 'remote',
      label: 'Secure GW',
      url: 'https://sec.com',
      authMode: 'oauth',
      token: 'CRITICAL_SECRET_TOKEN_123',
      headers: { 'CF-Access-Token': 'SECRET_HEADER' }
    }

    const staged = constructAllowlistRegistry(rawEntry)
    const stagedJson = JSON.stringify(staged)

    assert.equal(stagedJson.includes('CRITICAL_SECRET_TOKEN_123'), false)
    assert.equal(stagedJson.includes('SECRET_HEADER'), false)
    assert.equal('token' in staged.connections[1], false)
    assert.equal('headers' in staged.connections[1], false)
  })

  test('(b) Source registry with SSH entries — verifies they are excluded entirely', async () => {
    const registry = {
      version: 2,
      connections: [
        {
          id: 'ssh-1',
          kind: 'ssh',
          label: 'SSH Box',
          host: 'ssh.secret.com',
          user: 'admin',
          port: 2222,
          keyPath: '/secrets/id_rsa',
          remoteHermesPath: '/opt/hermes',
          remoteProfile: 'prod'
        },
        { id: 'rem-1', kind: 'remote', label: 'Remote', url: 'https://rem.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    const kinds = res.registry.connections.map((c) => c.kind)
    assert.equal(kinds.includes('ssh'), false)
    assert.equal(JSON.stringify(res.registry).includes('id_rsa'), false)
  })

  test('(c) Source registry with quarantined array — verifies it does not appear in output', async () => {
    const registry = {
      version: 2,
      quarantined: [{ id: 'bad-1', reason: 'malformed' }],
      connections: [
        { id: 'rem-1', kind: 'remote', label: 'Remote', url: 'https://rem.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal('quarantined' in res.registry, false)
  })

  test('(d) Source registry with unknown/future top-level and per-connection fields — verifies exclusion', async () => {
    const rawEntry = {
      id: 'gw-future',
      kind: 'remote',
      label: 'Future GW',
      url: 'https://future.com',
      authMode: 'oauth',
      futureFeatureFlag: true,
      telemetryEndpoint: 'https://track.com',
      arbitraryInternalObject: { foo: 'bar' }
    }

    const staged = constructAllowlistRegistry(rawEntry)
    assert.deepEqual(Object.keys(staged).sort(), ['connections', 'lastUsed', 'launchMode', 'primary', 'version'].sort())
    assert.deepEqual(Object.keys(staged.connections[1]).sort(), ['authMode', 'id', 'kind', 'label', 'url'].sort())
  })

  test('(e) Source registry with nested credential-like objects — verifies deep fields do not survive', async () => {
    const rawEntry = {
      id: 'gw-deep',
      kind: 'remote',
      label: 'Deep GW',
      url: 'https://deep.com',
      authMode: 'oauth',
      credentials: { password: 'p1', clientSecret: 's2' },
      tls: { clientCert: 'CERT_DATA' }
    }

    const staged = constructAllowlistRegistry(rawEntry)
    assert.equal(JSON.stringify(staged).includes('CERT_DATA'), false)
    assert.equal(JSON.stringify(staged).includes('clientSecret'), false)
  })
})

describe('5.3: YAML Parsing Edge Cases for Rung 2', () => {
  test('(a) Quoted public_url values (single/double quotes)', () => {
    const yamlDouble = `
dashboard:
  public_url: "https://quoted-double.example.com"
`
    assert.equal(extractDashboardPublicUrl(yamlDouble), 'https://quoted-double.example.com')

    const yamlSingle = `
dashboard:
  public_url: 'https://quoted-single.example.com'
`
    assert.equal(extractDashboardPublicUrl(yamlSingle), 'https://quoted-single.example.com')
  })

  test('(b) Inline comments after URL', () => {
    const yaml = `
dashboard:
  public_url: https://comment.example.com # team gateway for region-a
`
    assert.equal(extractDashboardPublicUrl(yaml), 'https://comment.example.com')
  })

  test('(c) Empty/blank public_url', () => {
    const yaml1 = `
dashboard:
  public_url:
`
    assert.equal(extractDashboardPublicUrl(yaml1), null)

    const yaml2 = `
dashboard:
  public_url: "" # empty string
`
    assert.equal(extractDashboardPublicUrl(yaml2), null)
  })

  test('(d) Similarly-named keys outside dashboard section', () => {
    const yaml = `
other_service:
  public_url: https://should-not-match.com

web_server:
  public_url: https://also-wrong.com

dashboard:
  port: 8080
  public_url: https://correct-dashboard.com

gateway:
  public_url: https://after-dashboard.com
`
    assert.equal(extractDashboardPublicUrl(yaml), 'https://correct-dashboard.com')
  })

  test('(e) Malformed URL values — verifies rejection during normalization', () => {
    assert.equal(validateAndNormalizeUrl('not-a-url'), null)
    assert.equal(validateAndNormalizeUrl('ftp://insecure.com'), null)
    assert.equal(validateAndNormalizeUrl('javascript:alert(1)'), null)
    assert.equal(validateAndNormalizeUrl('https://valid.com:9000/'), 'https://valid.com:9000')
  })
})

describe('5.5: No-Clobber Decision Logic and NSIS Hook Verification', () => {
  test('(a) Verifies NSIS file existence and macro definitions', () => {
    const nshPath = path.resolve(import.meta.dirname, '../resources/installer.nsh')
    assert.ok(fs.existsSync(nshPath), 'installer.nsh must exist at apps/desktop/resources/installer.nsh')

    const content = fs.readFileSync(nshPath, 'utf8')
    assert.ok(content.includes('!macro customInstall'), 'must define customInstall macro')
    assert.ok(content.includes('IfFileExists "$APPDATA\\Hermes\\connections.json"'), 'must check for existing connections.json')
    assert.ok(content.includes('CreateDirectory "$APPDATA\\Hermes"'), 'must ensure Hermes dir exists')
    assert.ok(content.includes('File "/oname=connections.json" "${BUILD_RESOURCES_DIR}\\default-connections.json"'), 'must extract default-connections.json to connections.json')
  })

  test('(b) Verifies DetailPrint statements for install-time visibility', () => {
    const nshPath = path.resolve(import.meta.dirname, '../resources/installer.nsh')
    const content = fs.readFileSync(nshPath, 'utf8')

    assert.ok(content.includes('DetailPrint "Configuring Team Hermes Gateway..."'), 'start DetailPrint missing')
    assert.ok(content.includes('DetailPrint "Successfully initialized Hermes Gateway connection."'), 'seed DetailPrint missing')
    assert.ok(content.includes('DetailPrint "Existing Hermes Gateway configuration found, skipping seed."'), 'skip DetailPrint missing')
  })

  test('(c) Simulates no-clobber decision logic with/without pre-existing target file', () => {
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-noclobber-test-'))
    try {
      const targetFile = path.join(tempDir, 'connections.json')
      const sourceFile = path.join(tempDir, 'default-connections.json')

      fs.writeFileSync(sourceFile, JSON.stringify({ seeded: true }), 'utf8')

      // 1. Fresh install (file missing) -> seed
      function simulateNsisCustomInstall(appDataDir) {
        const dest = path.join(appDataDir, 'connections.json')
        const logs = ['Configuring Team Hermes Gateway...']
        if (fs.existsSync(dest)) {
          logs.push('Existing Hermes Gateway configuration found, skipping seed.')
          return { seeded: false, logs }
        }
        fs.copyFileSync(sourceFile, dest)
        logs.push('Successfully initialized Hermes Gateway connection.')
        return { seeded: true, logs }
      }

      const run1 = simulateNsisCustomInstall(tempDir)
      assert.equal(run1.seeded, true)
      assert.deepEqual(run1.logs, [
        'Configuring Team Hermes Gateway...',
        'Successfully initialized Hermes Gateway connection.'
      ])
      assert.deepEqual(JSON.parse(fs.readFileSync(targetFile, 'utf8')), { seeded: true })

      // 2. Existing install (file already exists with custom content) -> no-clobber
      fs.writeFileSync(targetFile, JSON.stringify({ customUserConfig: true }), 'utf8')

      const run2 = simulateNsisCustomInstall(tempDir)
      assert.equal(run2.seeded, false)
      assert.deepEqual(run2.logs, [
        'Configuring Team Hermes Gateway...',
        'Existing Hermes Gateway configuration found, skipping seed.'
      ])
      // Existing content untouched
      assert.deepEqual(JSON.parse(fs.readFileSync(targetFile, 'utf8')), { customUserConfig: true })
    } finally {
      fs.rmSync(tempDir, { recursive: true, force: true })
    }
  })
})

describe('5.6: Credential-Free Eligibility Tests', () => {
  test('(a) Source registry whose only remote entry has authMode: "token" — candidate rejected with token diagnostic', async () => {
    const registry = {
      version: 2,
      connections: [
        { id: 'rem-token', kind: 'remote', label: 'Token Gateway', url: 'https://tok.com', authMode: 'token' }
      ]
    }

    const check = checkCredentialFreeEligibility(registry.connections[0])
    assert.equal(check.eligible, false)
    assert.ok(check.reason.includes("authMode='token' requires saved credentials"))

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock',
      hermesHome: '/mock-empty',
      fileExistsFn: (p) => p.includes('connections.json'),
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })
    assert.equal(res.success, false)
  })

  test('(b) Source registry whose only remote entry has authMode absent, null, or unknown non-oauth string — candidate rejected', () => {
    assert.equal(checkCredentialFreeEligibility({ label: 'G1', url: 'https://g1.com' }).eligible, false)
    assert.equal(checkCredentialFreeEligibility({ label: 'G2', url: 'https://g2.com', authMode: null }).eligible, false)
    assert.equal(checkCredentialFreeEligibility({ label: 'G3', url: 'https://g3.com', authMode: 'basic' }).eligible, false)
    assert.equal(checkCredentialFreeEligibility({ label: 'G4', url: 'https://g4.com', authMode: 'OAuth' }).eligible, false) // case-sensitive exact 'oauth'
  })

  test('(c) Source registry whose only remote entry has non-empty headers — candidate rejected with headers diagnostic', () => {
    const check = checkCredentialFreeEligibility({
      label: 'Header Gateway',
      url: 'https://g.com',
      authMode: 'oauth',
      headers: { 'CF-Access-Client-Id': 'client-id' }
    })
    assert.equal(check.eligible, false)
    assert.ok(check.reason.includes('non-empty headers require access-proxy credentials'))
  })

  test('(d) Source registry with two remote entries where primary is token and second is oauth — first skipped, second selected', async () => {
    const registry = {
      version: 2,
      primary: 'rem-token',
      connections: [
        { id: 'rem-token', kind: 'remote', label: 'Token GW', url: 'https://tok.com', authMode: 'token' },
        { id: 'rem-oauth', kind: 'remote', label: 'OAuth GW', url: 'https://oauth.com', authMode: 'oauth' }
      ]
    }

    const res = await resolveConnectionToPackage({
      appDataPath: '/mock',
      fileExistsFn: () => true,
      readFileFn: () => JSON.stringify(registry),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal(res.selectedGateway.id, 'rem-oauth')
  })

  test('(e) Source registry where all remote/cloud entries are ineligible — complete Rung 1 fallthrough to Rung 2', async () => {
    const registry = {
      version: 2,
      connections: [
        { id: 'rem-1', kind: 'remote', label: 'Token 1', url: 'https://tok1.com', authMode: 'token' },
        { id: 'rem-2', kind: 'remote', label: 'Header 2', url: 'https://tok2.com', authMode: 'oauth', headers: { k: 'v' } }
      ]
    }
    const yaml = `
dashboard:
  public_url: https://rung2-fallback.com
`
    const res = await resolveConnectionToPackage({
      appDataPath: '/mock',
      hermesHome: '/mock-hermes',
      fileExistsFn: () => true,
      readFileFn: (p) => (p.includes('connections.json') ? JSON.stringify(registry) : yaml),
      probeFn: async () => ({ success: true, auth_required: true }),
      logger: { log: () => {}, error: () => {} }
    })

    assert.equal(res.success, true)
    assert.equal(res.rung, 2)
    assert.equal(res.selectedGateway.url, 'https://rung2-fallback.com')
  })

  test('(f) Remote entry with authMode: "oauth" and no headers (or empty headers) — accepted', () => {
    assert.equal(checkCredentialFreeEligibility({ label: 'G1', authMode: 'oauth' }).eligible, true)
    assert.equal(checkCredentialFreeEligibility({ label: 'G2', authMode: 'oauth', headers: null }).eligible, true)
    assert.equal(checkCredentialFreeEligibility({ label: 'G3', authMode: 'oauth', headers: {} }).eligible, true)
  })
})

describe('5.7: Auth Mode Classification Tests for Rung 2', () => {
  test('(a) /api/status probe returns JSON with auth_required: true — synthesized entry uses authMode: "oauth", succeeds', async () => {
    const probe = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => ({ auth_required: true, auth_providers: ['google'] })
    })
    assert.equal(probe.success, true)
    assert.equal(probe.auth_required, true)
  })

  test('(b) /api/status probe returns JSON with auth_required: false — build fails with diagnostic identifying token auth', async () => {
    const probe = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => ({ auth_required: false })
    })
    assert.equal(probe.success, false)
    assert.equal(probe.tokenAuth, true)
    assert.ok(probe.reason.includes('auth_required=false (token auth)'))
    assert.ok(probe.reason.includes('Cannot provide zero-config staging'))
  })

  test('(c) /api/status probe times out or returns network/HTTP error — build fails with probe-failure diagnostic', async () => {
    const probeTimeout = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => {
        throw new Error('Timed out after 10s')
      }
    })
    assert.equal(probeTimeout.success, false)
    assert.ok(probeTimeout.reason.includes('Timed out after 10s'))

    const yaml = 'dashboard:\n  public_url: https://probe-test.com'
    await assert.rejects(
      async () => {
        await resolveConnectionToPackage({
          appDataPath: '/mock',
          hermesHome: '/mock-hermes',
          fileExistsFn: (p) => p.includes('config.yaml'),
          readFileFn: () => yaml,
          probeFn: async () => ({ success: false, reason: 'HTTP 502 Bad Gateway' }),
          logger: { log: () => {}, error: () => {} }
        })
      },
      /Cannot determine auth mode for https:\/\/probe-test\.com: \/api\/status probe failed/
    )
  })

  test('(d) /api/status probe returns non-JSON or JSON without boolean auth_required — build fails closed with diagnostic', async () => {
    // Missing auth_required
    const probeMissing = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => ({ other_field: 123 })
    })
    assert.equal(probeMissing.success, false)
    assert.ok(probeMissing.reason.includes('Missing or non-boolean "auth_required"'))

    // Non-boolean auth_required (e.g. string "true")
    const probeString = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => ({ auth_required: 'true' })
    })
    assert.equal(probeString.success, false)
    assert.ok(probeString.reason.includes('Missing or non-boolean "auth_required"'))

    // Non-object body
    const probeStringBody = await probeGatewayAuthRequired('https://probe-test.com', {
      fetchFn: async () => '<html>502 Bad Gateway</html>'
    })
    assert.equal(probeStringBody.success, false)
    assert.ok(probeStringBody.reason.includes('Empty or non-object status payload'))
  })
})
