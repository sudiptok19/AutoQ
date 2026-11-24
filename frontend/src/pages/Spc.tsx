import { useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Scatter } from 'recharts'
import { useAct, useGet } from '../api/hooks'
import { api } from '../api/client'
import { useAuth } from '../auth/auth'
import type { Line as LineT, Part, SpcSeries } from '../api/types'
import { Button, Card, CardHeader, Honesty, Mono, PageHeader, Segmented, Status } from '../components/ui'

const axis = { fontSize: 10.5, fill: 'var(--muted)', fontFamily: 'IBM Plex Mono' }
function Tip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return <div className="rounded-lg border border-line bg-surface px-2.5 py-1.5 text-[11.5px] shadow-lg"><div className="num">#{p.i} · score {p.score}</div><div className="num text-muted">z {p.z} · CUSUM {p.cusum}</div>{p.synthetic && <div className="text-muted">synthetic</div>}</div>
}

const fmt3 = (n: number | null | undefined) => (n == null ? '—' : Number(n).toFixed(3))

export default function Spc() {
  const { can } = useAuth()
  const { data: lines } = useGet<{ items: LineT[] }>('/lines')
  const { data: parts } = useGet<{ items: Part[] }>('/parts')
  const [picked, setLine] = useState<string | null>(null)
  const line = picked ?? lines?.items[0]?.id ?? null
  const part = parts?.items[0]?.id
  const { data: s } = useGet<SpcSeries>(line && part ? `/spc/series?part_id=${part}&line_id=${line}&method_version=gd-0.3` : null, { refetchInterval: 8000 })
  const { data: alerts } = useGet<{ items: any[] }>('/alerts')
  const ack = useAct((id: string) => api.post(`/alerts/${id}/ack`, { reason: 'Tooling inspection scheduled' }))
  const pts = s?.points.map((p, i) => ({ ...p, i })) ?? []
  const b = s?.baseline
  const alert = s?.alerts[0] as (SpcSeries['alerts'][number] & { inspection_id?: string; status?: string }) | undefined
  const alertAt = alert ? (alert.at_index ?? pts.findIndex(p => p.inspection_id === alert.inspection_id)) : -1
  const alertOpen = !!alert && (alert.status ?? 'OPEN') === 'OPEN' && !!alerts?.items.some(a => a.id === alert.id && (a.status ?? 'OPEN') === 'OPEN')
  const synth = pts.filter(p => p.synthetic).length
  return (
    <div>
      <PageHeader eyebrow="Statistical process control" title="Drift monitor"
        description="Anomaly scores per line and method version. A one-sided CUSUM flags a suspected upward drift while parts are still passing — a prompt for human investigation, not a defect prediction."
        actions={line && <Segmented value={line} onChange={setLine} options={(lines?.items ?? []).map(l => ({ value: l.id, label: l.name }))} />} />
      <div className="mb-6 grid gap-3 md:grid-cols-4">
        {[['Baseline', b ? <Status value={b.status} /> : '—', `first ${b?.n ?? 0} points, frozen`], ['Mean / σ', b ? `${fmt3(b.mean)} / ${fmt3(b.std)}` : '—', 'method gd-0.3 only'], ['Rule', s ? `CUSUM k=${s.rule.k_sigma}σ h=${s.rule.h_sigma}σ` : '—', 'upper, resets on version change'], ['Data', `${pts.length} points`, `${synth} synthetic`]].map(([k, v, sub]) => (
          <Card key={String(k)} className="p-4"><div className="eyebrow">{k}</div><div className="num mt-1.5 text-[15px]">{v}</div><div className="mt-1 text-[11px] text-muted">{sub}</div></Card>
        ))}
      </div>
      {alert && (
        <Card className="mb-6 border-hold/50">
          <div className="flex flex-wrap items-center gap-3 p-4">
            <Status value="WARN" pulse /><div className="min-w-0 flex-1 text-[13px]">{alert.message}<div className="text-[11.5px] text-muted">Triggered at point #{alertAt}. All those parts still passed individually.</div></div>
            {alertOpen ? <Button size="sm" disabled={!can('quality_engineer', 'quality_manager')} onClick={() => ack.mutate(alert.id)}>Acknowledge</Button> : <span className="text-[12px] text-muted">acknowledged</span>}
          </div>
        </Card>
      )}
      <Card className="mb-6">
        <CardHeader eyebrow="Anomaly score" title="Per-inspection score with baseline ±3σ" right={<><Honesty kind="not-probability" />{synth > 0 && <Honesty kind="synthetic">{synth} SYNTHETIC</Honesty>}</>} />
        <div className="h-72 p-2">
          <ResponsiveContainer>
            <ComposedChart data={pts} margin={{ top: 12, right: 16, bottom: 4, left: -12 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="i" tick={axis} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
              <YAxis domain={[0, 0.6]} tick={axis} tickLine={false} axisLine={false} />
              {b && <ReferenceArea y1={Math.max(0, b.mean - 3 * b.std)} y2={b.mean + 3 * b.std} fill="var(--accent)" fillOpacity={0.07} />}
              {b && <ReferenceLine y={b.mean} stroke="var(--accent)" strokeDasharray="4 4" />}
              <ReferenceLine y={0.45} stroke="var(--hold)" strokeDasharray="2 3" label={{ value: 'hold', position: 'right', ...axis }} />
              {b && <ReferenceArea x1={0} x2={b.n - 1} fill="var(--muted)" fillOpacity={0.05} label={{ value: 'baseline', position: 'insideTopLeft', ...axis }} />}
              {alert && alertAt >= 0 && <ReferenceLine x={alertAt} stroke="var(--hold)" />}
              <Tooltip content={<Tip />} />
              <Line dataKey="score" stroke="var(--ink-2)" strokeWidth={1.25} dot={false} isAnimationActive={false} />
              <Scatter dataKey="score" fill="var(--ink-2)" shape={(p: any) => <circle cx={p.cx} cy={p.cy} r={2.2} fill={p.payload.synthetic ? 'var(--muted)' : 'var(--accent)'} />} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card>
        <CardHeader eyebrow="Decision statistic" title="Upper CUSUM (σ units)" right={<span className="text-[11.5px] text-muted">alert when &gt; <Mono>{s?.rule.h_sigma}σ</Mono></span>} />
        <div className="h-48 p-2">
          <ResponsiveContainer>
            <ComposedChart data={pts} margin={{ top: 12, right: 16, bottom: 4, left: -12 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="i" tick={axis} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
              <YAxis tick={axis} tickLine={false} axisLine={false} />
              <ReferenceLine y={s?.rule.h_sigma} stroke="var(--hold)" strokeDasharray="4 4" />
              <Tooltip content={<Tip />} />
              <Area dataKey="cusum" stroke="var(--hold)" fill="var(--hold)" fillOpacity={0.15} strokeWidth={1.5} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </Card>
    </div>
  )
}
