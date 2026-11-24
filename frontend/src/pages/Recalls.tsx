import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { CheckCircle2, ClipboardCheck, FileText, PackageX, PhoneCall, ShieldCheck, Siren, Truck, Unlock } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Recall, Report8D, SegregationTask } from '../api/types'
import { Bucket } from '../components/domain'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Mono, PageHeader, Pill, Status, Textarea, ago, cx, fmtDateTime } from '../components/ui'

const TASK: Record<string, { label: string; icon: React.ReactNode; tone: 'reject' | 'hold' | 'info' }> = {
  REMOVAL_REQUIRED: { label: 'Unload & segregate', icon: <PackageX size={14} />, tone: 'reject' },
  IN_TRANSIT_RECALL: { label: 'Intercept in transit', icon: <Truck size={14} />, tone: 'hold' },
  NOTIFY_CUSTOMER: { label: 'Notify customer', icon: <PhoneCall size={14} />, tone: 'info' },
}
const taskMeta = (t: string) => TASK[t] ?? { label: t.replaceAll('_', ' ').toLowerCase(), icon: <ClipboardCheck size={14} />, tone: 'info' as const }

/** Units per physical location: the v0.2 record carries exact counts; older records only the per-bucket lists. */
function counts(r: Recall) {
  if (r.unit_counts) return r.unit_counts
  const sum = (xs: { units: number }[]) => xs.reduce((n, x) => n + x.units, 0)
  const a = r.affected
  return { at_plant: a.units - sum(a.loaded_not_departed) - sum(a.in_transit) - sum(a.delivered), loaded_not_departed: sum(a.loaded_not_departed), in_transit: sum(a.in_transit), delivered: sum(a.delivered) }
}
const isReleased = (r: Recall) => !!r.released_at

/** Human instruction for a task. Some records carry prose; others carry the facts (vehicle, customer, units). */
function instruction(t: SegregationTask): string | null {
  const a = t.consignee_action ?? {}
  if (typeof a.action === 'string' && a.action !== t.type) return a.action
  const n = a.units ? `${a.units} unit${a.units === 1 ? '' : 's'}` : 'the affected units'
  if (t.type === 'IN_TRANSIT_RECALL') return `Contact the carrier${a.vehicle_no ? ` (${a.vehicle_no})` : ''} and ${a.customer ?? 'the consignee'}: hold ${n} on arrival, do not install.`
  if (t.type === 'NOTIFY_CUSTOMER') return `Notify ${a.customer ?? 'the customer'} to quarantine ${n} already delivered.`
  return a.message ?? null
}

function TaskRow({ t }: { t: SegregationTask }) {
  const { can } = useAuth()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const done = useAct(() => api.post(`/segregation-tasks/${t.id}/complete`, { reason }))
  const m = taskMeta(t.type)
  const action = instruction(t)
  return (
    <li className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={t.status === 'OPEN' ? m.tone : 'pass'}><span className="flex items-center gap-1.5">{m.icon}{m.label}</span></Pill>
        {t.package_label && <Mono className="text-[12.5px]">{t.package_label}</Mono>}
        {t.shipment_code && <span className="text-[12px] text-muted">on <Mono>{t.shipment_code}</Mono></span>}
        <span className="ml-auto"><Status value={t.status} /></span>
      </div>
      {action && <div className="mt-1.5 text-[12.5px] text-ink-2">{action}</div>}
      {t.status === 'COMPLETED'
        ? <div className="mt-1.5 flex items-start gap-1.5 text-[12px] text-muted"><CheckCircle2 size={13} className="mt-0.5 shrink-0 text-pass" /><span>Done by {t.completed_by} · {fmtDateTime(t.completed_at)} — “{t.completion_reason}”</span></div>
        : can('dispatcher', 'quality_engineer', 'quality_manager') && (open
          ? <div className="mt-2.5 space-y-2">
              <Textarea value={reason} onChange={e => setReason(e.target.value)} className="min-h-14" placeholder={t.type === 'NOTIFY_CUSTOMER' ? 'e.g. Called Northline QA (R. Mehta), quarantine confirmed at their receiving' : 'e.g. Crate moved to quarantine cage Q2, tagged red'} />
              {done.error && <ErrorBox error={done.error} />}
              <div className="flex gap-2"><Button size="sm" variant="primary" disabled={!reason.trim()} loading={done.isPending} onClick={() => done.mutate(undefined)}>Record as done</Button><Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button></div>
            </div>
          : <Button size="sm" className="mt-2.5" onClick={() => setOpen(true)}>Mark done…</Button>)}
    </li>
  )
}

