import { useEffect, useRef, useState } from 'react'
import jsQR from 'jsqr'
import { CameraOff } from 'lucide-react'

/** Live QR decoding: native BarcodeDetector where available (Android Chrome), jsQR fallback (iOS Safari, desktop). */
export function QrCamera({ onDecode, paused }: { onDecode: (text: string) => void; paused?: boolean }) {
  const video = useRef<HTMLVideoElement>(null)
  const [err, setErr] = useState<string | null>(null)
  const [engine, setEngine] = useState<string>('')
  const cb = useRef(onDecode); cb.current = onDecode
  const pausedRef = useRef(paused); pausedRef.current = paused

  useEffect(() => {
    let stream: MediaStream | null = null, timer = 0, stopped = false
    const canvas = document.createElement('canvas'); const ctx = canvas.getContext('2d', { willReadFrequently: true })!
    const Native = (window as any).BarcodeDetector
    const detector = Native ? new Native({ formats: ['qr_code'] }) : null
    setEngine(detector ? 'BarcodeDetector' : 'jsQR')
    const tick = async () => {
      if (stopped) return
      const v = video.current
      if (v && v.readyState >= 2 && !pausedRef.current) {
        try {
          let text: string | null = null
          if (detector) { const r = await detector.detect(v); text = r[0]?.rawValue ?? null }
          else {
            const w = 640, h = Math.round((v.videoHeight / v.videoWidth) * 640) || 480
            canvas.width = w; canvas.height = h; ctx.drawImage(v, 0, 0, w, h)
            text = jsQR(ctx.getImageData(0, 0, w, h).data, w, h, { inversionAttempts: 'dontInvert' })?.data ?? null
          }
          if (text) cb.current(text)
        } catch { /* frame not ready */ }
      }
      timer = window.setTimeout(tick, 180)
    }
    if (!navigator.mediaDevices?.getUserMedia) { setErr('Camera needs a secure context (HTTPS or localhost). Run `npm run dev:https` for phones.'); return }
    navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1280 } }, audio: false })
      .then(s => { stream = s; if (stopped) return s.getTracks().forEach(t => t.stop()); video.current!.srcObject = s; video.current!.play(); tick() })
      .catch(e => setErr(e?.name === 'NotAllowedError' ? 'Camera permission denied.' : e?.message ?? 'Camera unavailable'))
    return () => { stopped = true; clearTimeout(timer); stream?.getTracks().forEach(t => t.stop()) }
  }, [])

  return (
    <div className="relative aspect-square w-full overflow-hidden rounded-2xl bg-black">
      {err ? <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-[13px] text-white/70"><CameraOff size={22} />{err}</div> : <>
        <video ref={video} playsInline muted className="h-full w-full object-cover" />
        <div className="pointer-events-none absolute inset-[14%] rounded-2xl border-2 border-white/80 shadow-[0_0_0_999px_rgba(0,0,0,.45)]">
          {['left-0 top-0 border-l-4 border-t-4 rounded-tl-2xl', 'right-0 top-0 border-r-4 border-t-4 rounded-tr-2xl', 'left-0 bottom-0 border-l-4 border-b-4 rounded-bl-2xl', 'right-0 bottom-0 border-r-4 border-b-4 rounded-br-2xl'].map(c => <span key={c} className={`absolute size-7 border-accent ${c}`} />)}
          {!paused && <div className="absolute inset-x-3 top-0 h-0.5 bg-accent/80 shadow-[0_0_12px_var(--accent)]" style={{ animation: 'sweep-line 2.2s ease-in-out infinite alternate' }} />}
        </div>
        <div className="absolute bottom-2 left-2 rounded bg-black/50 px-1.5 py-0.5 font-mono text-[9.5px] text-white/70">{engine}</div>
      </>}
    </div>
  )
}
