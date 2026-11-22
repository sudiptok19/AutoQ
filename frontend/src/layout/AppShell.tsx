import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Activity, Boxes, ClipboardList, Gauge, Layers, LogOut, Menu, Moon, Network, PackageCheck, RotateCcw, ScanLine, Search, ShieldAlert, Sun, Truck, X, ChevronsUpDown } from 'lucide-react'
import { useAuth, ROLE_LABEL, DEV_USERS } from '../auth/auth'
import { API_MODE, api } from '../api/client'
import { useGet } from '../api/hooks'
import type { DashboardSummary } from '../api/types'
import { Honesty, cx } from '../components/ui'

const NAV: { group: string; items: { to: string; label: string; icon: ReactNode; badge?: (s?: DashboardSummary) => number | undefined }[] }[] = [
  { group: 'Plant', items: [{ to: '/', label: 'Overview', icon: <Gauge size={16} /> }] },
  { group: 'Flow', items: [
    { to: '/lots', label: 'Material & lots', icon: <Layers size={16} /> },
    { to: '/inspect', label: 'Inspection', icon: <ScanLine size={16} /> },
    { to: '/containment', label: '4M containment', icon: <ShieldAlert size={16} />, badge: s => s?.active_containments || undefined },
    { to: '/packing', label: 'Packing & seals', icon: <PackageCheck size={16} />, badge: s => s?.held_packages || undefined },
    { to: '/dispatch', label: 'Dispatch gate', icon: <Truck size={16} /> },
    { to: '/trace', label: 'Trace & recall', icon: <Network size={16} /> },
  ] },
  { group: 'Insight', items: [
    { to: '/spc', label: 'Drift monitor', icon: <Activity size={16} />, badge: s => s?.open_alerts || undefined },
    { to: '/audit', label: 'Audit chain', icon: <ClipboardList size={16} /> },
  ] },
]

function useTheme() {
  const [t, setT] = useState<'light' | 'dark' | null>(() => { try { return (localStorage.getItem('autoq-theme') as any) || null } catch { return null } })
  useEffect(() => {
    if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme
    try { t ? localStorage.setItem('autoq-theme', t) : localStorage.removeItem('autoq-theme') } catch { /* ignore */ }
  }, [t])
  const dark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches
  return { dark, toggle: () => setT(dark ? 'light' : 'dark') }
}

export function Brand({ compact }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" fill="var(--ink)" />
        <path d="M7 22 L13 10 L19 22 M9.5 17 H16.5" stroke="var(--accent)" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="23.5" cy="16" r="3.2" stroke="var(--accent)" strokeWidth="2.4" fill="none" />
        <path d="M25.8 18.3 L27.5 20" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" />
      </svg>
      {!compact && <div className="leading-tight"><div className="text-[14px] font-semibold tracking-tight">AutoQ<span className="text-accent">·</span>Trace</div><div className="font-mono text-[9.5px] tracking-[.14em] text-muted">QUALITY · TRACE · DISPATCH</div></div>}
    </div>
  )
}

function SearchBox() {
  const nav = useNavigate(); const [q, setQ] = useState(''); const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { const h = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key === 'k') { e.preventDefault(); ref.current?.focus() } }; window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h) }, [])
  return (
    <form onSubmit={e => { e.preventDefault(); if (q.trim()) nav(`/trace?q=${encodeURIComponent(q.trim())}`) }} className="relative hidden w-full max-w-sm md:block">
      <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
      <input ref={ref} value={q} onChange={e => setQ(e.target.value)} placeholder="Trace a heat, lot, serial or package…"
        className="h-8 w-full rounded-lg border border-line bg-surface-2 pl-8 pr-12 text-[13px] placeholder:text-muted focus:border-accent focus:outline-none" />
      <kbd className="absolute right-2 top-1/2 -translate-y-1/2 rounded border border-line px-1 font-mono text-[10px] text-muted">⌘K</kbd>
    </form>
  )
}

