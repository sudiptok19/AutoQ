import { useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { BadgeCheck, Download, FileText, Paperclip, ShieldCheck } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { API_MODE, api, evidenceSrc } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Report8D, ReportItem, ReportItemKind, UploadedEvidence } from '../api/types'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Mono, PageHeader, Pill, Status, Textarea, ago, cx, fmtDateTime, type Tone } from '../components/ui'

const KIND: Record<ReportItemKind, { label: string; tone: Tone; hint: string }> = {
  FACT: { label: 'Fact', tone: 'pass', hint: 'Taken from a recorded event; the evidence chip links to it.' },
  HYPOTHESIS: { label: 'Hypothesis', tone: 'hold', hint: 'Plausible but not verified.' },
  PROPOSED: { label: 'Proposed', tone: 'info', hint: 'A suggested action, not yet agreed.' },
  PENDING_HUMAN: { label: 'Needs a person', tone: 'neutral', hint: 'The system cannot know this. A person must supply it before approval.' },
}
const SOURCE_LABEL: Record<string, string> = { RECALL: 'Recall', FOUR_M: '4M change point', ALERT: 'Alert' }

/** Where an evidence chip should take the reader. Unknown types render as plain text, never a guessed link. */
function evidenceHref(e: ReportItem['evidence'][number]): string | null {
  if (e.sha256) return evidenceSrc(`/api/v1/evidence/${e.sha256}`)
  switch (e.type) {
    case 'RECALL': return `/recalls/${e.id}`
    case 'UNIT': return `/units/${e.id}`
    case 'PACKAGE': case 'HEAT': case 'LOT': return `/trace?q=${encodeURIComponent(e.label.split(' ')[0])}`
    case 'FOUR_M': case 'CONTAINMENT': return '/containment'
    case 'ALERT': return '/spc'
    case 'SHIPMENT': return '/dispatch'
    default: return null
  }
}

function Item({ it }: { it: ReportItem }) {
  const k = KIND[it.kind] ?? KIND.PENDING_HUMAN
  return (
    <li className={cx('flex gap-3 px-4 py-2.5', it.kind === 'PENDING_HUMAN' && 'bg-surface-2/60')}>
      <span title={k.hint} className="w-28 shrink-0 pt-0.5"><Pill tone={k.tone}>{k.label}</Pill></span>
      <div className="min-w-0 flex-1">
        <div className={cx('text-[13px]', it.kind === 'PENDING_HUMAN' && 'italic text-muted')}>{it.text}</div>
        {!!it.evidence?.length && <div className="mt-1 flex flex-wrap gap-1.5">{it.evidence.map((e, i) => {
          const href = evidenceHref(e)
          const chip = <span className="inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[11px] text-muted"><Paperclip size={10} />{e.label || `${e.type} ${e.id}`}</span>
          return href ? (href.startsWith('/') && !href.startsWith('/api') ? <Link key={i} to={href} className="hover:text-ink">{chip}</Link> : <a key={i} href={href} target="_blank" rel="noreferrer">{chip}</a>) : <span key={i}>{chip}</span>
        })}</div>}
      </div>
    </li>
  )
}

function Approve({ rep }: { rep: Report8D }) {
  const { can } = useAuth()
  const file = useRef<HTMLInputElement>(null)
  const [picked, setPicked] = useState<File | null>(null)
  const [reason, setReason] = useState('')
  const pending = rep.sections.flatMap(s => s.items).filter(i => i.kind === 'PENDING_HUMAN').length
  const approve = useAct(async () => {
    const f = new FormData(); f.set('file', picked!, picked!.name); f.set('purpose', 'REVIEW_RECORD')
    const ev = await api.post<UploadedEvidence>('/evidence', f)
    return api.post<Report8D>(`/reports/${rep.id}/approve`, { reason, review_evidence_ids: [ev.sha256] })
  })
  if (rep.status === 'APPROVED') return (
    <Card className="border-pass/40">
      <CardHeader eyebrow="Closure" title={<span className="flex items-center gap-2"><ShieldCheck size={15} className="text-pass" /> Approved</span>} />
      <div className="p-4 text-[12.5px] text-ink-2">By {rep.approved_by} · {fmtDateTime(rep.approved_at)}. The approved revision is a new immutable snapshot; the draft stays on record.</div>
    </Card>
  )
  return (
    <Card>
      <CardHeader eyebrow="Closure" title={<span className="flex items-center gap-2"><BadgeCheck size={15} /> Approve with review evidence</span>} />
      <div className="space-y-3 p-4">
        {pending > 0 && <div className="rounded-lg bg-hold/10 px-3 py-2 text-[12px] text-ink-2"><b>{pending} item{pending > 1 ? 's' : ''} still need a person.</b> Approval records that a review happened. Attach the minutes that answer them.</div>}
        <Field label="Review record (PDF, JPEG or PNG)" hint="Stored as UPLOAD evidence; its SHA-256 is cited by the approved revision.">
          <input ref={file} type="file" accept="application/pdf,image/jpeg,image/png" className="hidden" onChange={e => setPicked(e.target.files?.[0] ?? null)} />
          <Button size="sm" onClick={() => file.current?.click()}><Paperclip size={13} /> {picked ? picked.name : 'Choose file…'}</Button>
        </Field>
        <Field label="Approval reason (audit)"><Textarea value={reason} onChange={e => setReason(e.target.value)} className="min-h-14" placeholder="e.g. Cross-functional review 24 Nov: supplier nodularity root cause confirmed by lab retest" /></Field>
        {approve.error && <ErrorBox error={approve.error} />}
        <Button variant="primary" className="w-full" disabled={!can('quality_manager') || !picked || !reason.trim()} loading={approve.isPending} onClick={() => approve.mutate(undefined)}>Approve 8D</Button>
        {!can('quality_manager') && <div className="text-[11px] text-muted">Approval requires a Quality Manager.</div>}
      </div>
    </Card>
  )
}

