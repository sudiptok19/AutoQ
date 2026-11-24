import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ScanLine, ShieldCheck, Stamp } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Inspection, Lot, Line, Unit } from '../api/types'
import { Button, Card, CardHeader, ErrorBox, Field, Honesty, Modal, Mono, PageHeader, Select, Status, Textarea, cx, toneVar, toneOf, ago } from '../components/ui'
import { EvidenceImage, ImageCapture, Metric, PART_FIXTURES, QrLabel, Reasons, type Captured } from '../components/domain'

function VerdictPanel({ insp, unit }: { insp: Inspection; unit?: Unit }) {
  const t = toneOf(insp.verdict)
  const a = insp.analysis
  return (
    <div className="rise space-y-4">
      <div className="flex items-center gap-4 rounded-xl border p-4" style={{ borderColor: `color-mix(in srgb, ${toneVar[t]} 40%, transparent)`, background: `color-mix(in srgb, ${toneVar[t]} 8%, transparent)` }}>
        <div className="num text-[34px] font-semibold leading-none tracking-tight" style={{ color: toneVar[t] }}>{insp.verdict}</div>
        <div className="min-w-0 flex-1 text-[12.5px]">
          <div className="font-medium">{unit?.serial}</div>
          <div className="text-muted">{insp.verdict === 'PASS' ? 'Passport issued and signed.' : insp.verdict === 'HOLD' ? 'Needs quality-engineer review. No release.' : 'Rejected. No shipment identity.'}</div>
        </div>
        <div className="flex flex-col items-end gap-1">{insp.synthetic && <Honesty kind="synthetic" />}{!a.calibrated && <Honesty kind="uncalibrated" />}</div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <EvidenceImage url={insp.evidence.image_url} heatmap={insp.evidence.heatmap_url} regions={a.regions} />
        <div className="space-y-3">
          <div>
            <div className="eyebrow mb-1">Image quality gate</div>
            <Metric label="Sharpness" value={Math.round(insp.quality_gate.blur_laplacian_var * 10) / 10} ok={!insp.quality_gate.failures.includes('IMAGE_QUALITY_BLUR')} rule={`≥ ${insp.quality_gate.thresholds.blur_min}`} />
            <Metric label="Glare" value={`${(insp.quality_gate.glare_ratio * 100).toFixed(1)}%`} ok={!insp.quality_gate.failures.includes('IMAGE_QUALITY_GLARE')} rule={`≤ ${insp.quality_gate.thresholds.glare_max * 100}%`} />
          </div>
          <div>
            <div className="eyebrow mb-1 flex items-center gap-2">Analysis · {a.method} <Mono>{a.method_version}</Mono></div>
            {a.score === null ? <div className="text-[12.5px] text-muted">Not run — quality gate or calibration failed first.</div> : <>
              <ScoreBar score={a.score} hold={a.hold_threshold} reject={a.reject_threshold} />
              <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted"><span>Anomaly score <Mono className="text-ink">{fmtScore(a.score)}</Mono></span><Honesty kind="not-probability" /></div>
              {a.threshold_tightened && <div className="mt-1 text-[11px] text-hold">Thresholds tightened by active 4M containment.</div>}
            </>}
          </div>
          <Reasons reasons={insp.reasons} />
        </div>
      </div>
      {insp.passport && unit && insp.verdict === 'PASS' && (
        <div>
          <div className="eyebrow mb-2 flex items-center gap-1.5"><ShieldCheck size={11} /> Quality passport · {insp.passport.status}</div>
          <QrLabel token={insp.passport.token} title={unit.serial} sub={`kid ${insp.passport.kid}`} />
          {insp.passport.status === 'HELD' && <div className="mt-2 text-[12px] text-hold">Passport issued but HELD: an active containment keeps this unit dispatch-ineligible until cleared.</div>}
        </div>
      )}
    </div>
  )
}

/** Scores are model outputs in [0,1]; four decimals is the meaningful precision for display. */
const fmtScore = (n: number | null | undefined) => (n == null ? '—' : n.toFixed(4))

export function ScoreBar({ score, hold, reject }: { score: number; hold: number; reject: number }) {
  return (
    <div className="relative h-2.5 rounded-full" style={{ background: `linear-gradient(90deg, color-mix(in srgb, var(--pass) 35%, transparent) 0 ${hold * 100}%, color-mix(in srgb, var(--hold) 35%, transparent) ${hold * 100}% ${reject * 100}%, color-mix(in srgb, var(--reject) 35%, transparent) ${reject * 100}%)` }}>
      <div className="absolute -top-1 h-4.5 w-1 -translate-x-1/2 rounded-full bg-ink shadow" style={{ left: `${Math.min(100, score * 100)}%` }} />
    </div>
  )
}

