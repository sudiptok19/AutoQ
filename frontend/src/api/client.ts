import { mockEvidence, mockFetch } from '../mock/router'
import { liveRequest, liveResponse } from './live'
import { renderBracket, type FixtureKind } from '../lib/cv'
import type { ApiError } from './types'

export const API_MODE: 'mock' | 'live' = (import.meta.env.VITE_API_MODE as any) === 'live' ? 'live' : 'mock'
const BASE = '/api/v1'
let token: string | null = (() => { try { return localStorage.getItem('autoq-token') } catch { return null } })()
export const setToken = (t: string | null) => { token = t; try { t ? localStorage.setItem('autoq-token', t) : localStorage.removeItem('autoq-token') } catch { /* ignore */ } }
export const getToken = () => token

export class ApiErr extends Error implements ApiError {
  constructor(public status: number, public code: string, message: string, public details?: any) { super(message) }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const isForm = typeof FormData !== 'undefined' && body instanceof FormData
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json'
  let status: number, json: any
  if (API_MODE === 'mock') ({ status, json } = await mockFetch(method, BASE + path, headers, body))
  else {
    const req = liveRequest(method, path, body)
    let res: Response
    try {
      res = await fetch(BASE + req.path, { method, headers, body: req.body === undefined ? undefined : isForm ? (req.body as FormData) : JSON.stringify(req.body) })
    } catch (e) {  // offline, DNS, TLS or gateway unreachable: not an auth decision
      throw new ApiErr(0, 'NETWORK_UNAVAILABLE', 'Server not reachable', { cause: String(e) })
    }
    status = res.status; json = res.headers.get('content-type')?.includes('json') ? await res.json() : null
    if (status < 400) json = liveResponse(method, path, json)
  }
  if (status >= 400) {
    if (status === 401 && path !== '/auth/login') { setToken(null); window.dispatchEvent(new Event('autoq:logout')) }
    throw new ApiErr(status, json?.error?.code ?? `HTTP_${status}`, json?.error?.message ?? 'Request failed', json?.error?.details)
  }
  return json as T
}

export const api = {
  get: <T>(p: string) => request<T>('GET', p),
  post: <T>(p: string, b?: unknown) => request<T>('POST', p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>('PUT', p, b ?? {}),
  del: <T>(p: string) => request<T>('DELETE', p),
  /** Authenticated binary GET (live mode only), e.g. the backend's SYNTHETIC fixture renders. */
  blob: async (p: string): Promise<Blob> => {
    let res: Response
    try { res = await fetch(BASE + p, { headers: token ? { Authorization: `Bearer ${token}` } : {} }) }
    catch (e) { throw new ApiErr(0, 'NETWORK_UNAVAILABLE', 'Server not reachable', { cause: String(e) }) }
    if (!res.ok) throw new ApiErr(res.status, `HTTP_${res.status}`, 'Request failed')
    return res.blob()
  },
}

/** Resolve an evidence URL to an <img> src. Live mode appends the bearer as a query param (CONTRACT note). */
const fixtureCache = new Map<string, string>()
export function evidenceSrc(url?: string | null): string | null {
  if (!url) return null
  if (url.startsWith('mock-evidence:')) return mockEvidence(url.slice(14)) ?? null
  if (url.startsWith('fixture:')) {
    if (!fixtureCache.has(url)) { const [, kind, seed] = url.split(':'); fixtureCache.set(url, renderBracket(kind as FixtureKind, Number(seed) || 7).toDataURL('image/jpeg', 0.8)) }
    return fixtureCache.get(url)!
  }
  return token ? `${url}${url.includes('?') ? '&' : '?'}access_token=${encodeURIComponent(token)}` : url
}
