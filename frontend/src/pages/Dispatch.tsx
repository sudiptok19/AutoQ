import { useState } from 'react'
import { Camera, CheckCircle2, ClipboardPaste, Lock, QrCode, Repeat, ShieldX, Truck, XOctagon, Flag, MapPin } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Order, Package, ScanDecision, SealCheck, Shipment, Unit } from '../api/types'
import { Button, Card, CardHeader, Empty, ErrorBox, Honesty, Input, Modal, Mono, PageHeader, Pill, Select, Status, ago, cx, fmtTime, toneVar } from '../components/ui'
import { forgeToken } from '../lib/token'
import { GateChecklist, ImageCapture, Reasons, SEAL_FIXTURES, type Captured } from '../components/domain'

type ShipView = Shipment & { expected_units: number; loaded_units: number; scans: ScanDecision[] }
type PkgView = Package & { units: Unit[]; shipment_id: string | null }

const deviceId = (() => { try { let d = localStorage.getItem('autoq-device'); if (!d) { d = 'web-' + Math.random().toString(36).slice(2, 6); localStorage.setItem('autoq-device', d) } return d } catch { return 'web-anon' } })()
let seq = Number((() => { try { return localStorage.getItem('autoq-seq') } catch { return null } })() ?? 0)
const nextEventId = () => { seq++; try { localStorage.setItem('autoq-seq', String(seq)) } catch { /* ignore */ } return `${deviceId}:${String(seq).padStart(6, '0')}` }

function SealRecheck({ pkg, onClose }: { pkg: PkgView; onClose: () => void }) {
  const [cap, setCap] = useState<Captured | null>(null)
  const act = useAct(async () => { const f = new FormData(); f.set('image', cap!.blob, 'seal.jpg'); if (cap!.fixture) f.set('fixture', cap!.fixture); return api.post<SealCheck>(`/packages/${pkg.id}/seal-checks`, f) })
  const res = act.data
  return (
    <div className="space-y-4">
      <ImageCapture kind="seal" fixtures={SEAL_FIXTURES} captured={cap} onCaptured={c => { setCap(c); act.reset() }} />
      {act.error && <ErrorBox error={act.error} />}
      {res ? (
        <div className="rise rounded-xl border p-4" style={{ borderColor: `color-mix(in srgb, ${toneVar[res.result === 'INTACT' ? 'pass' : res.result === 'SUSPICIOUS' ? 'reject' : 'hold']} 40%, transparent)` }}>
          <div className="flex items-center gap-2"><Status value={res.result} />{res.synthetic && <Honesty kind="synthetic" />}<Mono className="ml-auto text-muted">{res.method_version}</Mono></div>
          <div className="mt-2 text-[12.5px] text-ink-2">{res.explanation}</div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            {([['Inliers', res.metrics.inliers, '≥ 40'], ['Inlier ratio', res.metrics.inlier_ratio, ''], ['SSIM', res.metrics.ssim, '≥ 0.72 intact']] as [string, unknown, string][]).map(([k, v, t]) => (
              <div key={String(k)} className="rounded-lg bg-surface-2 py-2"><div className="num text-[16px]">{v == null ? '—' : String(v as any)}</div><div className="text-[10.5px] text-muted">{k} {t && `· ${t}`}</div></div>
            ))}
          </div>
          <div className="mt-3 text-[11.5px] text-muted">{res.result === 'INTACT' ? 'Valid for loading for 60 minutes.' : res.result === 'SUSPICIOUS' ? 'Package moved to SEAL_REVIEW. A Quality Engineer must inspect and re-enroll.' : 'Not evidence of tampering — recapture with better light and framing.'}</div>
          <div className="mt-3 flex justify-end"><Button onClick={onClose}>Done</Button></div>
        </div>
      ) : (
        <Button variant="primary" className="w-full" disabled={!cap} loading={act.isPending} onClick={() => act.mutate(undefined)}><Lock size={14} /> Compare with enrolled reference</Button>
      )}
      <div className="text-[11px] text-muted">Visual similarity is evidence, not proof the package was never opened. Bad captures fail safe to UNVERIFIABLE.</div>
    </div>
  )
}

