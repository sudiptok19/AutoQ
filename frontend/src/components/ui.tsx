import clsx from 'clsx'
import { type ButtonHTMLAttributes, type ReactNode, type InputHTMLAttributes, type SelectHTMLAttributes, useEffect } from 'react'
import { X, Loader2, FlaskConical, Info } from 'lucide-react'

export const cx = clsx

/* ── Tone system: status colours carry meaning, nothing else uses them ── */
export type Tone = 'pass' | 'hold' | 'reject' | 'prov' | 'info' | 'accent' | 'neutral'
const toneText: Record<Tone, string> = {
  pass: 'text-pass', hold: 'text-hold', reject: 'text-reject', prov: 'text-prov', info: 'text-info', accent: 'text-accent', neutral: 'text-ink-2',
}
export const toneVar: Record<Tone, string> = {
  pass: 'var(--pass)', hold: 'var(--hold)', reject: 'var(--reject)', prov: 'var(--prov)', info: 'var(--info)', accent: 'var(--accent)', neutral: 'var(--muted)',
}

const STATUS_TONE: Record<string, Tone> = {
  PASS: 'pass', ACTIVE: 'pass', ACCEPTED: 'pass', INTACT: 'pass', CLEARED: 'pass', SEALED: 'info', DELIVERED: 'pass', APPROVED: 'pass', READY: 'pass', OK: 'pass',
  HOLD: 'hold', HELD: 'hold', CONTAINMENT_ACTIVE: 'hold', SEAL_REVIEW: 'hold', SUSPICIOUS: 'reject', UNVERIFIABLE: 'hold', WARN: 'hold', COLLECTING: 'hold', DRAFT: 'hold',
  REJECT: 'reject', REVOKED: 'reject', BLOCKED: 'reject', FAIL: 'reject', QUARANTINED: 'reject', CONFLICT: 'reject',
  PROVISIONAL_OFFLINE: 'prov', PROVISIONAL_ACCEPT: 'prov',
  UNINSPECTED: 'neutral', OPEN: 'info', LOADED: 'info', IN_TRANSIT: 'info', CLOSED: 'info', SKIP: 'neutral', DUPLICATE: 'neutral',
}
export const toneOf = (s?: string | null): Tone => (s && STATUS_TONE[s]) || 'neutral'

export function Dot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <span className={cx('inline-block size-1.5 shrink-0 rounded-full', pulse && 'pulse-dot')} style={{ background: toneVar[tone], ['--c' as any]: toneVar[tone] }} />
}

export function Pill({ tone = 'neutral', children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx('inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-medium leading-4', toneText[tone], className)}
      style={{ borderColor: `color-mix(in srgb, ${toneVar[tone]} 35%, transparent)`, background: `color-mix(in srgb, ${toneVar[tone]} 10%, transparent)` }}>
      {children}
    </span>
  )
}

export function Status({ value, pulse }: { value?: string | null; pulse?: boolean }) {
  if (!value) return <span className="text-muted">—</span>
  const t = toneOf(value)
  return <Pill tone={t}><Dot tone={t} pulse={pulse} />{value.replaceAll('_', ' ')}</Pill>
}

/** Visible honesty: anything simulated, uncalibrated or provisional gets labelled. */
export function Honesty({ kind, children }: { kind: 'synthetic' | 'uncalibrated' | 'mock' | 'provisional' | 'not-probability'; children?: ReactNode }) {
  const map = {
    synthetic: { label: 'SYNTHETIC', tip: 'Generated or fixture data — not from a production line.' },
    uncalibrated: { label: 'UNCALIBRATED', tip: 'No measured calibration for this method/profile. Fails safe to HOLD.' },
    mock: { label: 'MOCK API', tip: 'Running against the in-browser mock backend that follows docs/CONTRACT.md.' },
    provisional: { label: 'PROVISIONAL', tip: 'Decided on-device without current server state. Not authoritative until synced.' },
    'not-probability': { label: 'RAW SCORE', tip: 'Method-specific anomaly score in [0,1]. Not a probability of defect.' },
  }[kind]
  return (
    <span title={map.tip} className="inline-flex items-center gap-1 rounded border border-dashed border-line-strong px-1.5 py-px font-mono text-[10px] tracking-wider text-muted">
      {kind === 'synthetic' || kind === 'mock' ? <FlaskConical size={10} /> : <Info size={10} />}
      {children ?? map.label}
    </span>
  )
}

type BtnVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export function Button({ variant = 'secondary', size = 'md', loading, className, children, ...p }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; size?: 'sm' | 'md' | 'lg'; loading?: boolean }) {
  return (
    <button {...p} disabled={p.disabled || loading}
      className={cx(
        'inline-flex select-none items-center justify-center gap-2 rounded-lg font-medium transition-[background,border,color,transform] active:scale-[.98] disabled:pointer-events-none disabled:opacity-45',
        size === 'sm' && 'h-7 px-2.5 text-xs', size === 'md' && 'h-9 px-3.5 text-[13px]', size === 'lg' && 'h-12 px-5 text-[15px]',
        variant === 'primary' && 'bg-accent text-accent-ink hover:brightness-110',
        variant === 'secondary' && 'border border-line-strong bg-surface text-ink hover:bg-surface-2',
        variant === 'ghost' && 'text-ink-2 hover:bg-surface-2 hover:text-ink',
        variant === 'danger' && 'bg-reject text-white hover:brightness-110',
        className)}>
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  )
}