function Pdf({ rep }: { rep: Report8D }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<unknown>(null)
  const get = async () => {
    setBusy(true); setErr(null)
    try {
      const url = URL.createObjectURL(await api.blob(`/reports/${rep.id}/pdf`))
      const a = document.createElement('a'); a.href = url; a.download = `8D-${rep.id}-${rep.revision_id}.pdf`; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (e) { setErr(e) } finally { setBusy(false) }
  }
  if (API_MODE === 'mock') return <div className="text-[11.5px] text-muted">PDF rendering runs on the live backend.</div>
  return <><Button size="sm" loading={busy} onClick={get}><Download size={13} /> PDF of this revision</Button>{!!err && <ErrorBox error={err} />}</>
}

function ReportDetail({ rep }: { rep: Report8D }) {
  const sourceHref = rep.source_type === 'RECALL' ? `/recalls/${rep.source_id}` : rep.source_type === 'FOUR_M' ? '/containment' : '/spc'
  return (
    <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
      <Card>
        <CardHeader eyebrow={`8D · ${SOURCE_LABEL[rep.source_type] ?? rep.source_type}`} title={rep.meta?.source_label ?? rep.source_id} right={<Status value={rep.status} />} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5 text-[11.5px] text-muted">
          <span>Generator <Mono>{rep.generator}</Mono></span><span>Revision {rep.revisions}</span><span>Facts as of {fmtDateTime(rep.as_of)}</span>
          <Link to={sourceHref} className="underline underline-offset-2">source record →</Link>
        </div>
        <div className="border-b border-line bg-surface-2/60 px-4 py-2 text-[11.5px] text-muted">Drafted from a deterministic template. It states only what the system recorded; root cause and actions stay with people until a Quality Manager approves.</div>
        {rep.sections.map(s => (
          <section key={s.key} className="border-b border-line last:border-0">
            <div className="flex items-baseline gap-2 px-4 pt-3"><Mono className="text-[12px] text-accent">{s.key}</Mono><h3 className="text-[13.5px] font-semibold">{s.title}</h3></div>
            <ul className="py-1.5">{s.items.map((it, i) => <Item key={i} it={it} />)}</ul>
          </section>
        ))}
      </Card>
      <div className="space-y-5">
        <Approve rep={rep} />
        <Card>
          <CardHeader eyebrow="Integrity" title="Snapshot" />
          <div className="space-y-2.5 p-4 text-[12px]">
            <div className="text-muted">SHA-256 of this revision's canonical content</div>
            <Mono className="block break-all text-[11px]">{rep.snapshot_sha256}</Mono>
            {rep.pdf_sha256 && <div className="text-muted">Last PDF <Mono className="break-all text-[11px]">{rep.pdf_sha256.slice(0, 16)}…</Mono></div>}
            <Pdf rep={rep} />
          </div>
        </Card>
      </div>
    </div>
  )
}

export default function Reports() {
  const { id } = useParams()
  const { data, isLoading } = useGet<{ items: Report8D[] }>('/reports')
  const items = data?.items ?? []
  const listed = items.find(r => r.id === id) ?? (id ? undefined : items[0])
  const { data: fresh } = useGet<Report8D>(listed ? `/reports/${listed.id}` : null)
  const selected = fresh?.id === listed?.id ? fresh : listed
  return (
    <div>
      <PageHeader eyebrow="8D problem solving" title="Reports"
        description="Evidence-linked 8D drafts from recalls, 4M change points and alerts. Every statement is labelled: recorded fact, hypothesis, proposal, or something a person still has to supply." />
      {!isLoading && !items.length
        ? <Card><Empty icon={<FileText size={20} />} title="No 8D reports yet">Draft one from a <Link className="underline underline-offset-2" to="/recalls">recall</Link> or a <Link className="underline underline-offset-2" to="/containment">4M change point</Link>.</Empty></Card>
        : <div className="grid gap-5 xl:grid-cols-[280px_1fr]">
            <Card className="self-start">
              <CardHeader eyebrow="Register" title={`${items.length} report${items.length === 1 ? '' : 's'}`} />
              <ul className="divide-y divide-line">{items.map(r => (
                <li key={r.id}><Link to={`/reports/${r.id}`} className={cx('block px-4 py-3 hover:bg-surface-2', selected?.id === r.id && 'bg-accent-soft')}>
                  <div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate text-[13px] font-medium">{r.meta?.source_label ?? r.source_id}</span><Status value={r.status} /></div>
                  <div className="mt-0.5 text-[11.5px] text-muted">{SOURCE_LABEL[r.source_type] ?? r.source_type} · rev {r.revisions} · {ago(r.created_at)}</div>
                </Link></li>
              ))}</ul>
            </Card>
            {selected ? <ReportDetail rep={selected} /> : id && !isLoading ? <Card><Empty title="Report not found" /></Card> : null}
          </div>}
    </div>
  )
}