function DecisionPanel({ d }: { d: ScanDecision | null }) {
  if (!d) return <Empty icon={<QrCode size={22} />} title="Waiting for a scan">Each scan runs 11 ordered gates in one transaction. Accepted and blocked attempts are both recorded.</Empty>
  const ok = d.decision === 'ACCEPTED'
  return (
    <div key={d.scan_id} className="space-y-4 p-4">
      <div className="rise relative overflow-hidden rounded-xl p-5" style={{ background: `color-mix(in srgb, ${ok ? 'var(--pass)' : 'var(--reject)'} 12%, transparent)`, border: `1px solid color-mix(in srgb, ${ok ? 'var(--pass)' : 'var(--reject)'} 40%, transparent)` }}>
        <div className="flex items-center gap-3">
          {ok ? <CheckCircle2 size={30} className="text-pass" /> : <XOctagon size={30} className="text-reject" />}
          <div>
            <div className="num text-[26px] font-semibold leading-none tracking-tight" style={{ color: ok ? 'var(--pass)' : 'var(--reject)' }}>{ok ? 'LOAD ACCEPTED' : 'LOAD BLOCKED'}</div>
            <div className="mt-1 text-[12px] text-ink-2"><Mono>{d.package_label ?? 'unidentified label'}</Mono> · {fmtTime(d.decided_at)} · {d.decided_by} {d.replay && <Pill tone="info">REPLAY · original decision returned</Pill>}</div>
          </div>
        </div>
        {!ok && <div className="mt-4"><Reasons reasons={d.reasons} /></div>}
      </div>
      <GateChecklist checks={d.checks} animate />
      <div className="text-[11px] text-muted">event <Mono>{d.client_event_id}</Mono> · idempotent: retrying returns this same decision</div>
    </div>
  )
}

