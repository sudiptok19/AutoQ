import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Camera, Check, CircleSlash, FileImage, FlaskConical, Minus, TriangleAlert, Upload, X } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import type { GateCheck, Reason } from '../api/types'
import { API_MODE, api, evidenceSrc } from '../api/client'
import { DEFAULT_GATE, canvasBlob, loadImageData, qualityGate, renderBracket, type FixtureKind } from '../lib/cv'
import { Button, Honesty, Mono, Segmented, cx, toneVar } from './ui'

/* ── Dispatch gate checklist ─────────────────────────────────── */
const GATE_LABEL: Record<string, string> = {
  SIGNATURE: 'Signature', KEY: 'Trusted key', IDENTITY: 'Identity', LIFECYCLE: 'Lifecycle', CONTAINMENT: '4M containment', QUARANTINE: 'Quarantine',
  ORDER: 'Order match', DUPLICATE: 'No duplicate', SEAL: 'Seal', QUANTITY: 'Quantity', SEQUENCE: 'Sequence',
}
export function GateChecklist({ checks, animate }: { checks: GateCheck[]; animate?: boolean }) {
  return (
    <ol className="grid gap-1">
      {checks.map((c, i) => {
        const tone = c.status === 'PASS' ? 'pass' : c.status === 'FAIL' ? 'reject' : c.status === 'WARN' ? 'hold' : 'neutral'
        return (
          <li key={c.gate} className={cx('flex items-start gap-3 rounded-lg px-2.5 py-1.5', animate && 'rise', c.status === 'FAIL' && 'bg-reject/8')} style={animate ? { animationDelay: `${i * 45}ms` } : undefined}>
            <span className="mt-0.5 grid size-4.5 shrink-0 place-items-center rounded-full" style={{ background: `color-mix(in srgb, ${toneVar[tone]} 18%, transparent)`, color: toneVar[tone] }}>
              {c.status === 'PASS' ? <Check size={11} strokeWidth={3} /> : c.status === 'FAIL' ? <X size={11} strokeWidth={3} /> : c.status === 'WARN' ? <TriangleAlert size={10} /> : <Minus size={11} />}
            </span>
            <span className="num w-5 shrink-0 text-[10.5px] leading-5 text-muted">{String(i + 1).padStart(2, '0')}</span>
            <span className={cx('w-28 shrink-0 text-[12.5px] leading-5', c.status === 'SKIP' ? 'text-muted' : 'font-medium')}>{GATE_LABEL[c.gate] ?? c.gate}</span>
            <span className={cx('min-w-0 text-[12px] leading-5', c.status === 'FAIL' ? 'text-reject' : 'text-muted')}>{c.status === 'SKIP' ? 'not evaluated — identity not established' : c.detail}</span>
          </li>
        )
      })}
    </ol>
  )
}

export function Reasons({ reasons, empty }: { reasons: Reason[]; empty?: ReactNode }) {
  if (!reasons.length) return <>{empty ?? null}</>
  return (
    <ul className="space-y-1.5">
      {reasons.map((r, i) => (
        <li key={i} className="flex items-start gap-2 text-[12.5px]">
          <CircleSlash size={13} className="mt-0.5 shrink-0 text-reject" />
          <span><Mono className="font-semibold text-reject">{r.code}</Mono> <span className="text-ink-2">{r.message}</span></span>
        </li>
      ))}
    </ul>
  )
}

/* ── Evidence viewer with heatmap overlay ─────────────────────── */
export function EvidenceImage({ url, heatmap, regions, className }: { url?: string | null; heatmap?: string | null; regions?: { x: number; y: number; w: number; h: number; score: number }[]; className?: string }) {
  const [show, setShow] = useState(true)
  const src = evidenceSrc(url), hm = evidenceSrc(heatmap)
  if (!src) return <div className={cx('grid aspect-[4/3] place-items-center rounded-lg border border-dashed border-line text-xs text-muted', className)}>No image evidence</div>
  return (
    <div className={cx('relative overflow-hidden rounded-lg border border-line bg-black', className)}>
      <img src={src} alt="Inspection evidence" className="block aspect-[4/3] w-full object-contain" />
      {hm && show && <img src={hm} alt="Difference heatmap" className="pointer-events-none absolute inset-0 aspect-[4/3] w-full object-contain mix-blend-screen" />}
      {show && regions?.map((r, i) => (
        <div key={i} className="pointer-events-none absolute border-2 border-reject" style={{ left: `${(r.x / 640) * 100}%`, top: `${(r.y / 480) * 100}%`, width: `${(r.w / 640) * 100}%`, height: `${(r.h / 480) * 100}%` }} />
      ))}
      {hm && (
        <button onClick={() => setShow(s => !s)} className="absolute bottom-2 right-2 rounded-md bg-black/60 px-2 py-1 font-mono text-[10.5px] text-white backdrop-blur hover:bg-black/80">
          {show ? 'hide' : 'show'} diff overlay
        </button>
      )}
    </div>
  )
}