export function Card({ className, children, ...p }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return <div {...p} className={cx('rounded-xl border border-line bg-surface', className)}>{children}</div>
}

export function CardHeader({ title, eyebrow, right, className }: { title: ReactNode; eyebrow?: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex items-start justify-between gap-3 border-b border-line px-4 py-3', className)}>
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-0.5">{eyebrow}</div>}
        <div className="truncate text-[13.5px] font-semibold">{title}</div>
      </div>
      {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
    </div>
  )
}

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1.5">{eyebrow}</div>}
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Stat({ label, value, tone = 'neutral', sub, icon }: { label: string; value: ReactNode; tone?: Tone; sub?: ReactNode; icon?: ReactNode }) {
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="flex items-center justify-between"><span className="eyebrow">{label}</span><span className="text-muted">{icon}</span></div>
      <div className={cx('num mt-2 text-[28px] font-medium leading-none tracking-tight', tone !== 'neutral' && toneText[tone])}>{value}</div>
      {sub && <div className="mt-2 text-xs text-muted">{sub}</div>}
      <div className="absolute inset-x-0 bottom-0 h-px" style={{ background: tone === 'neutral' ? 'transparent' : `linear-gradient(90deg, ${toneVar[tone]}, transparent)` }} />
    </Card>
  )
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx('font-mono text-[12px]', className)}>{children}</span>
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      {icon && <div className="mb-1 text-muted">{icon}</div>}
      <div className="font-medium">{title}</div>
      {children && <div className="max-w-sm text-[13px] text-muted">{children}</div>}
    </div>
  )
}

export function Spinner({ label }: { label?: string }) {
  return <div className="flex items-center gap-2 p-6 text-muted"><Loader2 size={16} className="animate-spin" />{label ?? 'Loading…'}</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  const e = error as any
  return (
    <div className="rounded-lg border px-3 py-2.5 text-[13px]" style={{ borderColor: 'color-mix(in srgb, var(--reject) 40%, transparent)', background: 'color-mix(in srgb, var(--reject) 8%, transparent)' }}>
      <div className="font-mono text-[11px] font-semibold text-reject">{e?.code ?? 'ERROR'}</div>
      <div className="mt-0.5 text-ink-2">{e?.message ?? String(error)}</div>
      {e?.details?.unmet && (
        <ul className="mt-1.5 list-inside list-disc text-xs text-muted">
          {e.details.unmet.map((u: any, i: number) => <li key={i}>{u.type}: {u.completed}/{u.required}</li>)}
        </ul>
      )}
      {e?.details?.blockers && (
        <ul className="mt-1.5 space-y-0.5 text-xs text-muted">
          {e.details.blockers.map((b: any, i: number) => <li key={i}><Mono className="text-reject">{b.code}</Mono> {b.message}</li>)}
        </ul>
      )}
    </div>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11.5px] text-muted">{hint}</span>}
    </label>
  )
}
const inputCls = 'h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-[13px] text-ink placeholder:text-muted focus:border-accent focus:outline-none'
export function Input(p: InputHTMLAttributes<HTMLInputElement>) { return <input {...p} className={cx(inputCls, p.className)} /> }
export function Textarea(p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea {...p} className={cx(inputCls, 'h-auto min-h-20 py-2', p.className)} /> }
export function Select({ children, ...p }: SelectHTMLAttributes<HTMLSelectElement>) { return <select {...p} className={cx(inputCls, 'pr-8', p.className)}>{children}</select> }

export function Modal({ open, onClose, title, children, width = 520 }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: number }) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 backdrop-blur-[2px] sm:items-center sm:p-6" onMouseDown={onClose}>
      <div className="rise max-h-[92vh] w-full overflow-auto rounded-t-2xl border border-line bg-surface shadow-2xl sm:rounded-2xl" style={{ maxWidth: width }} onMouseDown={e => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-surface px-5 py-3.5">
          <div className="font-semibold">{title}</div>
          <button onClick={onClose} className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Close"><X size={16} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cx('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-[13px]">
        <thead><tr className="border-b border-line">{head.map((h, i) => <th key={i} className="eyebrow whitespace-nowrap px-4 py-2.5 text-left font-normal">{h}</th>)}</tr></thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-line [&>tr:last-child]:border-0 [&_td]:px-4 [&_td]:py-2.5">{children}</tbody>
      </table>
    </div>
  )
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[] }) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5">
      {options.map(o => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={cx('rounded-md px-2.5 py-1 text-xs font-medium transition', value === o.value ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink')}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export const fmtTime = (iso?: string | null) => iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'
export const fmtDateTime = (iso?: string | null) => iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'
export const ago = (iso?: string | null) => {
  if (!iso) return '—'
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`; if (s < 3600) return `${Math.round(s / 60)}m ago`; if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}
