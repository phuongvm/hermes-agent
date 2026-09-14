import { type NativeTokenSet, parseTokenResponse, tokenNeedsRefresh } from './native-oauth'

interface NativeTokenRefreshIo {
  load: (baseUrl: string) => NativeTokenSet | null
  store: (baseUrl: string, tokens: NativeTokenSet) => void
  clear: (baseUrl: string) => void
  refresh: (baseUrl: string, tokens: NativeTokenSet) => Promise<unknown>
  now?: () => number
}

export interface EnsureNativeAccessTokenOptions {
  force?: boolean
  rejectedBearer?: string
}

export interface NativeTokenRefresher {
  (baseUrl: string, options?: EnsureNativeAccessTokenOptions | string): Promise<string | null>
  force?: (baseUrl: string, rejectedBearer?: string) => Promise<string | null>
  forceRefresh?: (baseUrl: string, rejectedBearer?: string) => Promise<string | null>
}

export function createNativeTokenRefresher(io: NativeTokenRefreshIo): NativeTokenRefresher {
  const inFlight = new Map<string, { promise: Promise<string | null>; refreshingBearer?: string }>()

  async function resolveRefresh(baseUrl: string, tokens: NativeTokenSet): Promise<string | null> {
    const unchanged = () => {
      const current = io.load(baseUrl)

      return current?.accessToken === tokens.accessToken && current?.refreshToken === tokens.refreshToken
    }

    try {
      const body = await io.refresh(baseUrl, tokens)
      const rotated = parseTokenResponse(body)

      if (!unchanged()) {
        return io.load(baseUrl)?.accessToken ?? null
      }

      if (!rotated.refreshToken) {
        rotated.refreshToken = tokens.refreshToken
      }

      io.store(baseUrl, rotated)

      return rotated.accessToken
    } catch (error) {
      if (!unchanged()) {
        return io.load(baseUrl)?.accessToken ?? null
      }

      const statusCode =
        (error && typeof error === 'object' && 'statusCode' in error && Number(error.statusCode)) ||
        (error instanceof Error && Number((error.message.match(/^(\d{3}):/) || [])[1])) ||
        0

      if (statusCode === 401) {
        io.clear(baseUrl)

        return null
      }

      throw error
    }
  }

  function ensureNativeAccessToken(
    baseUrl: string,
    options?: EnsureNativeAccessTokenOptions | string
  ): Promise<string | null> {
    const normalizedOptions: EnsureNativeAccessTokenOptions =
      typeof options === 'string' ? { force: true, rejectedBearer: options } : (options ?? {})

    const force = Boolean(normalizedOptions.force)
    const rejectedBearer = normalizedOptions.rejectedBearer

    const tokens = io.load(baseUrl)

    if (!tokens) {
      return Promise.resolve(null)
    }

    const now = io.now?.() ?? Math.floor(Date.now() / 1000)

    // 1. Generation/rejected-bearer aware recovery:
    // If a forced refresh was requested for a specific rejected bearer,
    // and storage already contains a different access token than the one rejected,
    // reuse that current bearer instead of forcing another refresh.
    if (force && rejectedBearer && tokens.accessToken && tokens.accessToken !== rejectedBearer) {
      if (!tokenNeedsRefresh(tokens, now)) {
        return Promise.resolve(tokens.accessToken)
      }
    }

    // 2. Preserve one in-flight refresh per gateway when the rejected bearer is still current:
    const pending = inFlight.get(baseUrl)

    if (pending) {
      if (!rejectedBearer || !pending.refreshingBearer || pending.refreshingBearer === rejectedBearer) {
        return pending.promise
      }
    }

    // 3. Normal client-side expiration check:
    if (!force && !tokenNeedsRefresh(tokens, now)) {
      return Promise.resolve(tokens.accessToken)
    }

    // 4. Missing refresh token handling:
    if (!tokens.refreshToken) {
      if (!force && Number.isFinite(tokens.expiresAt) && now < tokens.expiresAt) {
        return Promise.resolve(tokens.accessToken)
      }

      io.clear(baseUrl)

      return Promise.resolve(null)
    }

    // 5. Trigger refresh:
    const refreshingBearer = tokens.accessToken

    const request = resolveRefresh(baseUrl, tokens).finally(() => {
      if (inFlight.get(baseUrl)?.promise === request) {
        inFlight.delete(baseUrl)
      }
    })

    inFlight.set(baseUrl, { promise: request, refreshingBearer })

    return request
  }

  ensureNativeAccessToken.force = (baseUrl: string, rejectedBearer?: string) =>
    ensureNativeAccessToken(baseUrl, { force: true, rejectedBearer })
  ensureNativeAccessToken.forceRefresh = ensureNativeAccessToken.force

  return ensureNativeAccessToken
}
