#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import http from 'node:http'
import https from 'node:https'
import os from 'node:os'
import { fileURLToPath } from 'node:url'

export const PROBE_TIMEOUT_MS = 10_000

/**
 * Validates whether a URL string is a well-formed HTTP/HTTPS URL.
 * Normalizes by trimming trailing slashes.
 */
export function validateAndNormalizeUrl(rawUrl) {
  if (typeof rawUrl !== 'string' || !rawUrl.trim()) {
    return null
  }
  try {
    const parsed = new URL(rawUrl.trim())
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return null
    }
    // Strip trailing slash
    return parsed.origin + (parsed.pathname === '/' ? '' : parsed.pathname.replace(/\/+$/, ''))
  } catch {
    return null
  }
}

/**
 * Scoped parser for config.yaml to extract dashboard.public_url.
 * Locates the `dashboard:` section block and looks only within it for `public_url`.
 */
export function extractDashboardPublicUrl(yamlContent) {
  if (typeof yamlContent !== 'string') {
    return null
  }

  const lines = yamlContent.split(/\r?\n/)
  let insideDashboard = false
  let dashboardIndent = 0

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i]
    // Strip trailing comment for section detection, but check indent
    const lineWithoutComment = rawLine.replace(/#.*$/, '')
    const trimmed = lineWithoutComment.trim()

    if (!trimmed) {
      continue
    }

    const currentIndent = rawLine.match(/^\s*/)[0].length

    // Check if we are starting dashboard section (top-level or child)
    if (!insideDashboard) {
      if (/^dashboard\s*:\s*$/.test(trimmed)) {
        insideDashboard = true
        dashboardIndent = currentIndent
      }
      continue
    }

    // If we are inside dashboard, any line at or shallower than dashboardIndent
    // (and not blank) ends the dashboard section.
    if (currentIndent <= dashboardIndent) {
      insideDashboard = false
      // Could this line itself be a new dashboard section?
      if (/^dashboard\s*:\s*$/.test(trimmed)) {
        insideDashboard = true
        dashboardIndent = currentIndent
      }
      continue
    }

    // Inside dashboard section: look for public_url
    const match = trimmed.match(/^public_url\s*:\s*(.*)$/)
    if (match) {
      let val = match[1].trim()
      // Exclude inline comment if any
      val = val.replace(/\s+#.*$/, '').trim()
      // Strip outer double or single quotes
      if (
        (val.startsWith('"') && val.endsWith('"') && val.length >= 2) ||
        (val.startsWith("'") && val.endsWith("'") && val.length >= 2)
      ) {
        val = val.slice(1, -1).trim()
      }
      if (!val) {
        return null
      }
      return val
    }
  }

  return null
}

/**
 * Probes ${publicUrl}/api/status via HTTP/HTTPS GET with timeout.
 * Returns { success: true, auth_required: boolean } or { success: false, reason: string, tokenAuth?: boolean }
 */
export async function probeGatewayAuthRequired(publicUrl, { timeoutMs = PROBE_TIMEOUT_MS, fetchFn } = {}) {
  const normalizedUrl = validateAndNormalizeUrl(publicUrl)
  if (!normalizedUrl) {
    return {
      success: false,
      reason: `Invalid public URL: "${publicUrl}"`
    }
  }

  const targetUrl = `${normalizedUrl}/api/status`

  if (typeof fetchFn === 'function') {
    try {
      const resp = await fetchFn(targetUrl, { timeoutMs })
      return evaluateStatusResponseBody(resp, publicUrl)
    } catch (err) {
      return {
        success: false,
        reason: err.message || String(err)
      }
    }
  }

  return new Promise((resolve) => {
    let resolved = false
    const finish = (result) => {
      if (!resolved) {
        resolved = true
        resolve(result)
      }
    }

    try {
      const urlObj = new URL(targetUrl)
      const client = urlObj.protocol === 'https:' ? https : http

      const req = client.get(
        targetUrl,
        {
          headers: { Accept: 'application/json' },
          timeout: timeoutMs
        },
        (res) => {
          let data = ''
          res.setEncoding('utf8')
          res.on('data', (chunk) => {
            data += chunk
          })
          res.on('end', () => {
            if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
              return finish({
                success: false,
                reason: `HTTP ${res.statusCode}`
              })
            }
            try {
              const body = JSON.parse(data)
              finish(evaluateStatusResponseBody(body, publicUrl))
            } catch (err) {
              finish({
                success: false,
                reason: `Malformed JSON response (${err.message})`
              })
            }
          })
        }
      )

      req.on('timeout', () => {
        req.destroy()
        finish({
          success: false,
          reason: `Timed out after ${Math.round(timeoutMs / 1000)}s`
        })
      })

      req.on('error', (err) => {
        finish({
          success: false,
          reason: err.message || String(err)
        })
      })
    } catch (err) {
      finish({
        success: false,
        reason: err.message || String(err)
      })
    }
  })
}

