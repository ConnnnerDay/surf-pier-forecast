const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const ACCESS_KEY = 'ff.access_token'
const REFRESH_KEY = 'ff.refresh_token'

export const tokenStorage = {
  get access() {
    return localStorage.getItem(ACCESS_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set(access: string, refresh: string) {
    localStorage.setItem(ACCESS_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'DELETE' | 'PATCH'
  body?: unknown
  auth?: boolean
}

async function rawRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (options.auth && tokenStorage.access) {
    headers.Authorization = `Bearer ${tokenStorage.access}`
  }
  const res = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(res.status, detail.detail ?? res.statusText)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** Wraps rawRequest with a single silent refresh-and-retry on a 401, so a
 * long-lived session (see docs/V2_PLAN.md) doesn't force a re-login just
 * because the short-lived access token expired. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  try {
    return await rawRequest<T>(path, options)
  } catch (err) {
    if (options.auth && err instanceof ApiError && err.status === 401 && tokenStorage.refresh) {
      const refreshed = await rawRequest<{ access_token: string; refresh_token: string }>(
        '/auth/refresh',
        { method: 'POST', body: { refresh_token: tokenStorage.refresh } },
      ).catch(() => null)
      if (refreshed) {
        tokenStorage.set(refreshed.access_token, refreshed.refresh_token)
        return rawRequest<T>(path, options)
      }
      tokenStorage.clear()
    }
    throw err
  }
}
