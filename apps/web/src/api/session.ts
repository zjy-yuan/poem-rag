let accessToken: string | null = null
let refreshHandler: (() => Promise<string | null>) | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function setRefreshHandler(handler: () => Promise<string | null>): void {
  refreshHandler = handler
}

export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshHandler) {
    return null
  }
  return refreshHandler()
}