function evaluateStatusResponseBody(body, publicUrl) {
  if (!body || typeof body !== 'object') {
    return {
      success: false,
      reason: 'Empty or non-object status payload'
    }
  }

  if (typeof body.auth_required !== 'boolean') {
    return {
      success: false,
      reason: 'Missing or non-boolean "auth_required" field in /api/status'
    }
  }

  if (body.auth_required === true) {
    return {
      success: true,
      auth_required: true
    }
  }

  return {
    success: false,
    tokenAuth: true,
    reason: `Gateway at ${publicUrl} has auth_required=false (token auth), which requires saved credentials. Cannot provide zero-config staging.`
  }
}

/**
 * Checks whether a candidate connection passes the Credential-Free Eligibility Check:
 * 1. authMode must be strictly === 'oauth'.
 * 2. headers must be absent, null, or an empty object.
 */
export function checkCredentialFreeEligibility(candidate) {
  const label = candidate.label || candidate.id || 'unknown'

  if (candidate.authMode !== 'oauth') {
    return {
      eligible: false,
      reason: `Candidate '${label}' rejected: authMode='${candidate.authMode}' requires saved credentials or is not explicitly oauth`
    }
  }

  if (candidate.headers && typeof candidate.headers === 'object' && Object.keys(candidate.headers).length > 0) {
    return {
      eligible: false,
      reason: `Candidate '${label}' rejected: non-empty headers require access-proxy credentials`
    }
  }

  return { eligible: true }
}

/**
 * Reconstructs default-connections.json strictly via allowlist.
 */
export function constructAllowlistRegistry(selectedGateway) {
  const localEntry = {
    id: 'local',
    kind: 'local',
    label: 'Local'
  }

  const gatewayEntry = {
    id: selectedGateway.id,
    kind: selectedGateway.kind,
    label: selectedGateway.label,
    url: selectedGateway.url,
    authMode: selectedGateway.authMode
  }

  if (selectedGateway.org !== undefined && selectedGateway.org !== null) {
    gatewayEntry.org = selectedGateway.org
  }

  return {
    version: 2,
    primary: selectedGateway.id,
    launchMode: 'primary',
    lastUsed: selectedGateway.id,
    connections: [localEntry, gatewayEntry]
  }
}

/**
 * Main resolution ladder function.
 */