function ReleasePanel({ r, openTasks }: { r: Recall; openTasks: number }) {
  const { can } = useAuth()
  const [reason, setReason] = useState('')
  const rel = useAct(() => api.post<{ passports_reactivated?: number }>(`/recalls/${r.id}/release`, { reason }))
  if (isReleased(r)) return (
    <Card className="border-pass/40">
      <CardHeader eyebrow="Controlled release" title={<span className="flex items-center gap-2"><ShieldCheck size={15} className="text-pass" /> Released</span>} />
      <div className="p-4 text-[12.5px] text-ink-2">By {r.released_by} · {fmtDateTime(r.released_at)}<div className="mt-1 text-muted">“{r.release_reason}”</div></div>
    </Card>
  )
  return (
    <Card>
      <CardHeader eyebrow="Controlled release" title={<span className="flex items-center gap-2"><Unlock size={15} /> Release quarantine</span>} />
      <div className="space-y-3 p-4">
        <div className="text-[12.5px] text-muted">Lifts the recall holds on every unit in scope and reactivates their passports. Units still blocked by anything else (another recall, 4M containment, a reject) stay blocked.</div>
        {openTasks > 0 && <div className="rounded-lg bg-hold/10 px-3 py-2 text-[12px] text-ink-2"><b>{openTasks} physical task{openTasks > 1 ? 's' : ''} still open.</b> Releasing does not close them — they stay tracked until someone records what was done.</div>}
        <Field label="Release reason (audit)"><Textarea value={reason} onChange={e => setReason(e.target.value)} className="min-h-14" placeholder="e.g. Supplier lab retest of H-7781 passed (report MTC-R2 attached to 8D)" /></Field>
        {rel.error && <ErrorBox error={rel.error} />}
        {rel.data && <div className="rounded-lg bg-pass/10 px-3 py-2 text-[12.5px] text-pass">Released · {rel.data.passports_reactivated ?? 0} passports reactivated</div>}
        <Button variant="primary" className="w-full" disabled={!can('quality_manager') || !reason.trim()} loading={rel.isPending} onClick={() => rel.mutate(undefined)}>Release {r.scope.label}</Button>
        {!can('quality_manager') && <div className="text-[11px] text-muted">Release requires a Quality Manager.</div>}
      </div>
    </Card>
  )
}

