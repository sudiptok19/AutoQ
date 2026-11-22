import { useState } from 'react'
import { Link2, ShieldCheck, ShieldX } from 'lucide-react'
import { useGet } from '../api/hooks'
import { api } from '../api/client'
import type { AuditEvent } from '../api/types'
import { Button, Card, CardHeader, Mono, PageHeader, Table, fmtDateTime, Pill } from '../components/ui'

export default function Audit() {
  const { data } = useGet<{ items: AuditEvent[] }>('/audit', { refetchInterval: 6000 })
  const [v, setV] = useState<any>(null); const [busy, setBusy] = useState(false)
  const verify = async () => { setBusy(true); setV(await api.get('/audit/verify')); setBusy(false) }
  return (
    <div>
      <PageHeader eyebrow="Append-only · hash-chained" title="Audit chain"
        description="Every state change with actor, reason and a SHA-256 link to the previous event. Detects edits and reordering of recorded history; it cannot prove that nothing was withheld before recording."
        actions={<Button onClick={verify} loading={busy}><Link2 size={14} /> Verify chain</Button>} />
      {v && <div className="rise mb-4 flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2 text-[13px]">{v.ok ? <ShieldCheck size={16} className="text-pass" /> : <ShieldX size={16} className="text-reject" />}{v.ok ? <>Chain intact · {v.length} events · head <Mono>{v.head.slice(0, 16)}…</Mono></> : <>Broken at event #{v.broken_at}</>}</div>}
      <Card>
        <CardHeader title={`${data?.items.length ?? 0} most recent events`} />
        <Table head={['#', 'Time', 'Actor', 'Action', 'Entity', 'Reason / data', 'Hash']}>
          {data?.items.map(e => (
            <tr key={e.id} className="align-top hover:bg-surface-2/50">
              <td className="num text-muted">{e.seq}</td>
              <td className="whitespace-nowrap text-muted">{fmtDateTime(e.at)}</td>
              <td><Mono>{e.actor}</Mono></td>
              <td><Pill tone={e.action.includes('BLOCKED') || e.action.includes('RECALL') || e.action.includes('REVOK') ? 'reject' : e.action.includes('CONTAIN') || e.action.includes('FOUR_M') ? 'hold' : 'neutral'}>{e.action}</Pill></td>
              <td><Mono className="text-muted">{e.entity_type}:{e.entity_id.slice(-6)}</Mono></td>
              <td className="max-w-sm text-[12px] text-ink-2">{e.reason && <div>“{e.reason}”</div>}{e.data && Object.keys(e.data).length > 0 && <div className="font-mono text-[10.5px] text-muted">{Object.entries(e.data).map(([k, x]) => `${k}=${x}`).join(' ')}</div>}</td>
              <td><Mono className="text-[10.5px] text-muted" >{e.hash.slice(0, 10)}…</Mono></td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  )
}