export default function Dispatch() {
  const { can } = useAuth()
  const { data: ships } = useGet<{ items: ShipView[] }>('/shipments', { refetchInterval: 6000 })
  const { data: orders } = useGet<{ items: Order[] }>('/orders')
  const [shipId, setShipId] = useState('shp_0003')
  const ship = ships?.items.find(s => s.id === shipId)
  const { data: pkgs } = useGet<{ items: PkgView[] }>('/packages')
  const [decision, setDecision] = useState<ScanDecision | null>(null)
  const [recheck, setRecheck] = useState<PkgView | null>(null)
  const [paste, setPaste] = useState('')
  const [lastEvent, setLastEvent] = useState<{ id: string; token: string } | null>(null)
  const scan = useAct((b: { token: string; id?: string }) => { const id = b.id ?? nextEventId(); setLastEvent({ id, token: b.token }); return api.post<ScanDecision>(`/shipments/${shipId}/scans`, { token: b.token, client_event_id: id, device_id: deviceId }) })
  const close = useAct(() => api.post(`/shipments/${shipId}/close`, {}))
  const move = useAct((type: string) => api.post(`/shipments/${shipId}/events`, { type, location: type === 'DEPARTED' ? 'Plant gate' : type === 'DELIVERED' ? orders?.items.find(o => o.id === ship?.order_id)?.destination : 'Checkpoint' }))
  const doScan = (token: string, id?: string) => scan.mutate({ token, id }, { onSuccess: setDecision })

  const atDock = pkgs?.items.filter(p => p.token && !p.shipment_id && ['SEALED', 'SEAL_REVIEW'].includes(p.state)) ?? []
  const mine = atDock.filter(p => p.order_id === ship?.order_id), others = atDock.filter(p => p.order_id !== ship?.order_id)
  const loaded = pkgs?.items.filter(p => ship?.loaded_package_ids.includes(p.id)) ?? []
  const pct = ship ? Math.min(100, (ship.loaded_units / Math.max(1, ship.expected_units)) * 100) : 0
  const tamper = (t: string) => forgeToken(t, p => { p.order = 'ord_9999' })
  const disabled = !can('dispatcher')

  const crate = (p: PkgView, foreign = false) => {
    const sc = p.last_seal_check
    const fresh = sc?.kind === 'RECHECK' && Date.now() - new Date(sc.created_at).getTime() < 60 * 60_000
    return (
      <div key={p.id} className={cx('rounded-xl border bg-surface p-3', p.held ? 'border-reject/40' : 'border-line')}>
        <div className="flex items-center gap-2">
          <Mono className="text-[13px] font-semibold">{p.label}</Mono>
          {p.held ? <Status value="HELD" /> : p.state === 'SEAL_REVIEW' ? <Status value="SEAL_REVIEW" /> : null}
          {foreign && <Pill>{orders?.items.find(o => o.id === p.order_id)?.order_no}</Pill>}
          <span className="ml-auto text-[11px] text-muted">{p.unit_ids.length} units</span>
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-[11.5px] text-muted">
          <Lock size={11} /> {sc?.kind === 'RECHECK' ? <><Status value={sc.result} /> {ago(sc.created_at)}</> : 'enrolled · recheck needed'}
          {p.units.some(u => u.eligibility.blockers.some(b => b.code === 'CONTAINMENT_ACTIVE')) && <Pill tone="hold">4M</Pill>}
        </div>
        <div className="mt-3 flex gap-2">
          <Button size="sm" disabled={disabled} className="flex-1" onClick={() => setRecheck(p)}><Camera size={13} /> Seal{fresh ? ' ✓' : ''}</Button>
          <Button size="sm" variant="primary" disabled={disabled || !ship || ship.status !== 'OPEN'} className="flex-1" loading={scan.isPending && scan.variables?.token === p.token} onClick={() => doScan(p.token!)}><QrCode size={13} /> Scan</Button>
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader eyebrow="Policy interlock" title="Dispatch gate"
        description="Loading depends on the whole chain: signature, key, identity, lifecycle, containment, quarantine, order, duplicates, seal, quantity and sequence."
        actions={<Select value={shipId} onChange={e => { setShipId(e.target.value); setDecision(null) }} className="w-72">{ships?.items.map(s => <option key={s.id} value={s.id}>{s.code} · {s.dock} · {s.status}</option>)}</Select>} />
      {disabled && <div className="mb-4 rounded-lg border border-line bg-surface px-3 py-2 text-[12.5px] text-muted">You are viewing as a non-dispatcher. Scans would be refused with <Mono className="text-reject">ROLE_FORBIDDEN</Mono> — switch to <b>Kiran Das (Dispatcher)</b>.</div>}

      {ship && (
        <Card className="mb-6">
          <div className="grid gap-4 p-4 md:grid-cols-[1fr_auto] md:items-center">
            <div>
              <div className="flex flex-wrap items-center gap-2"><Truck size={16} className="text-accent" /><Mono className="text-[15px] font-semibold">{ship.code}</Mono><Status value={ship.status} /><span className="text-[12.5px] text-muted">{ship.vehicle_no} · {ship.dock} · {orders?.items.find(o => o.id === ship.order_id)?.order_no} → {orders?.items.find(o => o.id === ship.order_id)?.destination}</span></div>
              <div className="mt-3 flex items-center gap-3">
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-3"><div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} /></div>
                <span className="num text-[13px]"><b>{ship.loaded_units}</b> / {ship.expected_units} units · {ship.loaded_package_ids.length} pkgs</span>
              </div>
              {ship.movements.length > 0 && <div className="mt-2 flex flex-wrap gap-3 text-[11.5px] text-muted">{ship.movements.map((m, i) => <span key={i} className="flex items-center gap-1"><MapPin size={11} />{m.type.toLowerCase()} · {m.location} · {ago(m.occurred_at)}</span>)}</div>}
            </div>
            <div className="flex flex-wrap gap-2">
              {ship.status === 'OPEN' && <Button disabled={disabled} loading={close.isPending} onClick={() => close.mutate(undefined)}><Flag size={14} /> Close manifest</Button>}
              {ship.status === 'CLOSED' && <Button variant="primary" disabled={disabled} onClick={() => move.mutate('DEPARTED')}><Truck size={14} /> Record departure</Button>}
              {ship.status === 'IN_TRANSIT' && <Button disabled={disabled} onClick={() => move.mutate('DELIVERED')}>Record delivery</Button>}
            </div>
          </div>
          {(close.error || move.error) && <div className="px-4 pb-4"><ErrorBox error={close.error || move.error} /></div>}
        </Card>
      )}

      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="At the dock" title={`Sealed packages for this order (${mine.length})`} right={<Honesty kind="synthetic">SIMULATED SCAN</Honesty>} />
            <div className="grid gap-3 p-4 sm:grid-cols-2">{mine.map(p => crate(p))}{!mine.length && <div className="text-[13px] text-muted">No sealed packages waiting.</div>}</div>
            <div className="border-t border-line px-4 py-2.5 text-[11.5px] text-muted">“Scan” submits the package’s real signed QR token, exactly what the phone scanner reads from the label.</div>
          </Card>
          <Card>
            <CardHeader eyebrow="Adversarial tests" title="Try to break the gate" />
            <div className="space-y-3 p-4">
              <div className="grid gap-2 sm:grid-cols-2">
                <Button disabled={disabled || !mine[0]} onClick={() => doScan(tamper(mine[0].token!))}><ShieldX size={14} /> Forged QR (order edited)</Button>
                <Button disabled={disabled || !lastEvent} onClick={() => lastEvent && doScan(lastEvent.token, lastEvent.id)}><Repeat size={14} /> Network retry (same event)</Button>
                <Button disabled={disabled || !loaded[0]} onClick={() => doScan(loaded[0].token!)}><Repeat size={14} /> Re-scan loaded package</Button>
                <Button disabled={disabled || !others[0]} onClick={() => doScan(others[0].token!)}>Wrong-order package</Button>
              </div>
              <form className="flex gap-2" onSubmit={e => { e.preventDefault(); if (paste.trim()) doScan(paste.trim()) }}>
                <Input value={paste} onChange={e => setPaste(e.target.value)} placeholder="Paste any AQ1.… token" className="font-mono text-[12px]" />
                <Button disabled={disabled || !paste.trim()}><ClipboardPaste size={14} /> Scan</Button>
              </form>
              {others.length > 0 && <div><div className="eyebrow mb-2">Other orders at dock</div><div className="grid gap-3 sm:grid-cols-2">{others.map(p => crate(p, true))}</div></div>}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="xl:sticky xl:top-20">
            <CardHeader eyebrow="Decision" title="Gate result" />
            {scan.error ? <div className="p-4"><ErrorBox error={scan.error} /></div> : <DecisionPanel d={decision} />}
          </Card>
          <Card>
            <CardHeader eyebrow="Persisted" title={`Scan log · ${ship?.code ?? ''}`} />
            <ul className="max-h-80 divide-y divide-line overflow-y-auto">
              {ship?.scans.map(s => (
                <li key={s.scan_id + s.decided_at}><button onClick={() => setDecision(s)} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-surface-2">
                  <Status value={s.decision} /><Mono className="w-20">{s.package_label ?? '?'}</Mono>
                  <span className="min-w-0 flex-1 truncate text-[11.5px] text-muted">{s.reasons.map(r => r.code).join(' · ') || 'all gates passed'}</span>
                  <span className="num text-[11px] text-muted">{fmtTime(s.decided_at)}</span>
                </button></li>
              ))}
              {!ship?.scans.length && <li className="p-4 text-[13px] text-muted">No attempts yet.</li>}
            </ul>
          </Card>
        </div>
      </div>
      <Modal open={!!recheck} onClose={() => setRecheck(null)} title={`Seal recheck · ${recheck?.label}`}>{recheck && <SealRecheck pkg={recheck} onClose={() => setRecheck(null)} />}</Modal>
    </div>
  )
}