function RecallDetail({ r }: { r: Recall }) {
  const { can } = useAuth(); const nav = useNavigate()
  const { data: reports } = useGet<{ items: Report8D[] }>('/reports')
  const draft = useAct(() => api.post<Report8D>('/reports/8d', { source: { type: 'RECALL', id: r.id } }))
  const mine = reports?.items.filter(x => x.source_type === 'RECALL' && x.source_id === r.id) ?? []
  const tasks = r.segregation_tasks ?? []
  const openTasks = tasks.filter(t => t.status === 'OPEN').length
  const c = counts(r)
  return (
    <div className="space-y-5">
      <Card className={cx(!isReleased(r) && 'border-reject/40')}>
        <CardHeader eyebrow={`${r.scope.type} recall`} title={<span className="flex items-center gap-2"><Siren size={15} className={isReleased(r) ? 'text-muted' : 'text-reject'} /> {r.scope.label}</span>}
          right={<Status value={isReleased(r) ? 'RELEASED' : 'QUARANTINED'} pulse={!isReleased(r)} />} />
        <div className="space-y-4 p-4">
          <div className="text-[13px]">“{r.reason}”<div className="mt-0.5 text-[11.5px] text-muted">Executed by {r.executed_by} · {fmtDateTime(r.executed_at)} · <Link className="underline underline-offset-2" to={`/trace?q=${encodeURIComponent(r.scope.label)}`}>open genealogy</Link></div></div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            {isReleased(r) ? <>
              <Bucket label="At plant" value={c.at_plant} tone="neutral" note="were held · hold lifted" />
              <Bucket label="Loaded, not departed" value={c.loaded_not_departed} tone="neutral" note="were blocked at departure" />
              <Bucket label="In transit" value={c.in_transit} tone="neutral" note="were in transit at recall" />
              <Bucket label="Delivered" value={c.delivered} tone="neutral" note="were delivered at recall" />
            </> : <>
              <Bucket label="At plant" value={c.at_plant} tone="reject" note="held — gate refuses" />
              <Bucket label="Loaded, not departed" value={c.loaded_not_departed} tone="reject" note="unload before departure" />
              <Bucket label="In transit" value={c.in_transit} tone="hold" note="intercept — cannot block" />
              <Bucket label="Delivered" value={c.delivered} tone="info" note="customer notification" />
            </>}
          </div>
          <div className="rounded-md bg-surface-2 px-3 py-2 text-[11px] text-muted">Evidence: <Mono>{r.evidence?.genealogy_query}</Mono> as of {fmtDateTime(r.evidence?.as_of)}.</div>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader eyebrow="Physical follow-up" title={`Segregation tasks · ${openTasks} open`} />
          {tasks.length
            ? <ul className="divide-y divide-line">{[...tasks].sort((a, b) => (a.status === b.status ? 0 : a.status === 'OPEN' ? -1 : 1)).map(t => <TaskRow key={t.id} t={t} />)}</ul>
            : <Empty icon={<ClipboardCheck size={20} />} title="No physical actions">Everything in scope was still at the plant: the holds alone stop it at the gate.</Empty>}
        </Card>
        <div className="space-y-5">
          <ReleasePanel r={r} openTasks={openTasks} />
          <Card>
            <CardHeader eyebrow="Problem solving" title={<span className="flex items-center gap-2"><FileText size={15} /> 8D report</span>} />
            <div className="space-y-2 p-4">
              {mine.map(x => <Link key={x.id} to={`/reports/${x.id}`} className="flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-[12.5px] hover:border-line-strong"><Status value={x.status} /><span className="flex-1">Revision {x.revisions} · {ago(x.created_at)}</span><span className="text-muted">open →</span></Link>)}
              {draft.error && <ErrorBox error={draft.error} />}
              <Button className="w-full" disabled={!can('quality_engineer', 'quality_manager')} loading={draft.isPending}
                onClick={() => draft.mutate(undefined, { onSuccess: rep => nav(`/reports/${rep.id}`) })}>{mine.length ? 'Draft another 8D' : 'Draft 8D from this recall'}</Button>
              <div className="text-[11px] text-muted">Facts come from the recorded recall; root cause and actions stay pending until people fill them in.</div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  )
}

export default function Recalls() {
  const { id } = useParams()
  const { data, isLoading } = useGet<{ items: Recall[] }>('/recalls', { refetchInterval: 8000 })
  const items = data?.items ?? []
  const selected = items.find(r => r.id === id) ?? (id ? undefined : items[0])
  return (
    <div>
      <PageHeader eyebrow="Quarantine · segregation · release" title="Recalls"
        description="Every executed quarantine, the physical actions it raised for stock that was already loaded, moving or delivered, and the controlled release once the cause is resolved." />
      {!isLoading && !items.length
        ? <Card><Empty icon={<Siren size={20} />} title="No recalls yet">Preview and execute one from <Link className="underline underline-offset-2" to="/trace">Trace & recall</Link>.</Empty></Card>
        : <div className="grid gap-5 xl:grid-cols-[300px_1fr]">
            <Card className="self-start">
              <CardHeader eyebrow="History" title={`${items.length} recall${items.length === 1 ? '' : 's'}`} />
              <ul className="divide-y divide-line">
                {items.map(r => {
                  const open = (r.segregation_tasks ?? []).filter(t => t.status === 'OPEN').length
                  return (
                    <li key={r.id}><Link to={`/recalls/${r.id}`} className={cx('block px-4 py-3 hover:bg-surface-2', selected?.id === r.id && 'bg-accent-soft')}>
                      <div className="flex items-center gap-2"><Mono className="text-[13px] font-semibold">{r.scope.label}</Mono><Pill>{r.scope.type}</Pill><span className="ml-auto"><Status value={isReleased(r) ? 'RELEASED' : 'QUARANTINED'} /></span></div>
                      <div className="mt-1 truncate text-[11.5px] text-muted">{r.affected.units} units · {open ? `${open} open task${open > 1 ? 's' : ''}` : 'no open tasks'} · {ago(r.executed_at)}</div>
                    </Link></li>
                  )
                })}
              </ul>
            </Card>
            {selected ? <RecallDetail r={selected} /> : id && !isLoading ? <Card><Empty title="Recall not found">It may have been created in another demo dataset.</Empty></Card> : null}
          </div>}
    </div>
  )
}