function UserMenu() {
  const { user, logout, login } = useAuth(); const [open, setOpen] = useState(false); const qc = useQueryClient()
  if (!user) return null
  return (
    <div className="relative">
      <button onClick={() => setOpen(o => !o)} className="flex items-center gap-2 rounded-lg border border-line px-2 py-1 hover:bg-surface-2">
        <span className="grid size-6 place-items-center rounded-md bg-accent-soft font-mono text-[11px] font-semibold text-accent">{user.display_name.split(' ').map(s => s[0]).join('').slice(0, 2)}</span>
        <span className="hidden text-left leading-tight sm:block"><span className="block text-[12.5px] font-medium">{user.display_name}</span><span className="block font-mono text-[10px] text-muted">{ROLE_LABEL[user.roles[0]]}</span></span>
        <ChevronsUpDown size={13} className="text-muted" />
      </button>
      {open && (
        <div className="rise absolute right-0 z-40 mt-2 w-72 overflow-hidden rounded-xl border border-line bg-surface shadow-2xl" onMouseLeave={() => setOpen(false)}>
          <div className="border-b border-line px-3 py-2"><div className="eyebrow">Switch role · dev only</div></div>
          {DEV_USERS.map(d => (
            <button key={d.username} onClick={async () => { await login(d.username, 'autoq-dev'); qc.invalidateQueries(); setOpen(false) }}
              className={cx('flex w-full items-center justify-between px-3 py-2 text-left hover:bg-surface-2', d.username === user.username && 'bg-surface-2')}>
              <span><span className="block text-[13px]">{d.name}</span><span className="block text-[11px] text-muted">{ROLE_LABEL[d.role]} · {d.does}</span></span>
              {d.username === user.username && <span className="size-1.5 rounded-full bg-accent" />}
            </button>
          ))}
          <button onClick={logout} className="flex w-full items-center gap-2 border-t border-line px-3 py-2.5 text-[13px] text-ink-2 hover:bg-surface-2"><LogOut size={14} /> Sign out</button>
        </div>
      )}
    </div>
  )
}

export function AppShell() {
  const { dark, toggle } = useTheme()
  const { can } = useAuth()
  const [mobileNav, setMobileNav] = useState(false)
  const loc = useLocation(); const qc = useQueryClient()
  useEffect(() => setMobileNav(false), [loc.pathname])
  const { data: summary } = useGet<DashboardSummary>('/dashboard/summary', { refetchInterval: 8000 })
  const reset = async () => { if (confirm('Reset all demo data to the seed scenario?')) { await api.post('/demo/reset'); qc.invalidateQueries() } }

  const nav = (
    <nav className="flex flex-col gap-5 px-3 py-4">
      {NAV.map(g => (
        <div key={g.group}>
          <div className="eyebrow mb-1.5 px-2">{g.group}</div>
          <div className="flex flex-col gap-0.5">
            {g.items.map(i => {
              const b = i.badge?.(summary)
              return (
                <NavLink key={i.to} to={i.to} end={i.to === '/'}
                  className={({ isActive }) => cx('group relative flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-[13px] transition', isActive ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:bg-surface-2/60 hover:text-ink')}>
                  {({ isActive }) => <>
                    {isActive && <span className="absolute -left-3 top-1.5 bottom-1.5 w-0.5 rounded-r bg-accent" />}
                    <span className={cx(isActive ? 'text-accent' : 'text-muted group-hover:text-ink-2')}>{i.icon}</span>
                    <span className="flex-1">{i.label}</span>
                    {b ? <span className="num rounded-md bg-hold/15 px-1.5 text-[10.5px] text-hold">{b}</span> : null}
                  </>}
                </NavLink>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )

  return (
    <div className="flex h-full">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-14 items-center border-b border-line px-4"><Brand /></div>
        <div className="flex-1 overflow-y-auto">{nav}</div>
        <div className="space-y-2 border-t border-line p-3">
          <NavLink to="/scanner" className="flex items-center gap-2 rounded-lg border border-dashed border-line-strong px-2.5 py-2 text-[12.5px] text-ink-2 hover:border-accent hover:text-accent"><Boxes size={14} /> Open scanner (phone)</NavLink>
          {API_MODE === 'mock' && can('admin', 'quality_manager') && <button onClick={reset} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-[12px] text-muted hover:bg-surface-2 hover:text-ink"><RotateCcw size={13} /> Reset demo data</button>}
        </div>
      </aside>
      {mobileNav && (
        <div className="fixed inset-0 z-50 bg-black/50 lg:hidden" onClick={() => setMobileNav(false)}>
          <aside className="rise h-full w-64 border-r border-line bg-surface" onClick={e => e.stopPropagation()}>
            <div className="flex h-14 items-center justify-between border-b border-line px-4"><Brand /><button onClick={() => setMobileNav(false)}><X size={18} /></button></div>
            {nav}
          </aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur lg:px-6">
          <button className="lg:hidden" onClick={() => setMobileNav(true)} aria-label="Menu"><Menu size={18} /></button>
          <div className="lg:hidden"><Brand compact /></div>
          <SearchBox />
          <div className="ml-auto flex items-center gap-2">
            {API_MODE === 'mock' && <Honesty kind="mock" />}
            <button onClick={toggle} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Toggle theme">{dark ? <Sun size={15} /> : <Moon size={15} />}</button>
            <UserMenu />
          </div>
        </header>
        <main className="grid-bg flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] px-4 py-6 lg:px-8 lg:py-8"><Outlet /></div>
        </main>
      </div>
    </div>
  )
}
