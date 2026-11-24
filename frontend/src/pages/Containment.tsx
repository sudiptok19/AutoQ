import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Cog, HardHat, Package2, ScrollText, ShieldAlert, ShieldCheck, Plus } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Containment, FourMCategory, FourMEvent, Line, Unit } from '../api/types'
import { Button, Card, Dot, Empty, ErrorBox, Field, Modal, Mono, PageHeader, Pill, Segmented, Select, Status, Textarea, ago, cx, fmtDateTime } from '../components/ui'

const CAT: Record<FourMCategory, { icon: React.ReactNode; label: string; example: string }> = {
  MAN: { icon: <HardHat size={16} />, label: 'Man', example: 'Relief operator on CNC-2 without bracket certification' },
  MACHINE: { icon: <Cog size={16} />, label: 'Machine', example: 'Boring tool insert replaced after chipping' },
  MATERIAL: { icon: <Package2 size={16} />, label: 'Material', example: 'Switched to backup heat from same supplier' },
  METHOD: { icon: <ScrollText size={16} />, label: 'Method', example: 'Coolant concentration changed per new WI-17 rev B' },
}

function LogEventModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: lines } = useGet<{ items: Line[] }>('/lines')
  const { data: pol } = useGet<any>('/policies')
  const [cat, setCat] = useState<FourMCategory>('MACHINE')
  const [planned, setPlanned] = useState<'unplanned' | 'planned'>('unplanned')
  const [picked, setLine] = useState<string | null>(null)
  const line = picked ?? lines?.items[0]?.id ?? ''
  const [desc, setDesc] = useState(CAT.MACHINE.example)
  const [reason, setReason] = useState('Insert chipped mid-shift; replaced by maintenance without first-off approval')
  const act = useAct(() => api.post('/four-m-events', { category: cat, planned: planned === 'planned', line_id: line, description: desc, reason }))
  const c = pol?.containment
  return (
    <Modal open={open} onClose={onClose} title="Log 4M change point" width={600}>
      <div className="space-y-4">
        <div className="grid grid-cols-4 gap-2">
          {(Object.keys(CAT) as FourMCategory[]).map(k => (
            <button key={k} onClick={() => { setCat(k); setDesc(CAT[k].example) }} className={cx('flex flex-col items-center gap-1.5 rounded-lg border py-3 text-[12.5px] transition', cat === k ? 'border-accent bg-accent-soft text-accent' : 'border-line text-ink-2 hover:border-line-strong')}>
              {CAT[k].icon}{CAT[k].label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Segmented value={planned} onChange={setPlanned} options={[{ value: 'unplanned', label: 'Unplanned' }, { value: 'planned', label: 'Planned' }]} />
          <Select value={line} onChange={e => setLine(e.target.value)} className="w-40">{lines?.items.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</Select>
        </div>
        <Field label="What changed"><Textarea value={desc} onChange={e => setDesc(e.target.value)} /></Field>
        <Field label="Reason"><Textarea value={reason} onChange={e => setReason(e.target.value)} className="min-h-14" /></Field>
        {c && <div className="rounded-lg border border-line bg-surface-2 p-3 text-[12px]">
          <div className="eyebrow mb-1.5">Policy that will apply (configurable, provisional defaults)</div>
          <ul className="space-y-1 text-ink-2">
            <li>• Hold unshipped units on this line created in the last <b>{c.lookback_minutes} min</b>, plus all new units until cleared</li>
            <li>• Full inspection of the next <b>{c.first_n}</b> pieces</li>
            {(planned === 'planned' ? c.recheck_existing_planned : c.recheck_existing_unplanned) && <li>• Re-inspect every held unit that had already passed</li>}
            <li>• Anomaly thresholds tightened by <b>{Math.round(c.threshold_tightening * 100)}%</b> on this line</li>
            <li>• Only a Quality Manager can clear, and only after all obligations are met</li>
          </ul>
        </div>}
        {act.error && <ErrorBox error={act.error} />}
        <div className="flex justify-end gap-2"><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={act.isPending} onClick={() => act.mutate(undefined, { onSuccess: onClose })}><ShieldAlert size={15} /> Log & contain</Button></div>
      </div>
    </Modal>
  )
}

function ContainmentCard({ c, ev, units, lines }: { c: Containment; ev?: FourMEvent; units: Unit[]; lines: Line[] }) {
  const { can } = useAuth()
  const [reason, setReason] = useState('')
  const clear = useAct(() => api.post(`/containments/${c.id}/clear`, { reason }))
  const affected = units.filter(u => c.affected_unit_ids.includes(u.id))
  const recheck = c.obligations.find(o => o.type === 'RECHECK_UNIT')
  const allMet = c.obligations.every(o => o.completed >= o.required)
  const active = c.status === 'ACTIVE'
  return (
    <Card className={cx(active && 'border-hold/40')}>
      <div className="flex flex-wrap items-start gap-3 border-b border-line px-4 py-3">
        <span className={cx('grid size-9 place-items-center rounded-lg', active ? 'bg-hold/12 text-hold' : 'bg-pass/12 text-pass')}>{ev ? CAT[ev.category].icon : <ShieldAlert size={16} />}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><Mono className="font-semibold">{ev?.code}</Mono><Pill tone={active ? 'hold' : 'pass'}>{active ? <><Dot tone="hold" pulse /> CONTAINMENT ACTIVE</> : 'CLEARED'}</Pill><Pill>{ev?.planned ? 'PLANNED' : 'UNPLANNED'} · {ev?.category}</Pill><span className="text-[11.5px] text-muted">{lines.find(l => l.id === c.line_id)?.name}</span></div>
          <div className="mt-1 text-[13px]">{ev?.description}</div>
          <div className="mt-0.5 text-[11.5px] text-muted">“{ev?.reason}” — {ev?.created_by}, {fmtDateTime(ev?.created_at)}</div>
        </div>
      </div>
      <div className="grid gap-px bg-line md:grid-cols-3">
        <div className="bg-surface p-4">
          <div className="eyebrow mb-2">Obligations</div>
          {c.obligations.map(o => (
            <div key={o.type} className="mb-3">
              <div className="flex items-center justify-between text-[12.5px]"><span>{o.type === 'FULL_INSPECT_FIRST_N' ? `First ${o.required} pieces, full inspection` : 'Re-inspect previously passed units'}</span><span className="num">{o.completed}/{o.required}</span></div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full transition-all" style={{ width: `${(o.completed / Math.max(1, o.required)) * 100}%`, background: o.completed >= o.required ? 'var(--pass)' : 'var(--hold)' }} /></div>
            </div>
          ))}
          {active && <Link to="/inspect" className="text-[12px] text-accent hover:underline">Go to inspection station →</Link>}
        </div>
        <div className="bg-surface p-4">
          <div className="eyebrow mb-2">Held inventory · {affected.length} units</div>
          <div className="flex max-h-40 flex-wrap gap-1 overflow-y-auto">
            {affected.map(u => {
              const done = recheck?.unit_ids?.includes(u.id) ? false : null
              return <Link key={u.id} to={`/inspect?unit=${u.id}`} title={u.quality_status}><Pill tone={u.quality_status === 'UNINSPECTED' ? 'neutral' : u.quality_status === 'PASS' ? (done === false ? 'hold' : 'pass') : 'reject'}>{u.serial.slice(-4)}</Pill></Link>
            })}
          </div>
          <div className="mt-2 text-[11px] text-muted">Window: units created ≥ {c.rule.lookback_minutes} min before the event, not yet shipped. Shipped stock is unaffected — use recall for that.</div>
        </div>
        <div className="bg-surface p-4">
          <div className="eyebrow mb-2">Controlled resumption</div>
          {active ? <>
            <Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Clearance rationale (QM)…" className="min-h-14 text-[12.5px]" />
            {clear.error && <div className="mt-2"><ErrorBox error={clear.error} /></div>}
            <Button className="mt-2 w-full" variant={allMet ? 'primary' : 'secondary'} disabled={!can('quality_manager') || !reason.trim()} loading={clear.isPending} onClick={() => clear.mutate(undefined)}><ShieldCheck size={14} /> Clear containment</Button>
            <div className="mt-1.5 text-[11px] text-muted">{!can('quality_manager') ? 'Requires Quality Manager.' : allMet ? 'All obligations met.' : 'Server will refuse until obligations are met.'}</div>
          </> : <div className="text-[12.5px]"><Status value="CLEARED" /> <div className="mt-1.5 text-muted">by {c.cleared_by} · {ago(c.cleared_at)}<br />“{c.clear_reason}”</div></div>}
        </div>
      </div>
    </Card>
  )
}

export default function ContainmentPage() {
  const { can } = useAuth()
  const [open, setOpen] = useState(false)
  const { data: conts } = useGet<{ items: Containment[] }>('/containments', { refetchInterval: 6000 })
  const { data: evs } = useGet<{ items: FourMEvent[] }>('/four-m-events')
  const { data: units } = useGet<{ items: Unit[] }>('/units')
  const { data: lines } = useGet<{ items: Line[] }>('/lines')
  return (
    <div>
      <PageHeader eyebrow="Man · Machine · Material · Method" title="4M change-point containment"
        description="A logged change point automatically holds recent unshipped inventory, adds inspection obligations, and blocks dispatch until a Quality Manager clears it."
        actions={<Button variant="primary" disabled={!can('quality_engineer', 'quality_manager')} onClick={() => setOpen(true)}><Plus size={15} /> Log 4M event</Button>} />
      {!can('quality_engineer', 'quality_manager') && <div className="mb-4 text-[12.5px] text-muted">Logging 4M events requires a Quality Engineer or Manager — switch role from the top-right menu.</div>}
      <div className="space-y-4">
        {!conts?.items.length && <Card><Empty icon={<ShieldAlert size={22} />} title="No change points logged">Try: an unplanned <b>Machine</b> change on Line 2 — then scan PKG-0005 at the dispatch gate.</Empty></Card>}
        {conts?.items.map(c => <ContainmentCard key={c.id} c={c} ev={evs?.items.find(e => e.id === c.event_id)} units={units?.items ?? []} lines={lines?.items ?? []} />)}
      </div>
      <LogEventModal open={open} onClose={() => setOpen(false)} />
    </div>
  )
}

