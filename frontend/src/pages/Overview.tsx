import { Link } from 'react-router-dom'
import { Activity, ArrowRight, PackageX, ShieldAlert, Truck, CheckCircle2 } from 'lucide-react'
import { useGet } from '../api/hooks'
import type { Containment, DashboardSummary, Heat, Lot, Shipment, FourMEvent } from '../api/types'
import { Card, CardHeader, Empty, PageHeader, Pill, Stat, Status, ago, Mono, Dot } from '../components/ui'
import { StackBar } from '../components/domain'

export default function Overview() {
  const { data: s } = useGet<DashboardSummary>('/dashboard/summary', { refetchInterval: 5000 })
  const { data: lots } = useGet<{ items: Lot[] }>('/lots')
  const { data: conts } = useGet<{ items: Containment[] }>('/containments')
  const { data: evs } = useGet<{ items: FourMEvent[] }>('/four-m-events')
  const { data: ships } = useGet<{ items: (Shipment & { loaded_units: number; expected_units: number })[] }>('/shipments')
  const { data: heats } = useGet<{ items: Heat[] }>('/heats')
  const active = conts?.items.filter(c => c.status === 'ACTIVE') ?? []
  const u = s?.units_by_status

  const stages = [
    { label: 'Material heats', value: heats?.items.length ?? '—', to: '/lots', hint: `${heats?.items.filter(h => h.held).length ?? 0} held` },
    { label: 'Units produced', value: u ? u.UNINSPECTED + u.PASS + u.HOLD + u.REJECT : '—', to: '/lots', hint: `${u?.UNINSPECTED ?? 0} awaiting inspection` },
    { label: 'Inspected PASS', value: u?.PASS ?? '—', to: '/inspect', hint: `${u?.HOLD ?? 0} hold · ${u?.REJECT ?? 0} reject` },
    { label: 'Dispatch-eligible', value: s?.dispatch_eligible_units ?? '—', to: '/packing', hint: 'unshipped, all gates clear' },
    { label: 'Open shipments', value: s?.open_shipments ?? '—', to: '/dispatch', hint: 'at dock' },
  ]

  return (
    <div>
      <PageHeader eyebrow="Plant overview · Line 1 & Line 2" title="Quality & dispatch integrity"
        description="Live state of every serialized unit from material heat to consignee. Every number below is computed from recorded events." />

      {/* Flow strip */}
      <Card className="mb-6 overflow-hidden">
        <div className="grid grid-cols-2 md:grid-cols-5">
          {stages.map((st, i) => (
            <Link key={st.label} to={st.to} className="group relative border-b border-r border-line p-4 last:border-r-0 hover:bg-surface-2 md:border-b-0">
              <div className="flex items-center gap-2"><span className="num text-[10.5px] text-accent">{String(i + 1).padStart(2, '0')}</span><span className="eyebrow">{st.label}</span></div>
              <div className="num mt-2 text-[30px] font-medium leading-none tracking-tight">{st.value}</div>
              <div className="mt-1.5 text-[11.5px] text-muted">{st.hint}</div>
              {i < stages.length - 1 && <ArrowRight size={14} className="absolute right-3 top-4 hidden text-line-strong group-hover:text-accent md:block" />}
            </Link>
          ))}
        </div>
      </Card>

      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Active 4M containments" value={s?.active_containments ?? '—'} tone={s?.active_containments ? 'hold' : 'pass'} icon={<ShieldAlert size={15} />} sub={s?.active_containments ? 'release blocked on affected units' : 'no open change points'} />
        <Stat label="Held / seal-review packages" value={s?.held_packages ?? '—'} tone={s?.held_packages ? 'reject' : 'neutral'} icon={<PackageX size={15} />} sub="cannot pass the dispatch gate" />
        <Stat label="First-pass yield" value={s?.first_pass_yield != null ? `${Math.round(s.first_pass_yield * 100)}%` : '—'} icon={<CheckCircle2 size={15} />} sub="PASS ÷ inspections recorded" />
        <Stat label="Drift alerts" value={s?.open_alerts ?? '—'} tone={s?.open_alerts ? 'hold' : 'neutral'} icon={<Activity size={15} />} sub="CUSUM on anomaly scores" />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.3fr_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="Dispatch gate" title="Recent load decisions" right={<Link to="/dispatch" className="text-[12px] text-accent hover:underline">Open dock →</Link>} />
            {!s?.recent_scans.length ? <Empty icon={<Truck size={20} />} title="No scans yet">Scan a sealed package at Dock 3 to see the gate decide.</Empty> :
              <ul className="divide-y divide-line">
                {s.recent_scans.map(sc => (
                  <li key={sc.scan_id} className="flex items-center gap-3 px-4 py-2.5">
                    <Status value={sc.decision} />
                    <Mono className="w-20">{sc.package_label ?? 'unknown'}</Mono>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted">{sc.reasons.length ? sc.reasons.map(r => r.code).join(' · ') : 'all 11 gates passed'}</span>
                    <span className="text-[11.5px] text-muted">{ago(sc.decided_at)}</span>
                  </li>
                ))}
              </ul>}
          </Card>

          <Card>
            <CardHeader eyebrow="Genealogy" title="Production lots" right={<Link to="/lots" className="text-[12px] text-accent hover:underline">All lots →</Link>} />
            <div className="divide-y divide-line">
              {lots?.items.map(l => {
                const total = l.counts.PASS + l.counts.HOLD + l.counts.REJECT + l.counts.UNINSPECTED
                return (
                  <Link to={`/lots/${l.id}`} key={l.id} className="grid grid-cols-[110px_1fr_auto] items-center gap-4 px-4 py-3 hover:bg-surface-2">
                    <div><Mono className="font-semibold">{l.lot_code}</Mono><div className="text-[11px] text-muted">{total} units</div></div>
                    <StackBar parts={[{ value: l.counts.PASS, color: 'var(--pass)', label: 'PASS' }, { value: l.counts.HOLD, color: 'var(--hold)', label: 'HOLD' }, { value: l.counts.REJECT, color: 'var(--reject)', label: 'REJECT' }, { value: l.counts.UNINSPECTED, color: 'var(--line-strong)', label: 'UNINSPECTED' }]} />
                    <div className="flex gap-1.5">{l.held && <Pill tone="reject">QUARANTINED</Pill>}{l.containment_ids.length > 0 && <Pill tone="hold">CONTAINED</Pill>}</div>
                  </Link>
                )
              })}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="Change points" title="4M containment" right={<Link to="/containment" className="text-[12px] text-accent hover:underline">Manage →</Link>} />
            {!active.length ? <Empty icon={<ShieldAlert size={20} />} title="No active containment">Log a Man / Machine / Material / Method change to trigger deterministic containment.</Empty> :
              <ul className="divide-y divide-line">{active.map(c => {
                const ev = evs?.items.find(e => e.id === c.event_id)
                return (
                  <li key={c.id} className="px-4 py-3">
                    <div className="flex items-center gap-2"><Dot tone="hold" pulse /><Mono className="font-semibold">{ev?.code}</Mono><Pill tone="hold">{ev?.category}</Pill><span className="ml-auto text-[11.5px] text-muted">{ago(c.created_at)}</span></div>
                    <div className="mt-1 text-[12.5px] text-ink-2">{ev?.description}</div>
                    <div className="mt-2 flex flex-wrap gap-3 text-[11.5px] text-muted">
                      <span><span className="num text-ink">{c.affected_unit_ids.length}</span> units held</span>
                      {c.obligations.map(o => <span key={o.type}><span className="num text-ink">{o.completed}/{o.required}</span> {o.type === 'FULL_INSPECT_FIRST_N' ? 'first-piece' : 'rechecks'}</span>)}
                    </div>
                  </li>
                )
              })}</ul>}
          </Card>

          <Card>
            <CardHeader eyebrow="Movement" title="Shipments" />
            <ul className="divide-y divide-line">
              {ships?.items.map(sh => (
                <li key={sh.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Mono className="w-20 font-semibold">{sh.code}</Mono>
                  <div className="min-w-0 flex-1"><div className="truncate text-[12.5px]">{sh.vehicle_no} · {sh.dock}</div><div className="text-[11px] text-muted">{sh.movements.at(-1)?.location ?? 'At dock'} {sh.movements.length ? `· ${ago(sh.movements.at(-1)!.occurred_at)}` : ''}</div></div>
                  <span className="num text-[11.5px] text-muted">{sh.loaded_units}/{sh.expected_units}</span>
                  <Status value={sh.status} />
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  )
}
