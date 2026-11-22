import { Link, useNavigate, useParams } from 'react-router-dom'
import { FileCheck2, Factory, Layers } from 'lucide-react'
import { useGet } from '../api/hooks'
import type { Heat, Line, Lot, Supplier, Unit } from '../api/types'
import { Card, CardHeader, Mono, PageHeader, Pill, Spinner, Status, cx, fmtDateTime, toneOf, toneVar, Honesty } from '../components/ui'
import { StackBar } from '../components/domain'

export default function Lots() {
  const { id } = useParams(); const nav = useNavigate()
  const { data: lots } = useGet<{ items: Lot[] }>('/lots')
  const { data: heats } = useGet<{ items: Heat[] }>('/heats')
  const { data: sups } = useGet<{ items: Supplier[] }>('/suppliers')
  const { data: lines } = useGet<{ items: Line[] }>('/lines')
  const lotId = id ?? lots?.items[0]?.id
  const lot = lots?.items.find(l => l.id === lotId)
  const { data: units } = useGet<{ items: Unit[] }>(lotId ? `/units?lot_id=${lotId}` : null)

  return (
    <div>
      <PageHeader eyebrow="Material → lot → unit" title="Material & lots" description="Raw-material heats with certificate digests, the production lots made from them, and every serialized unit." />
      <div className="grid gap-6 xl:grid-cols-[340px_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="Raw material" title="Heats" />
            <ul className="divide-y divide-line">
              {heats?.items.map(h => {
                const sup = sups?.items.find(s => s.id === h.supplier_id)
                return (
                  <li key={h.id} className="px-4 py-3">
                    <div className="flex items-center gap-2"><Mono className="font-semibold">{h.heat_code}</Mono><span className="text-[11.5px] text-muted">{h.material_grade}</span>{h.held && <Pill tone="reject">HELD</Pill>}
                      <Link to={`/trace?q=${h.heat_code}`} className="ml-auto text-[11.5px] text-accent hover:underline">trace</Link></div>
                    <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-muted"><Factory size={12} /> {sup?.name} {sup?.synthetic && <Honesty kind="synthetic" />}</div>
                    <div className="mt-1 flex items-center gap-1.5 text-[11.5px] text-muted" title={h.certificate_sha256}><FileCheck2 size={12} /> {h.certificate_name} · <Mono className="text-[10.5px]">sha256:{h.certificate_sha256.slice(0, 12)}…</Mono></div>
                  </li>
                )
              })}
            </ul>
          </Card>
          <Card>
            <CardHeader eyebrow="Production" title="Lots" />
            <ul className="divide-y divide-line">
              {lots?.items.map(l => (
                <li key={l.id}>
                  <button onClick={() => nav(`/lots/${l.id}`)} className={cx('w-full px-4 py-3 text-left hover:bg-surface-2', l.id === lotId && 'bg-surface-2')}>
                    <div className="flex items-center gap-2"><Mono className="font-semibold">{l.lot_code}</Mono><span className="text-[11.5px] text-muted">{lines?.items.find(x => x.id === l.line_id)?.name}</span>
                      <span className="ml-auto flex gap-1">{l.held && <Pill tone="reject">QUARANTINED</Pill>}{l.containment_ids.length > 0 && <Pill tone="hold">CONTAINED</Pill>}</span></div>
                    <div className="mt-2"><StackBar parts={[{ value: l.counts.PASS, color: 'var(--pass)', label: 'PASS' }, { value: l.counts.HOLD, color: 'var(--hold)', label: 'HOLD' }, { value: l.counts.REJECT, color: 'var(--reject)', label: 'REJECT' }, { value: l.counts.UNINSPECTED, color: 'var(--line-strong)', label: 'UNINSPECTED' }]} height={6} /></div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <Card>
          {!lot ? <Spinner /> : <>
            <CardHeader eyebrow="Lot detail" title={<span className="flex items-center gap-2"><Layers size={15} className="text-accent" /> {lot.lot_code}</span>}
              right={<Link to={`/trace?q=${lot.lot_code}`} className="text-[12px] text-accent hover:underline">Trace genealogy →</Link>} />
            <div className="grid gap-px border-b border-line bg-line sm:grid-cols-4">
              {[['Line', lines?.items.find(x => x.id === lot.line_id)?.name], ['Heat(s)', lot.heat_ids.map(h => heats?.items.find(x => x.id === h)?.heat_code).join(', ')], ['Planned qty', lot.planned_qty], ['Created', fmtDateTime(lot.created_at)]].map(([k, v]) => (
                <div key={String(k)} className="bg-surface px-4 py-3"><div className="eyebrow">{k}</div><div className="num mt-1 text-[13px]">{v}</div></div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-4 border-b border-line px-4 py-3 text-[12px]">
              {(['PASS', 'HOLD', 'REJECT', 'UNINSPECTED'] as const).map(k => <span key={k} className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: k === 'UNINSPECTED' ? 'var(--line-strong)' : toneVar[toneOf(k)] }} />{k.toLowerCase()} <span className="num text-muted">{lot.counts[k]}</span></span>)}
              <span className="ml-auto text-muted">Dot ring = dispatch-blocked</span>
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(118px,1fr))] gap-2 p-4">
              {units?.items.map(u => {
                const t = u.quality_status === 'UNINSPECTED' ? 'neutral' : toneOf(u.quality_status)
                const blocked = u.quality_status === 'PASS' && !u.eligibility.dispatch_eligible
                return (
                  <Link key={u.id} to={`/units/${u.id}`} title={u.eligibility.blockers.map(b => b.code).join(', ') || 'Dispatch-eligible'}
                    className="group rounded-lg border border-line bg-surface-2/50 px-2.5 py-2 hover:border-accent">
                    <div className="flex items-center gap-1.5">
                      <span className={cx('size-2 rounded-full', blocked && 'ring-2 ring-offset-1 ring-offset-surface')} style={{ background: t === 'neutral' ? 'var(--line-strong)' : toneVar[t], ['--tw-ring-color' as any]: 'var(--hold)' }} />
                      <Mono className="text-[11px]">{u.serial.slice(-9)}</Mono>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[10.5px] text-muted"><span>{u.quality_status === 'UNINSPECTED' ? 'uninspected' : u.quality_status.toLowerCase()}</span>{u.package_id && <span>packed</span>}</div>
                  </Link>
                )
              })}
            </div>
          </>}
        </Card>
      </div>
    </div>
  )
}

export function UnitStatusLine({ u }: { u: Unit }) {
  return <span className="flex items-center gap-2"><Status value={u.quality_status} />{!u.eligibility.dispatch_eligible && u.quality_status === 'PASS' && <Pill tone="hold">DISPATCH-BLOCKED</Pill>}</span>
}
