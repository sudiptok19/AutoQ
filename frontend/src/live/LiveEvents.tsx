import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { BellRing, X } from 'lucide-react'
import { API_MODE, api } from '../api/client'
import { useAuth } from '../auth/auth'
import { cx, toneVar } from '../components/ui'
import { connect, describe, eventsUrl, type Notice, type SocketStatus } from './events'

interface Ctx { status: SocketStatus | 'off'; muted: boolean; toggleMute: () => void }
const LiveCtx = createContext<Ctx>({ status: 'off', muted: false, toggleMute: () => {} })
export const useLive = () => useContext(LiveCtx)

type Toast = Notice & { id: string; count: number; at: number }
const MAX_TOASTS = 4

/** Two short tones. Browsers only allow audio after the user has interacted with the page; if blocked, stay silent. */
function siren() {
  try {
    const ac = new AudioContext()
    ;[0, 0.28].forEach((t, i) => {
      const o = ac.createOscillator(), g = ac.createGain()
      o.frequency.value = i ? 660 : 880; o.type = 'square'
      g.gain.setValueAtTime(0.0001, ac.currentTime + t); g.gain.exponentialRampToValueAtTime(0.08, ac.currentTime + t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + t + 0.24)
      o.connect(g).connect(ac.destination); o.start(ac.currentTime + t); o.stop(ac.currentTime + t + 0.26)
    })
    setTimeout(() => ac.close(), 800)
  } catch { /* no audio */ }
}

export function LiveEventsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth(); const qc = useQueryClient()
  const [status, setStatus] = useState<SocketStatus | 'off'>('off')
  const [toasts, setToasts] = useState<Toast[]>([])
  const [muted, setMuted] = useState(() => { try { return localStorage.getItem('autoq-muted') === '1' } catch { return false } })
  const mutedRef = useRef(muted); mutedRef.current = muted
  const refetch = useRef<ReturnType<typeof setTimeout>>(undefined)

  const toggleMute = useCallback(() => setMuted(m => { try { localStorage.setItem('autoq-muted', m ? '0' : '1') } catch { /* ignore */ } return !m }), [])
  const dismiss = (id: string) => setToasts(ts => ts.filter(t => t.id !== id))

  useEffect(() => {
    if (API_MODE !== 'live' || !user) { setStatus('off'); return }
    // Bursts (a recall holds dozens of packages) collapse into one refetch of whatever is on screen.
    const invalidate = () => { clearTimeout(refetch.current); refetch.current = setTimeout(() => qc.invalidateQueries(), 250) }
    const stop = connect({
      ticket: async () => (await api.post<{ ticket: string }>('/ws/tickets')).ticket,
      url: eventsUrl,
      onStatus: setStatus,
      onSync: invalidate,
      onEvent: e => {
        invalidate()
        const n = describe(e)
        if (!n) return
        if (n.siren && !mutedRef.current) siren()
        setToasts(ts => {
          const same = ts.find(t => t.title === n.title && t.body === n.body)
          if (same) return ts.map(t => (t === same ? { ...t, count: t.count + 1, at: Date.now() } : t))
          return [{ ...n, id: e.event_id, count: 1, at: Date.now() }, ...ts].slice(0, MAX_TOASTS)
        })
      },
    })
    return () => { stop(); clearTimeout(refetch.current); setStatus('off') }
  }, [user?.username])

  // Auto-dismiss: 10 s, or 30 s for anything that sounded the siren.
  useEffect(() => {
    if (!toasts.length) return
    const h = setInterval(() => setToasts(ts => ts.filter(t => Date.now() - t.at < (t.siren ? 30_000 : 10_000))), 1000)
    return () => clearInterval(h)
  }, [toasts.length])

  return (
    <LiveCtx.Provider value={{ status, muted, toggleMute }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map(t => (
          <div key={t.id} role={t.siren ? 'alert' : 'status'} className="rise pointer-events-auto overflow-hidden rounded-xl border bg-surface shadow-2xl"
            style={{ borderColor: `color-mix(in srgb, ${toneVar[t.tone]} 45%, transparent)` }}>
            <div className="flex items-start gap-3 p-3">
              <span className="mt-0.5" style={{ color: toneVar[t.tone] }}><BellRing size={16} /></span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[13px] font-semibold">{t.title}{t.count > 1 && <span className="num rounded bg-surface-2 px-1.5 text-[10.5px] text-muted">×{t.count}</span>}</div>
                {t.body && <div className="mt-0.5 truncate font-mono text-[11.5px] text-muted" title={t.body}>{t.body}</div>}
                <Link to={t.href} onClick={() => dismiss(t.id)} className="mt-1.5 inline-block text-[12px] text-accent hover:underline">Open →</Link>
              </div>
              <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink"><X size={14} /></button>
            </div>
          </div>
        ))}
      </div>
    </LiveCtx.Provider>
  )
}

/** Header indicator: whether this screen is receiving plant events right now. */
export function LiveBadge() {
  const { status } = useLive()
  if (status === 'off') return null
  const live = status === 'live'
  return (
    <span title={live ? 'Receiving plant events in real time.' : 'Event stream disconnected; reconnecting. Screens keep refreshing every few seconds, and anything missed is refetched on reconnect.'}
      className={cx('inline-flex items-center gap-1.5 rounded border px-1.5 py-px font-mono text-[10px] tracking-wider', live ? 'border-pass/40 text-pass' : 'border-hold/40 text-hold')}>
      <span className={cx('size-1.5 rounded-full', live ? 'bg-pass' : 'animate-pulse bg-hold')} />{live ? 'LIVE' : 'RECONNECTING'}
    </span>
  )
}
