import { useState } from 'react'
import { Package, Plus, Lock, Trash2 } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Order, Package as Pkg, Unit, Customer } from '../api/types'
import { Button, Card, CardHeader, ErrorBox, Honesty, Modal, Mono, PageHeader, Pill, Select, Status, cx, ago } from '../components/ui'
import { ImageCapture, QrLabel, Reasons, SEAL_FIXTURES, type Captured } from '../components/domain'

type PkgView = Pkg & { units: Unit[]; shipment_id: string | null }

function SealEnroll({ pkg, onDone }: { pkg: PkgView; onDone: () => void }) {
  const [cap, setCap] = useState<Captured | null>(null)
  const act = useAct(async () => { const f = new FormData(); f.set('image', cap!.blob, 'seal.jpg'); if (cap!.fixture) f.set('fixture', 'enroll'); return api.post(`/packages/${pkg.id}/seal`, f) })
  return (
    <div className="space-y-3">
      <div className="text-[12.5px] text-muted">Apply a patterned tamper-evident seal across the lid opening and photograph it squarely. The reference is stored by sha256 and the package label is signed.</div>
      <ImageCapture kind="seal" fixtures={[{ value: 'intact', label: 'Seal reference', hint: 'synthetic patterned tape' }]} captured={cap} onCaptured={setCap} />
      {act.error && <ErrorBox error={act.error} />}
      <Button variant="primary" className="w-full" disabled={!cap} loading={act.isPending} onClick={() => act.mutate(undefined, { onSuccess: onDone })}><Lock size={14} /> Enroll seal & sign label</Button>
    </div>
  )
}