/* ── Image capture: synthetic fixture / upload / camera, with live quality pre-check ── */
export interface Captured { blob: Blob; preview: string; fixture: string | null; precheck: ReturnType<typeof qualityGate> }
type FixtureOpt = { value: string; label: string; hint: string }
export const PART_FIXTURES: FixtureOpt[] = [
  { value: 'good', label: 'Good part', hint: 'aligned, sharp' },
  { value: 'defect', label: 'Cracked bore', hint: 'crack + edge chip' },
  { value: 'blurry', label: 'Motion blur', hint: 'fails sharpness gate' },
  { value: 'glare', label: 'Glare', hint: 'fails glare gate' },
]
export const SEAL_FIXTURES: FixtureOpt[] = [
  { value: 'intact', label: 'Clean recapture', hint: 'same seal, re-photographed' },
  { value: 'torn', label: 'Torn & re-stuck', hint: 'pattern disturbed' },
  { value: 'bad_light', label: 'Bad light', hint: 'too few keypoints' },
]

function renderSeal(kind: string, seed = 3): HTMLCanvasElement {
  const c = document.createElement('canvas'); c.width = 640; c.height = 480
  const x = c.getContext('2d')!
  let s = seed; const r = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296)
  x.fillStyle = '#8b6a43'; x.fillRect(0, 0, 640, 480)
  for (let i = 0; i < 3000; i++) { x.fillStyle = `rgba(60,40,20,${r() * 0.15})`; x.fillRect(r() * 640, r() * 480, 2, 8) }
  x.save(); x.translate(320, 240); x.rotate(-0.06)
  x.fillStyle = '#e9e4d8'; x.fillRect(-280, -60, 560, 120)
  let s2 = 99; const r2 = () => ((s2 = (s2 * 1664525 + 1013904223) >>> 0) / 4294967296)
  for (let i = 0; i < 420; i++) { x.fillStyle = r2() > 0.5 ? '#0a978c' : '#1f2933'; x.beginPath(); x.arc(-275 + r2() * 550, -55 + r2() * 110, 1 + r2() * 4, 0, Math.PI * 2); x.fill() }
  x.fillStyle = '#1f2933'; x.font = '700 22px monospace'; x.fillText('AQ · VOID IF BROKEN', -130, 8)
  if (kind === 'torn') { x.fillStyle = '#8b6a43'; x.beginPath(); x.moveTo(-280, -60); x.lineTo(-120, -60); x.lineTo(-150, -10); x.lineTo(-200, 10); x.lineTo(-280, 0); x.fill(); x.fillStyle = '#e9e4d8'; x.fillRect(-150, -30, 70, 40) }
  x.restore()
  if (kind === 'bad_light') { x.fillStyle = 'rgba(0,0,0,.82)'; x.fillRect(0, 0, 640, 480); x.filter = 'blur(2px)' }
  return c
}