function ReviewModal({ insp, onClose }: { insp: Inspection | null; onClose: () => void }) {
  const [reason, setReason] = useState('')
  const act = useAct((b: { action: string }) => api.post(`/inspections/${insp!.id}/review`, { action: b.action, reason }))
  return (
    <Modal open={!!insp} onClose={onClose} title="Review HOLD">
      {insp && <div className="space-y-4">
        <EvidenceImage url={insp.evidence.image_url} heatmap={insp.evidence.heatmap_url} regions={insp.analysis.regions} />
        <Reasons reasons={insp.reasons} />
        <div className="rounded-lg bg-surface-2 p-3 text-[12px] text-muted">A review never releases a unit. <b className="text-ink">Reinspect</b> sends it back for a fresh capture; <b className="text-ink">Reject</b> is terminal and revokes any passport.</div>
        <Field label="Reason (recorded in audit chain)"><Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. Glare from overhead lamp; recapture with diffuser" /></Field>
        {act.error && <ErrorBox error={act.error} />}
        <div className="flex justify-end gap-2">
          <Button variant="danger" disabled={!reason.trim()} loading={act.isPending} onClick={() => act.mutate({ action: 'REJECT' }, { onSuccess: onClose })}>Reject unit</Button>
          <Button variant="primary" disabled={!reason.trim()} loading={act.isPending} onClick={() => act.mutate({ action: 'REINSPECT' }, { onSuccess: onClose })}>Allow reinspection</Button>
        </div>
      </div>}
    </Modal>
  )
}

