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
}

export interface NativeTokenRefresher {
  (baseUrl: string, options?: EnsureNativeAccessTokenOptions): Promise<string | null>
  force?: (baseUrl: string) => Promise<string | null>
  forceRefresh?: (baseUrl: string) => Promise<string | null>
}

export function createNativeTokenRefresher(io: NativeTokenRefreshIo): NativeTokenRefresher {
  const inFlight = new Map<string, Promise<string | null>>()

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
    options?: EnsureNativeAccessTokenOptions
  ): Promise<string | null> {
    const pending = inFlight.get(baseUrl)

    if (pending) {
      return pending
    }

    const tokens = io.load(baseUrl)

    if (!tokens) {
      return Promise.resolve(null)
    }

    const now = io.now?.() ?? Math.floor(Date.now() / 1000)
    const force = Boolean(options?.force)

    if (!force && !tokenNeedsRefresh(tokens, now)) {
      return Promise.resolve(tokens.accessToken)
    }

    if (!tokens.refreshToken) {
      if (!force && Number.isFinite(tokens.expiresAt) && now < tokens.expiresAt) {
        return Promise.resolve(tokens.accessToken)
      }

      io.clear(baseUrl)

      return Promise.resolve(null)
    }

    const request = resolveRefresh(baseUrl, tokens).finally(() => {
      if (inFlight.get(baseUrl) === request) {
        inFlight.delete(baseUrl)
      }
    })

    inFlight.set(baseUrl, request)

    return request
  }

  ensureNativeAccessToken.force = (baseUrl: string) => ensureNativeAccessToken(baseUrl, { force: true })
  ensureNativeAccessToken.forceRefresh = ensureNativeAccessToken.force

  return ensureNativeAccessToken
}
