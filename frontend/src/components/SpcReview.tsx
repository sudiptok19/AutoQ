import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ShieldAlert, Siren } from 'lucide-react'
import { useAct } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import { Button, Card, CardHeader, ErrorBox, Field, Honesty, Mono, Pill, Status, Textarea, fmtDateTime, type Tone } from './ui'

/** Server view of an A3 review: Astra's investigation + recommendation, frozen, plus the human decision. */
export interface SpcReviewView {
  id: string; alert_id: string | null; target_inspection_id: string; target_serial: string | null
  status: 'PROPOSED' | 'APPROVED' | 'REJECTED'
  recommendation_status: 'REVIEW_RECOMMENDED' | 'SCOPE_UNRESOLVED' | 'NO_SIGNAL' | 'INSUFFICIENT_EVIDENCE'
  investigation_status: 'SIGNAL' | 'NO_SIGNAL' | 'INSUFFICIENT_EVIDENCE'
  qualification_version: string; recommendation_version: string
  issue_counts: Record<string, number>
  recommendation: {
    confirmed_observations: { observation_id: string; inspection_id: string; unit_id: string; rules: string[] }[]
    proposed_lots: { lot_id: string; recorded_unit_ids: string[]; basis: string[]; supporting_inspection_ids: string[] }[]
    unresolved: { entity_type: string; entity_id: string; reason: string }[]
    excluded: { entity_type: string; entity_id: string; reason: string }[]
    related_four_m: { event_id: string; category: string; effective_at: string }[]
    limitations: string[]; actions: string[]
  }
  read_context: { events_read: number; lots_read: string[]; units_read: number; exposure_links_read: number; note: string; as_of: string }
  lot_codes: Record<string, string>; approved_lot_ids: string[]; recall_ids: Record<string, string>
  created_by: string; created_at: string; decided_by: string | null; decided_at: string | null; decision_reason: string | null
}

const REC: Record<SpcReviewView['recommendation_status'], { tone: Tone; text: string }> = {
  REVIEW_RECOMMENDED: { tone: 'hold', text: 'Review recommended for the lots below. Nothing is held until a quality manager approves.' },
  SCOPE_UNRESOLVED: { tone: 'hold', text: 'A signal exists, but the evidence cannot bound which lots were exposed. Resolve the open items first.' },
  NO_SIGNAL: { tone: 'pass', text: 'The qualified evidence shows no drift signal at this inspection.' },
  INSUFFICIENT_EVIDENCE: { tone: 'neutral', text: 'Not enough qualified evidence to investigate. Synthetic, uncalibrated or re-inspected data is excluded on purpose.' },
}
const human = (s: string) => s.replaceAll('_', ' ').toLowerCase()