export default function Inspect() {
  const { can } = useAuth()
  const [sp] = useSearchParams()
  const { data: lots } = useGet<{ items: Lot[] }>('/lots')
  const { data: lines } = useGet<{ items: Line[] }>('/lines')
  const [lotId, setLotId] = useState<string>('')
  const { data: units } = useGet<{ items: Unit[] }>(lotId ? `/units?lot_id=${lotId}` : null)
  const [unitId, setUnitId] = useState<string>(sp.get('unit') ?? '')
  const [cap, setCap] = useState<Captured | null>(null)
  const [result, setResult] = useState<Inspection | null>(null)
  const [review, setReview] = useState<Inspection | null>(null)
  const { data: recent } = useGet<{ items: Inspection[] }>('/inspections')
  const { data: all } = useGet<{ items: Unit[] }>('/units')

  useEffect(() => {
    if (lotId || !lots) return
    const fromUnit = all?.items.find(u => u.id === sp.get('unit'))
    setLotId(fromUnit?.lot_id ?? lots.items.find(l => l.counts.UNINSPECTED > 0)?.id ?? lots.items[0].id)
  }, [lots, all])
  const queue = useMemo(() => units?.items.filter(u => u.quality_status === 'UNINSPECTED' || (u.quality_status === 'PASS' && !u.package_id) || u.eligibility.blockers.some(b => b.code === 'CONTAINMENT_ACTIVE')) ?? [], [units])
  useEffect(() => { if (units && !units.items.some(u => u.id === unitId)) setUnitId(units.items.find(u => u.quality_status === 'UNINSPECTED')?.id ?? queue[0]?.id ?? '') }, [units])
  const unit = units?.items.find(u => u.id === unitId)
  const resultUnit = all?.items.find(u => u.id === result?.unit_id) ?? units?.items.find(u => u.id === result?.unit_id)
  const lot = lots?.items.find(l => l.id === lotId)
  const line = lines?.items.find(l => l.id === lot?.line_id)

  const submit = useAct(async () => {
    const f = new FormData(); f.set('unit_id', unitId); f.set('station_id', line?.station_id ?? ''); f.set('image', cap!.blob, 'capture.jpg'); if (cap!.fixture) f.set('fixture', cap!.fixture)
    return api.post<Inspection>('/inspections', f)
  })
  const holds = recent?.items.filter(i => i.verdict === 'HOLD' && !i.review && all?.items.find(u => u.id === i.unit_id)?.quality_status === 'HOLD') ?? []

  return (
    <div>
      <PageHeader eyebrow={`Station · ${line?.station_id ?? '—'}`} title="Inspection" description="Capture → quality gate → golden-reference comparison → deterministic verdict. A PASS issues a signed passport; anything uncertain becomes HOLD." />
      <div className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <Card>
          <CardHeader eyebrow="1 · Identify" title="Unit under inspection" />
          <div className="grid gap-3 p-4 sm:grid-cols-2">
            <Field label="Lot"><Select value={lotId} onChange={e => { setLotId(e.target.value); setResult(null) }}>{lots?.items.map(l => <option key={l.id} value={l.id}>{l.lot_code} · {lines?.items.find(x => x.id === l.line_id)?.name}</option>)}</Select></Field>
            <Field label="Serial" hint={unit && <span className="flex items-center gap-1.5">current: <Status value={unit.quality_status} /></span>}>
              <Select value={unitId} onChange={e => { setUnitId(e.target.value); setResult(null) }}>
                {units?.items.filter(u => u.quality_status !== 'REJECT').map(u => <option key={u.id} value={u.id}>{u.serial} — {u.quality_status.toLowerCase()}{u.eligibility.blockers.some(b => b.code === 'CONTAINMENT_ACTIVE') ? ' · contained' : ''}</option>)}
              </Select>
            </Field>
          </div>
          <div className="border-t border-line p-4">
            <div className="eyebrow mb-3">2 · Capture</div>
            <ImageCapture kind="part" fixtures={PART_FIXTURES} captured={cap} onCaptured={c => { setCap(c); setResult(null) }} />
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-line p-4">
            <div className="text-[11.5px] text-muted">{can('operator', 'quality_engineer', 'quality_manager') ? 'Server re-runs every check; the pre-check above is advisory.' : 'Switch to an Operator or QE to record inspections.'}</div>
            <Button variant="primary" size="lg" disabled={!cap || !unitId} loading={submit.isPending}
              onClick={() => submit.mutate(undefined, { onSuccess: r => { setResult(r); setCap(null); const next = units?.items.find(u => u.quality_status === 'UNINSPECTED' && u.id !== r.unit_id); if (next) setUnitId(next.id) } })}><ScanLine size={16} /> Inspect</Button>
          </div>
          {submit.error && <div className="px-4 pb-4"><ErrorBox error={submit.error} /></div>}
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="3 · Decision" title="Result" />
            <div className="p-4">{result ? <VerdictPanel insp={result} unit={resultUnit} /> :
              <div className="grid place-items-center gap-2 py-16 text-center text-muted"><Stamp size={22} /><div className="text-[13px]">Verdict appears here. Rules: gate fail or uncalibrated → HOLD · score ≥ reject → REJECT · score ≥ hold → HOLD · else PASS.</div></div>}</div>
          </Card>
          <Card>
            <CardHeader eyebrow="Quality engineer" title={`HOLD review queue (${holds.length})`} />
            {holds.length === 0 ? <div className="p-4 text-[13px] text-muted">Nothing waiting.</div> :
              <ul className="divide-y divide-line">{holds.map(i => (
                <li key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Mono>{all?.items.find(u => u.id === i.unit_id)?.serial}</Mono>
                  <span className="min-w-0 flex-1 truncate text-[12px] text-muted">{i.reasons.map(r => r.code).join(' · ')}</span>
                  <span className="text-[11px] text-muted">{ago(i.created_at)}</span>
                  <Button size="sm" disabled={!can('quality_engineer', 'quality_manager')} onClick={() => setReview(i)}>Review</Button>
                </li>
              ))}</ul>}
          </Card>
          <Card>
            <CardHeader eyebrow="Log" title="Recent inspections" right={<Link to="/spc" className="text-[12px] text-accent hover:underline">Drift monitor →</Link>} />
            <ul className="max-h-72 divide-y divide-line overflow-y-auto">{recent?.items.slice(0, 15).map(i => (
              <li key={i.id} className="flex items-center gap-3 px-4 py-2 text-[12.5px]">
                <span className={cx('size-2 rounded-full')} style={{ background: toneVar[toneOf(i.verdict)] }} />
                <Link to={`/units/${i.unit_id}`} className="hover:text-accent"><Mono>{all?.items.find(u => u.id === i.unit_id)?.serial}</Mono></Link>
                <Mono className="text-muted">{fmtScore(i.analysis.score)}</Mono>
                <span className="ml-auto text-[11px] text-muted">{i.created_by} · {ago(i.created_at)}</span>
              </li>
            ))}</ul>
          </Card>
        </div>
      </div>
      <ReviewModal insp={review} onClose={() => setReview(null)} />
    </div>
  )
}