export async function resolveConnectionToPackage(options = {}) {
  const {
    appDataPath = process.env.APPDATA ? path.join(process.env.APPDATA, 'Hermes') : null,
    hermesHome = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes'),
    readFileFn = (p) => fs.readFileSync(p, 'utf8'),
    fileExistsFn = (p) => fs.existsSync(p),
    probeFn = probeGatewayAuthRequired,
    logger = console
  } = options

  const diagnostics = []

  // Rung 1: %APPDATA%\Hermes\connections.json
  const rung1Path = appDataPath ? path.join(appDataPath, 'connections.json') : null
  logger.log(`[stage-gateway-connection] Checking Rung 1: AppData registry at ${rung1Path || 'N/A'}`)

  if (rung1Path && fileExistsFn(rung1Path)) {
    try {
      const content = readFileFn(rung1Path)
      const registry = JSON.parse(content)

      if (registry && Array.isArray(registry.connections)) {
        // Filter to remote or cloud entries only
        const candidates = registry.connections.filter(
          (c) => c && (c.kind === 'remote' || c.kind === 'cloud')
        )

        if (candidates.length === 0) {
          logger.log('[stage-gateway-connection] Rung 1: No remote or cloud connections found in registry.')
          diagnostics.push('Rung 1 (AppData connections.json): No remote/cloud connections in registry')
        } else {
          // Find candidates in priority: primary -> lastUsed -> array order
          const orderedCandidates = []
          const candidateMap = new Map(candidates.map((c) => [c.id, c]))

          if (registry.primary && candidateMap.has(registry.primary)) {
            orderedCandidates.push(candidateMap.get(registry.primary))
          }
          if (registry.lastUsed && candidateMap.has(registry.lastUsed) && registry.lastUsed !== registry.primary) {
            orderedCandidates.push(candidateMap.get(registry.lastUsed))
          }
          for (const cand of candidates) {
            if (!orderedCandidates.includes(cand)) {
              orderedCandidates.push(cand)
            }
          }

          let selected = null
          for (const candidate of orderedCandidates) {
            const eligibility = checkCredentialFreeEligibility(candidate)
            if (eligibility.eligible) {
              selected = candidate
              break
            } else {
              logger.log(`[stage-gateway-connection] ${eligibility.reason}`)
              diagnostics.push(`Rung 1 candidate: ${eligibility.reason}`)
            }
          }

          if (selected) {
            logger.log(`[stage-gateway-connection] Rung 1 succeeded: selected gateway "${selected.label}" (${selected.url})`)
            return {
              success: true,
              rung: 1,
              registry: constructAllowlistRegistry(selected),
              selectedGateway: selected
            }
          } else {
            logger.log('[stage-gateway-connection] Rung 1: All remote/cloud candidates were ineligible.')
            diagnostics.push('Rung 1: All remote/cloud entries failed credential-free eligibility')
          }
        }
      } else {
        logger.log('[stage-gateway-connection] Rung 1: Malformed connections.json (connections is not an array).')
        diagnostics.push('Rung 1: Malformed connections.json structure')
      }
    } catch (err) {
      logger.log(`[stage-gateway-connection] Rung 1: Failed to read/parse connections.json (${err.message})`)
      diagnostics.push(`Rung 1 read error: ${err.message}`)
    }
  } else {
    logger.log('[stage-gateway-connection] Rung 1: connections.json does not exist, skipping.')
    diagnostics.push(`Rung 1 (AppData connections.json): File does not exist at ${rung1Path}`)
  }

  // Rung 2: $HERMES_HOME/config.yaml
  const rung2Path = hermesHome ? path.join(hermesHome, 'config.yaml') : null
  logger.log(`[stage-gateway-connection] Checking Rung 2: config.yaml at ${rung2Path || 'N/A'}`)

  if (rung2Path && fileExistsFn(rung2Path)) {
    try {
      const yamlContent = readFileFn(rung2Path)
      const rawPublicUrl = extractDashboardPublicUrl(yamlContent)

      if (!rawPublicUrl) {
        logger.log('[stage-gateway-connection] Rung 2: dashboard.public_url not found or empty in config.yaml.')
        diagnostics.push('Rung 2: dashboard.public_url not found or empty in config.yaml')
      } else {
        const normalizedUrl = validateAndNormalizeUrl(rawPublicUrl)
        if (!normalizedUrl) {
          logger.log(`[stage-gateway-connection] Rung 2: dashboard.public_url "${rawPublicUrl}" is not a valid HTTP/HTTPS URL.`)
          diagnostics.push(`Rung 2: Malformed dashboard.public_url "${rawPublicUrl}"`)
        } else {
          logger.log(`[stage-gateway-connection] Rung 2: Probing ${normalizedUrl}/api/status...`)
          const probeResult = await probeFn(normalizedUrl)

          if (probeResult.success && probeResult.auth_required === true) {
            const urlObj = new URL(normalizedUrl)
            const label = `${urlObj.hostname} Gateway`
            const synthesizedGateway = {
              id: 'remote-gateway-staged',
              kind: 'remote',
              label,
              url: normalizedUrl,
              authMode: 'oauth'
            }

            logger.log(`[stage-gateway-connection] Rung 2 succeeded: synthesized gateway "${label}" (${normalizedUrl})`)
            return {
              success: true,
              rung: 2,
              registry: constructAllowlistRegistry(synthesizedGateway),
              selectedGateway: synthesizedGateway
            }
          } else if (probeResult.tokenAuth) {
            const errorMsg = probeResult.reason
            logger.error(`[stage-gateway-connection] Rung 2 failed: ${errorMsg}`)
            throw new Error(errorMsg)
          } else {
            const errorMsg = `Cannot determine auth mode for ${normalizedUrl}: /api/status probe failed (${probeResult.reason}). Cannot guarantee zero-config connectivity.`
            logger.error(`[stage-gateway-connection] Rung 2 failed: ${errorMsg}`)
            throw new Error(errorMsg)
          }
        }
      }
    } catch (err) {
      if (err.message && err.message.includes('Cannot provide zero-config staging') || err.message.includes('Cannot guarantee zero-config connectivity')) {
        throw err
      }
      logger.log(`[stage-gateway-connection] Rung 2: Error reading/evaluating config.yaml (${err.message})`)
      diagnostics.push(`Rung 2 error: ${err.message}`)
    }
  } else {
    logger.log('[stage-gateway-connection] Rung 2: config.yaml does not exist, skipping.')
    diagnostics.push(`Rung 2 (config.yaml): File does not exist at ${rung2Path}`)
  }

  // Neither rung resolved
  const summaryMsg = [
    'Failed to resolve a gateway connection for packaging.',
    'Checked resolution paths:',
    ...diagnostics.map((d) => `  - ${d}`)
  ].join('\n')

  return {
    success: false,
    diagnostics,
    error: summaryMsg
  }
}

/**
 * CLI execution entrypoint
 */
export async function main() {
  const logger = console
  try {
    const result = await resolveConnectionToPackage({ logger })

    if (!result.success) {
      logger.error(result.error)
      process.exit(1)
    }

    const scriptDir = path.dirname(fileURLToPath(import.meta.url))
    const buildDir = path.resolve(scriptDir, '..', 'build')
    const targetPath = path.join(buildDir, 'default-connections.json')

    if (!fs.existsSync(buildDir)) {
      fs.mkdirSync(buildDir, { recursive: true })
    }

    fs.writeFileSync(targetPath, JSON.stringify(result.registry, null, 2) + '\n', 'utf8')
    logger.log(`[stage-gateway-connection] Successfully wrote staged connection registry to ${targetPath}`)
  } catch (err) {
    logger.error(`[stage-gateway-connection] Build error: ${err.message}`)
    process.exit(1)
  }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main()
}
