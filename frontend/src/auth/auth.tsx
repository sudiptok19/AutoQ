import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken } from '../api/client'
import { cacheUser, cachedUser, restoreSession } from './session'
import type { Role, User } from '../api/types'

interface AuthCtx { user: User | null; loading: boolean; unverified: string | null; login: (u: string, p: string) => Promise<void>; logout: () => void; can: (...r: Role[]) => boolean }
const Ctx = createContext<AuthCtx>(null as any)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(!!getToken())
  const [unverified, setUnverified] = useState<string | null>(null)
  useEffect(() => {
    const verify = async () => {
      if (!getToken()) return
      const r = await restoreSession(() => api.get<User>('/auth/me'), cachedUser())
      if (r.kind === 'signed_out') { setToken(null); cacheUser(null); setUser(null) }
      else { setUser(r.user); if (r.kind === 'verified') cacheUser(r.user); setUnverified(r.kind === 'unverified' ? r.reason : null) }
    }
    verify().finally(() => setLoading(false))
    const out = () => { cacheUser(null); setUser(null); setUnverified(null) }
    const back = () => { if (getToken()) verify() }   // re-verify when signal returns
    // The live event socket asks for this when the server ends it over authorization (deactivated, roles, session end).
    window.addEventListener('autoq:logout', out); window.addEventListener('online', back); window.addEventListener('autoq:reverify', back)
    return () => { window.removeEventListener('autoq:logout', out); window.removeEventListener('online', back); window.removeEventListener('autoq:reverify', back) }
  }, [])
  const login = async (username: string, password: string) => {
    const r = await api.post<{ access_token: string; user: User }>('/auth/login', { username, password })
    setToken(r.access_token); cacheUser(r.user); setUser(r.user); setUnverified(null)
  }
  const logout = () => { setToken(null); cacheUser(null); setUser(null); setUnverified(null) }
  const can = (...roles: Role[]) => !!user && roles.some(r => user.roles.includes(r))
  return <Ctx.Provider value={{ user, loading, unverified, login, logout, can }}>{children}</Ctx.Provider>
}
export const useAuth = () => useContext(Ctx)

export const ROLE_LABEL: Record<Role, string> = { admin: 'Admin', operator: 'Operator', quality_engineer: 'Quality Engineer', quality_manager: 'Quality Manager', dispatcher: 'Dispatcher' }
export const DEV_USERS = [
  { username: 'op.ravi', name: 'Ravi Kumar', role: 'operator' as Role, does: 'Inspect · pack · seal' },
  { username: 'qe.ananya', name: 'Ananya Sen', role: 'quality_engineer' as Role, does: 'Review holds · log 4M' },
  { username: 'qm.farhan', name: 'Farhan Ali', role: 'quality_manager' as Role, does: 'Clear containment · recall' },
  { username: 'disp.kiran', name: 'Kiran Das', role: 'dispatcher' as Role, does: 'Seal recheck · load · ship' },
  { username: 'admin', name: 'Admin', role: 'admin' as Role, does: 'Master data · policies' },
]
