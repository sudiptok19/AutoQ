import { useParams, Link } from 'react-router-dom'
import { useState } from 'react'
import { KeyRound, ShieldCheck, ShieldX, Fingerprint } from 'lucide-react'
import { useGet } from '../api/hooks'
import type { Inspection, Passport, Unit } from '../api/types'
import { Button, Card, CardHeader, Honesty, Mono, PageHeader, Spinner, Status, fmtDateTime, Pill } from '../components/ui'
import { EvidenceImage, QrLabel, Reasons } from '../components/domain'
import { verifyToken, decodeTokenUnsafe, forgeToken, type VerifyResult } from '../lib/token'
import { api } from '../api/client'

type UnitDetail = Unit & { inspections: Inspection[]; passport: Passport | null }

export function PassportVerify({ token }: { token: string }) {
  const [res, setRes] = useState<VerifyResult | null>(null); const [busy, setBusy] = useState(false)
  const run = async (t: string) => { setBusy(true); const { keys } = await api.get<{ keys: any[] }>('/keys'); setRes(await verifyToken(t, keys)); setBusy(false) }
  const tamper = () => run(forgeToken(token, p => { p.serial = String(p.serial).replace(/\d$/, d => String((+d + 1) % 10)) }))
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => run(token)} loading={busy}><KeyRound size={13} /> Verify signature locally</Button>
        <Button size="sm" variant="ghost" onClick={tamper}>Try a forged copy (serial edited)</Button>
      </div>
      {res && (
        <div className="rise flex items-start gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[12.5px]">
          {res.ok ? <ShieldCheck size={16} className="text-pass" /> : <ShieldX size={16} className="text-reject" />}
          <div>
            <div className="font-medium">{res.ok ? `Valid Ed25519 signature · kid ${res.kid}` : <Mono className="text-reject">{res.code}</Mono>}</div>
            <div className="text-[11.5px] text-muted">Verified in this browser against the public key set. A valid signature proves the data was issued by AutoQ — not that this label is on the original physical part.</div>
          </div>
        </div>
      )}
    </div>
  )
}

export default function UnitPage() {
  const { id } = useParams()
  const { data: u } = useGet<UnitDetail>(`/units/${id}`)
  if (!u) return <Spinner />
  const payload = u.passport ? decodeTokenUnsafe(u.passport.token) : null
  return (
    <div>
      <PageHeader eyebrow="Serialized unit" title={<Mono className="text-[22px] font-semibold">{u.serial}</Mono>}
        description={<span className="flex items-center gap-2"><Status value={u.quality_status} />{u.eligibility.dispatch_eligible ? <Pill tone="pass">DISPATCH-ELIGIBLE</Pill> : <Pill tone="hold">DISPATCH-BLOCKED</Pill>}</span>}
        actions={<><Link to={`/trace?q=${u.serial}`}><Button>Trace genealogy</Button></Link>{(u.quality_status === 'UNINSPECTED' || u.quality_status === 'PASS') && <Link to={`/inspect?unit=${u.id}`}><Button variant="primary">Inspect</Button></Link>}</>} />
      <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="Computed, never stored" title="Dispatch eligibility" />
            <div className="p-4"><Reasons reasons={u.eligibility.blockers} empty={<div className="text-[13px] text-pass">All conditions met: PASS · passport active · no containment · not quarantined.</div>} /></div>
          </Card>
          <Card>
            <CardHeader eyebrow="Evidence" title={`Inspections (${u.inspections.length})`} />
            {u.inspections.length === 0 ? <div className="p-6 text-[13px] text-muted">Not inspected yet.</div> :
              <div className="divide-y divide-line">{u.inspections.map(i => (
                <div key={i.id} className="grid gap-4 p-4 md:grid-cols-[220px_1fr]">
                  <EvidenceImage url={i.evidence.image_url} heatmap={i.evidence.heatmap_url} regions={i.analysis.regions} />
                  <div className="space-y-2 text-[12.5px]">
                    <div className="flex flex-wrap items-center gap-2"><Status value={i.verdict} /><Mono className="text-muted">{i.id}</Mono>{i.synthetic && <Honesty kind="synthetic" />}{!i.analysis.calibrated && <Honesty kind="uncalibrated" />}</div>
                    <div className="text-muted">{fmtDateTime(i.created_at)} · {i.created_by} · {i.analysis.method} <Mono>{i.analysis.method_version}</Mono></div>
                    <div>Anomaly score <Mono className="font-semibold">{i.analysis.score ?? '—'}</Mono> <span className="text-muted">(hold ≥ {i.analysis.hold_threshold}, reject ≥ {i.analysis.reject_threshold}{i.analysis.threshold_tightened ? ', tightened by containment' : ''})</span></div>
                    <Reasons reasons={i.reasons} />
                    {i.review && <div className="rounded-md bg-surface-2 px-2.5 py-1.5 text-[12px]">Review: <b>{i.review.action}</b> by {i.review.by} — “{i.review.reason}”</div>}
                    <div className="text-[11px] text-muted">image <Mono>sha256:{i.evidence.image_sha256.slice(0, 16)}…</Mono></div>
                  </div>
                </div>
              ))}</div>}
          </Card>
        </div>
        <div className="space-y-6">
          <Card>
            <CardHeader eyebrow="Digital quality passport" title={u.passport ? u.passport.id : 'Not issued'} right={u.passport && <Status value={u.passport.status} />} />
            {!u.passport ? <div className="p-4 text-[13px] text-muted">Issued automatically when an inspection results in PASS.</div> :
              <div className="space-y-4 p-4">
                <QrLabel token={u.passport.token} title={u.serial} sub={`${payload?.lot} · ${payload?.part}`} />
                <PassportVerify token={u.passport.token} />
                <div>
                  <div className="eyebrow mb-1.5 flex items-center gap-1.5"><Fingerprint size={11} /> Signed payload (immutable)</div>
                  <pre className="max-h-64 overflow-auto rounded-lg border border-line bg-surface-2 p-3 font-mono text-[11px] leading-relaxed">{JSON.stringify(payload, null, 2)}</pre>
                  <div className="mt-1.5 text-[11px] text-muted">Status ({u.passport.status}) lives outside the signature — holds and revocations change it without re-signing.</div>
                </div>
              </div>}
          </Card>
        </div>
      </div>
    </div>
  )
}