export function ImageCapture({ fixtures, kind, onCaptured, captured }: { fixtures: FixtureOpt[]; kind: 'part' | 'seal'; onCaptured: (c: Captured | null) => void; captured: Captured | null }) {
  const [mode, setMode] = useState<'fixture' | 'upload' | 'camera'>('fixture')
  const [fixture, setFixture] = useState<string | null>(null)
  const [camErr, setCamErr] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null); const streamRef = useRef<MediaStream | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const finish = async (blob: Blob, fx: string | null) => {
    const { data, origW, origH, dataUrl } = await loadImageData(blob)
    onCaptured({ blob, preview: dataUrl, fixture: fx, precheck: qualityGate(data, origW, origH, DEFAULT_GATE) })
  }
  const pickFixture = async (v: string) => {
    setFixture(v)
    const seed = Math.floor(Math.random() * 1000) + 11
    if (API_MODE === 'live') {
      // Live: the backend's own SYNTHETIC render family, which its demo profiles were calibrated on.
      // A browser-drawn bracket would never register against the server's golden reference.
      await finish(await api.blob(kind === 'part' ? `/demo/fixtures/part/${v}?seed=${seed}` : `/demo/fixtures/seal/${v}?seed=${seed}`), v)
      return
    }
    const canvas = kind === 'part' ? renderBracket(v as FixtureKind, seed) : renderSeal(v)
    await finish(await canvasBlob(canvas), v)
  }
  useEffect(() => {
    if (mode !== 'camera') { streamRef.current?.getTracks().forEach(t => t.stop()); streamRef.current = null; return }
    setCamErr(null)
    if (!navigator.mediaDevices?.getUserMedia) { setCamErr('Camera API unavailable — needs HTTPS or localhost.'); return }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 } } })
      .then(s => { streamRef.current = s; if (videoRef.current) { videoRef.current.srcObject = s; videoRef.current.play() } })
      .catch(e => setCamErr(e?.message ?? 'Camera permission denied'))
    return () => { streamRef.current?.getTracks().forEach(t => t.stop()) }
  }, [mode])
  const snap = async () => {
    const v = videoRef.current!; const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight
    c.getContext('2d')!.drawImage(v, 0, 0); await finish(await canvasBlob(c), null)
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented value={mode} onChange={m => { setMode(m); onCaptured(null); setFixture(null) }} options={[
          { value: 'fixture', label: <span className="flex items-center gap-1.5"><FlaskConical size={12} />Fixture</span> },
          { value: 'upload', label: <span className="flex items-center gap-1.5"><Upload size={12} />Upload</span> },
          { value: 'camera', label: <span className="flex items-center gap-1.5"><Camera size={12} />Camera</span> },
        ]} />
        {mode === 'fixture' && <Honesty kind="synthetic">SYNTHETIC FIXTURE</Honesty>}
      </div>

      {mode === 'fixture' && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {fixtures.map(f => (
            <button key={f.value} onClick={() => pickFixture(f.value)}
              className={cx('rounded-lg border px-3 py-2 text-left transition', fixture === f.value ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong')}>
              <div className="text-[12.5px] font-medium">{f.label}</div><div className="text-[11px] text-muted">{f.hint}</div>
            </button>
          ))}
        </div>
      )}
      {mode === 'upload' && (
        <button onClick={() => fileRef.current?.click()} className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong py-6 text-[13px] text-muted hover:border-accent hover:text-accent">
          <FileImage size={16} /> Choose a JPEG/PNG photo
          <input ref={fileRef} type="file" accept="image/*" capture="environment" hidden onChange={e => e.target.files?.[0] && finish(e.target.files[0], null)} />
        </button>
      )}
      {mode === 'camera' && (
        <div className="relative overflow-hidden rounded-lg border border-line bg-black">
          {camErr ? <div className="p-6 text-center text-[13px] text-hold">{camErr}</div> : <>
            <video ref={videoRef} playsInline muted className="aspect-[4/3] w-full object-cover" />
            <div className="pointer-events-none absolute inset-6 rounded-lg border-2 border-dashed border-white/40" />
            <div className="absolute inset-x-0 bottom-3 flex justify-center"><Button variant="primary" onClick={snap}><Camera size={15} /> Capture</Button></div>
          </>}
        </div>
      )}

      {captured && (
        <div className="rise grid gap-3 rounded-lg border border-line bg-surface-2 p-3 sm:grid-cols-[160px_1fr]">
          <img src={captured.preview} alt="capture" className="aspect-[4/3] w-full rounded-md border border-line object-cover" />
          <div>
            <div className="mb-2 flex items-center gap-2"><span className="eyebrow">On-device pre-check</span><span className="text-[11px] text-muted">server re-measures</span></div>
            <Metric label="Sharpness (Laplacian var.)" value={captured.precheck.blur_laplacian_var} ok={captured.precheck.blur_laplacian_var >= DEFAULT_GATE.blur_min} rule={`≥ ${DEFAULT_GATE.blur_min}`} />
            <Metric label="Glare ratio" value={`${(captured.precheck.glare_ratio * 100).toFixed(1)}%`} ok={captured.precheck.glare_ratio <= DEFAULT_GATE.glare_max} rule={`≤ ${DEFAULT_GATE.glare_max * 100}%`} />
            <Metric label="Resolution" value={`${captured.precheck.width}×${captured.precheck.height}`} ok={!captured.precheck.failures.includes('IMAGE_QUALITY_RESOLUTION')} rule="≥ 320×240" />
            <div className="mt-1.5 text-[11px] text-muted">Thresholds are provisional calibration defaults.</div>
          </div>
        </div>
      )}
    </div>
  )
}

export function Metric({ label, value, ok, rule }: { label: string; value: ReactNode; ok: boolean; rule?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5 text-[12.5px]">
      <span className="text-ink-2">{label}</span>
      <span className="flex items-center gap-2"><span className="num">{value}</span>{rule && <span className="num text-[10.5px] text-muted">{rule}</span>}
        <span style={{ color: ok ? 'var(--pass)' : 'var(--reject)' }}>{ok ? <Check size={13} /> : <X size={13} />}</span></span>
    </div>
  )
}

/* ── QR label ── */
export function QrLabel({ token, title, sub }: { token: string; title: string; sub?: string }) {
  return (
    <div className="flex items-center gap-4 rounded-xl border border-line bg-white p-3 text-black">
      <QRCodeSVG value={token} size={112} level="M" marginSize={0} />
      <div className="min-w-0">
        <div className="font-mono text-[10px] tracking-widest text-neutral-500">AUTOQ-TRACE · SIGNED</div>
        <div className="mt-0.5 font-mono text-[15px] font-semibold">{title}</div>
        {sub && <div className="mt-0.5 font-mono text-[11px] text-neutral-600">{sub}</div>}
        <div className="mt-2 max-w-[220px] break-all font-mono text-[9px] leading-tight text-neutral-400">{token.slice(0, 64)}…</div>
      </div>
    </div>
  )
}

/** Segmented quantity bar for status distributions. */
export function StackBar({ parts, height = 8 }: { parts: { value: number; color: string; label: string }[]; height?: number }) {
  const total = parts.reduce((a, b) => a + b.value, 0) || 1
  return (
    <div className="flex w-full gap-px overflow-hidden rounded-full bg-surface-3" style={{ height }}>
      {parts.filter(p => p.value > 0).map(p => <div key={p.label} title={`${p.label}: ${p.value}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} />)}
    </div>
  )
}