export function SpcReviewPanel({ r }: { r: SpcReviewView }) {
  const { can } = useAuth()
  const rec = r.recommendation
  const [picked, setPicked] = useState<string[]>(() => rec.proposed_lots.map(l => l.lot_id))
  const [reason, setReason] = useState('')
  const decide = useAct((decision: 'APPROVE' | 'REJECT') => api.post(`/spc-reviews/${r.id}/decision`, { decision, reason, lot_ids: decision === 'APPROVE' ? picked : [] }))
  const quarantine = useAct((lot_id: string) => api.post(`/spc-reviews/${r.id}/quarantine`, { lot_id }))
  const code = (id: string) => r.lot_codes[id] ?? id
  const m = REC[r.recommendation_status]
  return (
    <Card className="mb-6">
      <CardHeader eyebrow={`SPC containment review · ${r.qualification_version} / ${r.recommendation_version}`}
        title={<span className="flex items-center gap-2"><ShieldAlert size={15} /> Scope for {r.target_serial ?? r.target_inspection_id}</span>}
        right={<span className="flex items-center gap-2"><Pill tone={m.tone}>{human(r.recommendation_status)}</Pill><Status value={r.status} /></span>} />
      <div className="space-y-4 p-4 text-[12.5px]">
        <div className="rounded-lg bg-surface-2 px-3 py-2 text-ink-2">{m.text} <span className="text-muted">A contributing observation confirms a measurement, not a defect or its cause.</span></div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-muted">
          <span>Investigation <b className="text-ink">{human(r.investigation_status)}</b></span>
          <span>{r.read_context.events_read} inspections read</span><span>{r.read_context.lots_read.length} lots · {r.read_context.units_read} units in genealogy</span>
          <span>{r.read_context.exposure_links_read} independent exposure links</span><span>as of {fmtDateTime(r.read_context.as_of)}</span>
        </div>
        {Object.keys(r.issue_counts).length > 0 && (
          <div><div className="eyebrow mb-1">Why observations were excluded or flagged</div>
            <div className="flex flex-wrap gap-1.5">{Object.entries(r.issue_counts).map(([k, n]) => <Pill key={k} tone="neutral">{human(k)} · {n}</Pill>)}</div>
            {r.issue_counts.NON_PRODUCTION_SOURCE && <div className="mt-1.5"><Honesty kind="synthetic">SYNTHETIC DATA NEVER SUPPORTS CONTAINMENT</Honesty></div>}
          </div>
        )}
        {rec.confirmed_observations.length > 0 && (
          <div><div className="eyebrow mb-1">Confirmed contributing observations ({rec.confirmed_observations.length})</div>
            <div className="flex flex-wrap gap-1.5">{rec.confirmed_observations.slice(0, 12).map(o => <Link key={o.observation_id} to={`/units/${o.unit_id}`}><Pill tone="info"><Mono>{o.inspection_id.slice(-6)}</Mono> · {o.rules.join('+')}</Pill></Link>)}
              {rec.confirmed_observations.length > 12 && <span className="text-muted">+{rec.confirmed_observations.length - 12} more</span>}</div>
          </div>
        )}
        {rec.proposed_lots.length > 0 && (
          <div><div className="eyebrow mb-1">Proposed lot reviews</div>
            <ul className="divide-y divide-line rounded-lg border border-line">{rec.proposed_lots.map(l => (
              <li key={l.lot_id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                {r.status === 'PROPOSED' && can('quality_manager') && <input type="checkbox" checked={picked.includes(l.lot_id)} aria-label={`approve ${code(l.lot_id)}`}
                  onChange={e => setPicked(p => e.target.checked ? [...p, l.lot_id] : p.filter(x => x !== l.lot_id))} />}
                <Mono className="font-semibold">{code(l.lot_id)}</Mono>
                {l.basis.map(b => <Pill key={b} tone="hold">{human(b)}</Pill>)}
                <span className="text-muted">{l.recorded_unit_ids.length} recorded units · {l.supporting_inspection_ids.length} supporting inspections</span>
                {r.status === 'APPROVED' && r.approved_lot_ids.includes(l.lot_id) && (r.recall_ids[l.lot_id]
                  ? <Link to={`/recalls/${r.recall_ids[l.lot_id]}`} className="ml-auto text-accent underline underline-offset-2">quarantined →</Link>
                  : <Button size="sm" variant="danger" className="ml-auto" disabled={!can('quality_manager')} loading={quarantine.isPending && quarantine.variables === l.lot_id}
                      onClick={() => quarantine.mutate(l.lot_id)}><Siren size={13} /> Quarantine {code(l.lot_id)}</Button>)}
              </li>
            ))}</ul>
          </div>
        )}
        {rec.unresolved.length > 0 && (
          <div><div className="eyebrow mb-1">Unresolved exposure (stays visible, never auto-included)</div>
            <ul className="space-y-0.5">{rec.unresolved.map((u, i) => <li key={i}><Mono>{u.entity_type === 'LOT' ? code(u.entity_id) : u.entity_id}</Mono> <span className="text-muted">· {human(u.reason)}</span></li>)}</ul>
          </div>
        )}
        {rec.related_four_m.length > 0 && <div className="text-muted">Related 4M changes (context, not cause): {rec.related_four_m.map(f => `${f.category.toLowerCase()} ${fmtDateTime(f.effective_at)}`).join(' · ')}</div>}
        <div className="text-[11px] text-muted">Limitations: {rec.limitations.map(human).join(' · ')}. {r.read_context.note}</div>

        {r.status === 'PROPOSED' ? (
          <div className="space-y-2 border-t border-line pt-3">
            <Field label="Decision reason (audit)"><Textarea value={reason} onChange={e => setReason(e.target.value)} className="min-h-14" placeholder="e.g. Wear trend confirmed on the OP-20 insert; review the whole lot" /></Field>
            {(decide.error || quarantine.error) && <ErrorBox error={decide.error || quarantine.error} />}
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={!can('quality_manager') || r.recommendation_status !== 'REVIEW_RECOMMENDED' || !picked.length || reason.trim().length < 10}
                loading={decide.isPending && decide.variables === 'APPROVE'} onClick={() => decide.mutate('APPROVE')}>Approve {picked.length} lot{picked.length === 1 ? '' : 's'} for containment</Button>
              <Button disabled={!can('quality_manager') || reason.trim().length < 10} loading={decide.isPending && decide.variables === 'REJECT'} onClick={() => decide.mutate('REJECT')}>Reject</Button>
            </div>
            {!can('quality_manager') && <div className="text-[11px] text-muted">The containment decision is a quality manager's.</div>}
          </div>
        ) : (
          <div className="border-t border-line pt-3 text-ink-2">{r.status === 'APPROVED' ? 'Approved' : 'Rejected'} by {r.decided_by} · {fmtDateTime(r.decided_at)} — “{r.decision_reason}”
            {quarantine.error && <div className="mt-2"><ErrorBox error={quarantine.error} /></div>}</div>
        )}
      </div>
    </Card>
  )
}
