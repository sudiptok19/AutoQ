import type { User } from '../api/types'

/** Cached identity so a PWA reopened without signal keeps its operator. Only the server can END a session:
 *  a 401/403 from /auth/me clears it; a network/gateway failure keeps the cached user, flagged as unverified. */
const KEY = 'autoq-user'

export function cacheUser(u: User | null) {
  try { u ? localStorage.setItem(KEY, JSON.stringify(u)) : localStorage.removeItem(KEY) } catch { /* private mode */ }
}
export function cachedUser(): User | null {
  try { const raw = localStorage.getItem(KEY); return raw ? (JSON.parse(raw) as User) : null } catch { return null }
}

export type Restore =
  | { kind: 'verified'; user: User }
  | { kind: 'unverified'; user: User; reason: string }   // keep token; server unreachable
  | { kind: 'signed_out'; reason: string }               // clear token

export async function restoreSession(fetchMe: () => Promise<User>, cached: User | null): Promise<Restore> {
  try {
    return { kind: 'verified', user: await fetchMe() }
  } catch (e: any) {
    const status = typeof e?.status === 'number' ? e.status : 0
    if (status === 401 || status === 403) return { kind: 'signed_out', reason: e?.code ?? 'UNAUTHENTICATED' }
    if (cached) return { kind: 'unverified', user: cached, reason: e?.code ?? 'NETWORK_UNAVAILABLE' }
    return { kind: 'signed_out', reason: 'NO_CACHED_IDENTITY' }
  }
}