export default function Packing() {
  const { can } = useAuth()
  const { data: orders } = useGet<{ items: Order[] }>('/orders')
  const { data: customers } = useGet<{ items: Customer[] }>('/customers')
  const [orderId, setOrderId] = useState('ord_1002')
  const { data: pkgs } = useGet<{ items: PkgView[] }>(`/packages?order_id=${orderId}`)
  const { data: units } = useGet<{ items: Unit[] }>('/units')
  const [sel, setSel] = useState<string | null>(null)
  const [sealFor, setSealFor] = useState<PkgView | null>(null)
  const order = orders?.items.find(o => o.id === orderId)
  const pkg = pkgs?.items.find(p => p.id === sel) ?? pkgs?.items.find(p => p.state === 'OPEN') ?? pkgs?.items[0]
  const create = useAct(() => api.post<PkgView>('/packages', { order_id: orderId }))
  const add = useAct((uid: string) => api.post(`/packages/${pkg!.id}/items`, { unit_id: uid }))
  const remove = useAct((uid: string) => api.del(`/packages/${pkg!.id}/items/${uid}`))
  const candidates = units?.items.filter(u => !u.package_id && u.part_id === order?.part_id && u.quality_status !== 'REJECT') ?? []

  return (
    <div>
      <PageHeader eyebrow="Pack · seal · label" title="Packing & seals" description="Only dispatch-eligible units can be packed. Sealing enrolls a reference photo and signs a package token that binds order, passports and seal digest."
        actions={<><Select value={orderId} onChange={e => { setOrderId(e.target.value); setSel(null) }} className="w-64">{orders?.items.map(o => <option key={o.id} value={o.id}>{o.order_no} · {customers?.items.find(c => c.id === o.customer_id)?.city} · {o.qty_units} units</option>)}</Select>
          <Button variant="primary" disabled={!can('operator')} loading={create.isPending} onClick={() => create.mutate(undefined, { onSuccess: (p: any) => setSel(p.id) })}><Plus size={15} /> New package</Button></>} />
      <div className="grid gap-6 xl:grid-cols-[300px_1fr]">
        <Card>
          <CardHeader eyebrow={order?.order_no} title={`Packages (${pkgs?.items.length ?? 0})`} />
          <ul className="divide-y divide-line">{pkgs?.items.map(p => (
            <li key={p.id}><button onClick={() => setSel(p.id)} className={cx('flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2', pkg?.id === p.id && 'bg-surface-2')}>
              <Package size={15} className="text-muted" /><div className="min-w-0 flex-1"><Mono className="font-semibold">{p.label}</Mono><div className="text-[11px] text-muted">{p.unit_ids.length} units</div></div>
              {p.held ? <Status value="HELD" /> : <Status value={p.state} />}
            </button></li>
          ))}</ul>
        </Card>
        {pkg && (
          <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
            <Card>
              <CardHeader eyebrow="Contents" title={<span className="flex items-center gap-2"><Mono className="text-[14px] font-semibold">{pkg.label}</Mono><Status value={pkg.held ? 'HELD' : pkg.state} /></span>}
                right={pkg.state === 'OPEN' && <Button size="sm" variant="primary" disabled={!can('operator') || !pkg.unit_ids.length} onClick={() => setSealFor(pkg)}><Lock size={13} /> Seal</Button>} />
              {pkg.held && <div className="border-b border-line px-4 py-3"><Reasons reasons={pkg.hold_reasons} /></div>}
              <ul className="divide-y divide-line">{pkg.units.map(u => (
                <li key={u.id} className="flex items-center gap-3 px-4 py-2">
                  <Mono>{u.serial}</Mono><Status value={u.quality_status} />
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted">{u.eligibility.blockers.map(b => b.code).join(' · ')}</span>
                  {pkg.state === 'OPEN' && <button onClick={() => remove.mutate(u.id)} className="text-muted hover:text-reject" aria-label="Remove"><Trash2 size={14} /></button>}
                </li>
              ))}{!pkg.units.length && <li className="p-4 text-[13px] text-muted">Empty — add eligible units below.</li>}</ul>
              {pkg.state === 'OPEN' && (
                <div className="border-t border-line p-4">
                  <div className="eyebrow mb-2">Add units · {candidates.length} unpacked</div>
                  {add.error && <div className="mb-2"><ErrorBox error={add.error} /></div>}
                  <div className="flex flex-wrap gap-1.5">{candidates.map(u => (
                    <button key={u.id} disabled={!can('operator')} onClick={() => add.mutate(u.id)} title={u.eligibility.blockers.map(b => b.code).join(', ') || 'eligible'}
                      className={cx('rounded-md border px-2 py-1 font-mono text-[11px] transition', u.eligibility.dispatch_eligible ? 'border-pass/40 text-pass hover:bg-pass/10' : 'border-line text-muted line-through decoration-reject/60 hover:border-reject/50')}>
                      {u.serial.slice(-9)}
                    </button>
                  ))}</div>
                  <div className="mt-2 text-[11px] text-muted">Struck-through units are not eligible — clicking shows the server’s reason.</div>
                </div>
              )}
            </Card>
            <Card>
              <CardHeader eyebrow="Signed label" title="Package identity" />
              <div className="space-y-4 p-4">
                {pkg.token ? <QrLabel token={pkg.token} title={pkg.label} sub={`${order?.order_no} · ${pkg.unit_ids.length} units`} /> : <div className="text-[13px] text-muted">Label is signed when the seal is enrolled.</div>}
                {pkg.last_seal_check && (
                  <div className="rounded-lg border border-line bg-surface-2 p-3 text-[12.5px]">
                    <div className="mb-1 flex items-center gap-2"><span className="eyebrow">Last seal check</span>{pkg.last_seal_check.synthetic && <Honesty kind="synthetic" />}</div>
                    <div className="flex items-center gap-2"><Pill>{pkg.last_seal_check.kind}</Pill>{pkg.last_seal_check.result && <Status value={pkg.last_seal_check.result} />}<span className="text-[11px] text-muted">{ago(pkg.last_seal_check.created_at)}</span></div>
                    <div className="mt-1 text-muted">{pkg.last_seal_check.explanation}</div>
                  </div>
                )}
                {pkg.seal_ref_sha256 && <div className="text-[11px] text-muted">Seal reference <Mono>sha256:{pkg.seal_ref_sha256.slice(0, 16)}…</Mono></div>}
              </div>
            </Card>
          </div>
        )}
      </div>
      <Modal open={!!sealFor} onClose={() => setSealFor(null)} title={`Seal ${sealFor?.label}`}>{sealFor && <SealEnroll pkg={sealFor} onDone={() => setSealFor(null)} />}</Modal>
    </div>
  )
}
export { SEAL_FIXTURES }
