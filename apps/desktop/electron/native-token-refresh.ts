import { type NativeTokenSet, parseTokenResponse, tokenNeedsRefresh } from './native-oauth'

interface NativeTokenRefreshIo {
  load: (baseUrl: string) => NativeTokenSet | null
  store: (baseUrl: string, tokens: NativeTokenSet) => void
  clear: (baseUrl: string) => void
  refresh: (baseUrl: string, tokens: NativeTokenSet) => Promise<unknown>
  now?: () => number
}

export function createNativeTokenRefresher(io: NativeTokenRefreshIo) {
  const inFlight = new Map<string, Promise<string | null>>()

  async function resolve(baseUrl: string): Promise<string | null> {
    const tokens = io.load(baseUrl)

    if (!tokens) return null

    const now = io.now?.() ?? Math.floor(Date.now() / 1000)

    if (!tokenNeedsRefresh(tokens, now)) return tokens.accessToken

    if (!tokens.refreshToken) {
      if (Number.isFinite(tokens.expiresAt) && now < tokens.expiresAt) return tokens.accessToken

      io.clear(baseUrl)
      return null
    }

    const unchanged = () => {
      const current = io.load(baseUrl)
      return current?.accessToken === tokens.accessToken && current?.refreshToken === tokens.refreshToken
    }

    try {
      const body = await io.refresh(baseUrl, tokens)
      const rotated = parseTokenResponse(body)

      if (!unchanged()) return io.load(baseUrl)?.accessToken ?? null

      if (!rotated.refreshToken) rotated.refreshToken = tokens.refreshToken
      io.store(baseUrl, rotated)
      return rotated.accessToken
    } catch (error) {
      if (!unchanged()) return io.load(baseUrl)?.accessToken ?? null

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

  return function ensureNativeAccessToken(baseUrl: string): Promise<string | null> {
    const pending = inFlight.get(baseUrl)
    if (pending) return pending

    const request = resolve(baseUrl).finally(() => {
      if (inFlight.get(baseUrl) === request) inFlight.delete(baseUrl)
    })
    inFlight.set(baseUrl, request)
    return request
  }
}
