import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ReactFlow, Background, Handle, Position, type Node, type Edge, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Factory, Flame, Layers, Box, Package, Truck, Building2, BadgeCheck, Search, Siren, Timer } from 'lucide-react'
import { useAct, useGet } from '../api/hooks'
import { API_MODE, api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { NodeType, Recall, TraceGraph, TraceNode } from '../api/types'
import { Bucket } from '../components/domain'
import { Button, Card, CardHeader, Empty, ErrorBox, Field, Input, Mono, PageHeader, Pill, Spinner, Textarea, cx, toneOf, toneVar, fmtDateTime } from '../components/ui'

const COLS: NodeType[] = ['SUPPLIER', 'HEAT', 'LOT', 'UNIT', 'PASSPORT', 'PACKAGE', 'SHIPMENT', 'CUSTOMER']
const ICON: Record<string, React.ReactNode> = { SUPPLIER: <Factory size={13} />, HEAT: <Flame size={13} />, LOT: <Layers size={13} />, UNIT: <Box size={13} />, PASSPORT: <BadgeCheck size={13} />, PACKAGE: <Package size={13} />, SHIPMENT: <Truck size={13} />, CUSTOMER: <Building2 size={13} /> }

function TraceNodeView({ data }: NodeProps) {
  const n = data as unknown as TraceNode & { root?: boolean }
  const t = n.held ? 'reject' : toneOf(n.status)
  return (
    <div className={cx('w-[178px] rounded-lg border bg-surface px-2.5 py-2 shadow-sm', n.root && 'ring-2 ring-accent')} style={{ borderColor: n.held ? 'var(--reject)' : 'var(--line-strong)' }}>
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-line-strong" />
      <div className="flex items-center gap-1.5 text-muted">{ICON[n.type]}<span className="font-mono text-[9.5px] tracking-wider">{n.type}</span>{n.held && <span className="ml-auto font-mono text-[9px] font-semibold text-reject">HELD</span>}</div>
      <div className="mt-0.5 truncate font-mono text-[12px] font-semibold text-ink" title={n.label}>{n.label}</div>
      <div className="mt-0.5 flex items-center gap-1.5 truncate text-[10.5px] text-muted">
        {n.status && <span style={{ color: toneVar[t] }}>{n.status.replaceAll('_', ' ').toLowerCase()}</span>}
        {n.meta?.units != null && <span>{n.meta.units} units</span>}{n.meta?.city && <span>{n.meta.city}</span>}{n.meta?.grade && <span>{n.meta.grade}</span>}{n.meta?.line && <span>{n.meta.line}</span>}{n.meta?.vehicle && <span>{n.meta.vehicle}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-line-strong" />
    </div>
  )
}
const nodeTypes = { t: TraceNodeView }

function layout(g: TraceGraph): { nodes: Node[]; edges: Edge[] } {
  const used = COLS.filter(c => g.nodes.some(n => n.type === c))
  const nodes: Node[] = []
  for (const [ci, c] of used.entries()) {
    const col = g.nodes.filter(n => n.type === c)
    const gap = c === 'UNIT' || c === 'PASSPORT' ? 62 : 84
    col.forEach((n, i) => nodes.push({ id: n.id, type: 't', position: { x: ci * 220, y: i * gap - ((col.length - 1) * gap) / 2 }, data: { ...n, root: n.id === g.root.id } as any, draggable: false }))
  }
  const edges: Edge[] = g.edges.map(e => {
    const held = g.nodes.find(n => n.id === e.to)?.held
    return { id: `${e.from}-${e.to}`, source: e.from, target: e.to, label: (e as any).units > 1 ? `${(e as any).units}` : undefined, animated: e.type === 'BOUND_FOR', style: { stroke: held ? 'var(--reject)' : 'var(--line-strong)', strokeWidth: 1.5 }, labelStyle: { fontSize: 10, fontFamily: 'IBM Plex Mono', fill: 'var(--muted)' }, labelBgStyle: { fill: 'var(--surface)' } }
  })
  return { nodes, edges }
}


function RecallPanel({ g }: { g: TraceGraph }) {
  const { can } = useAuth()
  const [reason, setReason] = useState('')
  const scope = { type: g.root.type, id: g.root.id }
  const preview = useAct(() => api.post<Recall>('/recalls/preview', { scope }))
  const exec = useAct(() => api.post<Recall>('/recalls', { scope, reason }))
  useEffect(() => { preview.reset(); exec.reset() }, [g.root.id])
  const r = exec.data ?? preview.data
  return (
    <Card className={cx(exec.data && 'border-reject/50')}>
      <CardHeader eyebrow="Blast radius" title={<span className="flex items-center gap-2"><Siren size={15} className="text-reject" /> Recall {g.root.label}</span>}
        right={r && <span className="flex items-center gap-1 text-[11px] text-muted"><Timer size={11} /> {r.query_ms} ms{API_MODE === 'mock' ? ' (in-browser mock)' : ' (PostgreSQL)'}</span>} />
      <div className="space-y-4 p-4">
        {!r && <div className="text-[12.5px] text-muted">Preview computes the exact affected set from recorded genealogy and movement events, without writing anything.</div>}
        {r && <>
          {exec.data && <div className="rise flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-reject/10 px-3 py-2 text-[12.5px] text-reject"><span className="flex-1">Quarantine executed by {r.executed_by} · {fmtDateTime(r.executed_at)} · {r.audit_event_ids.length} audit events written</span><Link to={`/recalls/${r.id}`} className="font-medium underline underline-offset-2">Physical tasks & release →</Link></div>}
          <div className="grid grid-cols-2 gap-2">
            <Bucket label="Units" value={r.affected.units} tone="neutral" note={`${r.affected.passports_held} passports → HELD`} />
            <Bucket label="Blocked at plant" value={r.affected.blocked_at_plant.reduce((n, x) => n + x.units, 0)} tone="reject" note={`${r.affected.blocked_at_plant.length} packages · gate will refuse`} />
            <Bucket label="In transit" value={r.affected.in_transit.reduce((n, x) => n + x.units, 0)} tone="hold" note="notify & intercept — cannot block" />
            <Bucket label="Delivered" value={r.affected.delivered.reduce((n, x) => n + x.units, 0)} tone="info" note="customer notification only" />
          </div>
          <ul className="space-y-1.5 text-[12px]">
            {r.affected.blocked_at_plant.map(b => <li key={b.package_id} className="flex items-center gap-2"><Pill tone="reject">BLOCK</Pill><Mono>{b.label}</Mono><span className="text-muted">{b.units} units at plant</span></li>)}
            {r.affected.loaded_not_departed.map(b => <li key={b.shipment_id} className="flex items-center gap-2"><Pill tone="reject">UNLOAD</Pill><Mono>{b.code}</Mono><span className="text-muted">{b.units} units loaded, not departed</span></li>)}
            {r.affected.in_transit.map(b => <li key={b.shipment_id} className="flex items-center gap-2"><Pill tone="hold">INTERCEPT</Pill><Mono>{b.code}</Mono><span className="truncate text-muted">{b.vehicle_no} → {b.customer} · {b.units}u</span></li>)}
            {r.affected.delivered.map(b => <li key={b.shipment_id} className="flex items-center gap-2"><Pill tone="info">NOTIFY</Pill><Mono>{b.code}</Mono><span className="truncate text-muted">{b.customer} · {b.units}u</span></li>)}
          </ul>
          <div className="rounded-md bg-surface-2 px-3 py-2 text-[11px] text-muted">Evidence: <Mono>{r.evidence.genealogy_query}</Mono> as of {fmtDateTime(r.evidence.as_of)}. Location comes only from accepted scans and movement events.</div>
        </>}
        {preview.error && <ErrorBox error={preview.error} />}{exec.error && <ErrorBox error={exec.error} />}
        {!exec.data && <>
          <Button className="w-full" disabled={!can('quality_engineer', 'quality_manager')} loading={preview.isPending} onClick={() => preview.mutate(undefined)}>Preview impact</Button>
          {preview.data && <>
            <Field label="Quarantine reason (audit)"><Textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. Supplier reported out-of-spec nodularity on heat H-7781" className="min-h-14" /></Field>
            <Button variant="danger" className="w-full" disabled={!can('quality_manager') || !reason.trim()} loading={exec.isPending} onClick={() => exec.mutate(undefined)}><Siren size={14} /> Quarantine {r?.affected.units} units atomically</Button>
            {!can('quality_manager') && <div className="text-[11px] text-muted">Execution requires a Quality Manager.</div>}
          </>}
        </>}
      </div>
    </Card>
  )
}

export default function Trace() {
  const [sp] = useSearchParams(); const nav = useNavigate()
  const q = sp.get('q') ?? 'H-7781'
  const [input, setInput] = useState(q)
  useEffect(() => setInput(q), [q])
  const { data: g, error, isLoading } = useGet<TraceGraph>(`/trace?q=${encodeURIComponent(q)}`)
  const flow = useMemo(() => (g ? layout(g) : null), [g])
  return (
    <div>
      <PageHeader eyebrow="Genealogy · recall" title="Trace & recall" description="From any heat, lot, serial or package: the exact lineage to packages, shipments and consignees — and an atomic quarantine of what can still be stopped." />
      <form onSubmit={e => { e.preventDefault(); nav(`/trace?q=${encodeURIComponent(input)}`) }} className="mb-4 flex max-w-xl gap-2">
        <div className="relative flex-1"><Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" /><Input value={input} onChange={e => setInput(e.target.value)} className="pl-8 font-mono" /></div>
        <Button variant="primary">Trace</Button>
      </form>
      <div className="mb-6 flex flex-wrap gap-1.5 text-[11.5px]">{['H-7781', 'H-7790', 'L-2611-A', 'PKG-0005', 'BRK-2611A-0003'].map(x => <button key={x} onClick={() => nav(`/trace?q=${x}`)} className={cx('rounded-md border px-2 py-0.5 font-mono', x === q ? 'border-accent text-accent' : 'border-line text-muted hover:text-ink')}>{x}</button>)}</div>
      {isLoading && <Spinner />}
      {error && <ErrorBox error={error} />}
      {g && flow && (
        <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
              {[['Units', g.summary.units], ['Packages', g.summary.packages], ['At plant', g.summary.at_plant], ['Loaded', g.summary.loaded], ['In transit', g.summary.in_transit], ['Delivered', g.summary.delivered]].map(([k, v]) => (
                <div key={String(k)} className="rounded-lg border border-line bg-surface px-3 py-2"><div className="eyebrow">{k}</div><div className="num text-[18px]">{v}</div></div>
              ))}
            </div>
            <Card className="h-[540px] overflow-hidden">
              {g.nodes.length ? (
                <ReactFlow nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.15 }} proOptions={{ hideAttribution: true }} nodesConnectable={false} minZoom={0.3}>
                  <Background gap={24} color="var(--line)" />
                </ReactFlow>
              ) : <Empty title="No lineage" />}
            </Card>
          </div>
          <RecallPanel g={g} />
        </div>
      )}
    </div>
  )
}
