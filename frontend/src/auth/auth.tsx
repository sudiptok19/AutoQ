import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken } from '../api/client'
import type { Role, User } from '../api/types'

interface AuthCtx { user: User | null; loading: boolean; login: (u: string, p: string) => Promise<void>; logout: () => void; can: (...r: Role[]) => boolean }
const Ctx = createContext<AuthCtx>(null as any)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(!!getToken())
  useEffect(() => {
    if (getToken()) api.get<User>('/auth/me').then(setUser).catch(() => setToken(null)).finally(() => setLoading(false))
    const out = () => setUser(null)
    window.addEventListener('autoq:logout', out); return () => window.removeEventListener('autoq:logout', out)
  }, [])
  const login = async (username: string, password: string) => {
    const r = await api.post<{ access_token: string; user: User }>('/auth/login', { username, password })
    setToken(r.access_token); setUser(r.user)
  }
  const logout = () => { setToken(null); setUser(null) }
  const can = (...roles: Role[]) => !!user && roles.some(r => user.roles.includes(r))
  return <Ctx.Provider value={{ user, loading, login, logout, can }}>{children}</Ctx.Provider>
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
