/**
 * HTTP client.
 *
 * Tokens live in `sessionStorage` (cleared when the tab closes) rather than
 * `localStorage`: a platform handling personal data must not leave a persistent
 * session behind on a shared machine. Refresh is automatic and transparent
 * on a 401.
 */
import type { TokenPair } from '@/types'

const BASE = import.meta.env.VITE_API_URL ?? '/api/v1'

const ACCESS_KEY = 'reconcore.access'
const REFRESH_KEY = 'reconcore.refresh'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message)
  }
}

export const tokens = {
  access: () => sessionStorage.getItem(ACCESS_KEY),
  refresh: () => sessionStorage.getItem(REFRESH_KEY),
  set(pair: TokenPair) {
    sessionStorage.setItem(ACCESS_KEY, pair.access_token)
    sessionStorage.setItem(REFRESH_KEY, pair.refresh_token)
  },
  clear() {
    sessionStorage.removeItem(ACCESS_KEY)
    sessionStorage.removeItem(REFRESH_KEY)
  },
}

let refreshing: Promise<boolean> | null = null

async function tryRefresh(): Promise<boolean> {
  const token = tokens.refresh()
  if (!token) return false
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
      .then(async (response) => {
        if (!response.ok) {
          tokens.clear()
          return false
        }
        tokens.set((await response.json()) as TokenPair)
        return true
      })
      .catch(() => false)
      .finally(() => {
        refreshing = null
      })
  }
  return refreshing
}

async function parseError(response: Response): Promise<ApiError> {
  let detail: unknown = null
  try {
    const body = await response.json()
    detail = body?.detail ?? body
  } catch {
    detail = await response.text().catch(() => '')
  }
  const message =
    typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join(', ')
        : typeof detail === 'object' && detail !== null && 'message' in detail
          ? String((detail as { message: unknown }).message)
          : `Erreur HTTP ${response.status}`
  return new ApiError(response.status, message, detail)
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  raw?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, raw, ...init } = options

  const send = async (): Promise<Response> => {
    const headers = new Headers(init.headers)
    if (body !== undefined && !(body instanceof FormData)) {
      headers.set('Content-Type', 'application/json')
    }
    const access = tokens.access()
    if (access) headers.set('Authorization', `Bearer ${access}`)
    return fetch(`${BASE}${path}`, {
      ...init,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  let response = await send()
  if (response.status === 401 && tokens.refresh()) {
    if (await tryRefresh()) response = await send()
  }

  if (!response.ok) throw await parseError(response)
  if (raw) return response as unknown as T
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),

  /** Download an export, honouring the Content-Disposition header. */
  async download(path: string, fallbackName: string): Promise<void> {
    const response = await request<Response>(path, { raw: true })
    const blob = await response.blob()
    const disposition = response.headers.get('content-disposition') ?? ''
    const match = disposition.match(/filename="?([^";]+)"?/)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = match?.[1] ?? fallbackName
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  },
}

export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const serialized = search.toString()
  return serialized ? `?${serialized}` : ''
}
