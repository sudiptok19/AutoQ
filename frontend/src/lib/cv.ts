/**
 * Browser-side image measurements.
 *  - qualityGate(): real blur / glare / resolution measurement on the pixels (used for capture
 *    pre-checks; the server re-measures and is authoritative).
 *  - renderBracket(): draws SYNTHETIC fixture images of a bracket for the mock backend & demos.
 *  - goldenDiff(): small aligned golden-reference difference used ONLY by the mock backend.
 */
export const W = 640, H = 480

export function toGray(d: ImageData): Float32Array {
  const g = new Float32Array(d.width * d.height)
  for (let i = 0, j = 0; i < g.length; i++, j += 4) g[i] = 0.299 * d.data[j] + 0.587 * d.data[j + 1] + 0.114 * d.data[j + 2]
  return g
}

export function laplacianVariance(g: Float32Array, w: number, h: number): number {
  let sum = 0, sq = 0, n = 0
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x
    const l = g[i - w] + g[i + w] + g[i - 1] + g[i + 1] - 4 * g[i]
    sum += l; sq += l * l; n++
  }
  const m = sum / n
  return sq / n - m * m
}

export function glareRatio(g: Float32Array): number {
  let c = 0
  for (let i = 0; i < g.length; i++) if (g[i] >= 250) c++
  return c / g.length
}

export const DEFAULT_GATE = { blur_min: 80, glare_max: 0.12, min_w: 320, min_h: 240 }

export function qualityGate(img: ImageData, origW: number, origH: number, t = DEFAULT_GATE) {
  const g = toGray(img)
  const blur = laplacianVariance(g, img.width, img.height)
  const glare = glareRatio(g)
  const failures: string[] = []
  if (origW < t.min_w || origH < t.min_h) failures.push('IMAGE_QUALITY_RESOLUTION')
  if (blur < t.blur_min) failures.push('IMAGE_QUALITY_BLUR')
  if (glare > t.glare_max) failures.push('IMAGE_QUALITY_GLARE')
  return { passed: failures.length === 0, blur_laplacian_var: round(blur, 1), glare_ratio: round(glare, 4), width: origW, height: origH, thresholds: t, failures }
}

const round = (v: number, d: number) => Math.round(v * 10 ** d) / 10 ** d

/* ── Synthetic fixture renderer ─────────────────────────────────── */
export type FixtureKind = 'golden' | 'good' | 'defect' | 'blurry' | 'glare'

function rng(seed: number) { return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296) }

export function renderBracket(kind: FixtureKind, seed = 7): HTMLCanvasElement {
  const c = document.createElement('canvas'); c.width = W; c.height = H
  const x = c.getContext('2d')!
  const r = rng(seed)
  const dx = kind === 'golden' ? 0 : Math.round((r() - 0.5) * 6), dy = kind === 'golden' ? 0 : Math.round((r() - 0.5) * 6)
  if (kind === 'blurry') x.filter = 'blur(5px)'
  const bg = x.createLinearGradient(0, 0, 0, H); bg.addColorStop(0, '#3a3f45'); bg.addColorStop(1, '#2a2e33')
  x.fillStyle = bg; x.fillRect(0, 0, W, H)
  x.strokeStyle = '#ffffff10'; x.lineWidth = 1
  for (let i = 0; i < W; i += 32) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, H); x.stroke() }
  x.save(); x.translate(dx, dy)
  const body = new Path2D()
  body.moveTo(130, 120); body.lineTo(470, 110); body.quadraticCurveTo(520, 112, 522, 160)
  body.lineTo(528, 330); body.quadraticCurveTo(526, 372, 480, 374); body.lineTo(300, 380)
  body.lineTo(250, 300); body.lineTo(140, 300); body.quadraticCurveTo(108, 298, 108, 262); body.lineTo(110, 150)
  body.quadraticCurveTo(112, 122, 130, 120); body.closePath()
  const metal = x.createLinearGradient(100, 100, 530, 380)
  metal.addColorStop(0, '#c9ced4'); metal.addColorStop(0.45, '#9ea5ad'); metal.addColorStop(1, '#7d848c')
  x.fillStyle = metal; x.fill(body)
  x.strokeStyle = '#5d636a'; x.lineWidth = 3; x.stroke(body)
  const holes: [number, number, number][] = [[175, 210, 30], [440, 175, 26], [450, 315, 26]]
  for (const [hx, hy, hr] of holes) {
    x.beginPath(); x.arc(hx, hy, hr + 7, 0, Math.PI * 2); x.fillStyle = '#b7bdc3'; x.fill()
    x.beginPath(); x.arc(hx, hy, hr, 0, Math.PI * 2); x.fillStyle = '#1c1f23'; x.fill()
  }
  x.beginPath(); x.roundRect(285, 190, 90, 26, 13); x.fillStyle = '#1c1f23'; x.fill()
  x.fillStyle = '#5d636a'; x.font = '600 15px monospace'; x.fillText('PN-44120', 300, 290)
  if (kind === 'defect') {
    x.strokeStyle = '#141619'; x.lineWidth = 4; x.lineCap = 'round'
    x.beginPath(); x.moveTo(205, 222); x.lineTo(232, 236); x.lineTo(248, 232); x.lineTo(268, 252); x.lineTo(281, 250); x.stroke()
    x.beginPath(); x.moveTo(470, 110); x.lineTo(500, 118); x.lineTo(490, 142); x.closePath(); x.fillStyle = '#2a2e33'; x.fill()
  }
  x.restore()
  x.filter = 'none'
  if (kind === 'glare') {
    const gl = x.createRadialGradient(330, 220, 10, 330, 220, 230)
    gl.addColorStop(0, '#ffffff'); gl.addColorStop(0.55, '#ffffff'); gl.addColorStop(1, '#ffffff00')
    x.fillStyle = gl; x.fillRect(0, 0, W, H)
  }
  const d = x.getImageData(0, 0, W, H)
  for (let i = 0; i < d.data.length; i += 4) { const n = (r() - 0.5) * 10; d.data[i] += n; d.data[i + 1] += n; d.data[i + 2] += n }
  x.putImageData(d, 0, 0)
  return c
}

/* ── Golden-reference diff (mock backend only) ─────────────────── */
function boxBlur(g: Float32Array, w: number, h: number, r: number) {
  const out = new Float32Array(g.length), tmp = new Float32Array(g.length)
  const cl = (v: number, m: number) => Math.min(m - 1, Math.max(0, v))
  for (let y = 0; y < h; y++) {
    let s = 0; for (let x = -r; x <= r; x++) s += g[y * w + cl(x, w)]
    for (let x = 0; x < w; x++) { tmp[y * w + x] = s / (2 * r + 1); s += g[y * w + cl(x + r + 1, w)] - g[y * w + cl(x - r, w)] }
  }
  for (let x = 0; x < w; x++) {
    let s = 0; for (let y = -r; y <= r; y++) s += tmp[cl(y, h) * w + x]
    for (let y = 0; y < h; y++) { out[y * w + x] = s / (2 * r + 1); s += tmp[cl(y + r + 1, h) * w + x] - tmp[cl(y - r, h) * w + x] }
  }
  return out
}

export function goldenDiff(probe: ImageData, golden: ImageData) {
  const w = probe.width, h = probe.height
  const a = boxBlur(toGray(probe), w, h, 2), b = boxBlur(toGray(golden), w, h, 2)
  let best = { dx: 0, dy: 0, e: Infinity }
  for (let dy = -6; dy <= 6; dy++) for (let dx = -6; dx <= 6; dx++) {
    let e = 0
    for (let y = 20; y < h - 20; y += 4) for (let x = 20; x < w - 20; x += 4) e += Math.abs(a[(y + dy) * w + x + dx] - b[y * w + x])
    if (e < best.e) best = { dx, dy, e }
  }
  const diff = new Float32Array(w * h)
  for (let y = 8; y < h - 8; y++) for (let x = 8; x < w - 8; x++) diff[y * w + x] = Math.abs(a[(y + best.dy) * w + x + best.dx] - b[y * w + x])
  const cw = 32, ch = 32, cells: { x: number; y: number; w: number; h: number; score: number }[] = []
  for (let cy = 0; cy < h; cy += ch) for (let cx = 0; cx < w; cx += cw) {
    let s = 0; for (let y = cy; y < cy + ch; y++) for (let x = cx; x < cx + cw; x++) s += diff[y * w + x]
    cells.push({ x: cx, y: cy, w: cw, h: ch, score: Math.min(1, s / (cw * ch) / 28) })
  }
  const score = Math.max(...cells.map(c => c.score))
  const hc = document.createElement('canvas'); hc.width = w; hc.height = h
  const hx = hc.getContext('2d')!; const hd = hx.createImageData(w, h)
  for (let i = 0; i < diff.length; i++) {
    const v = Math.min(1, diff[i] / 50)
    hd.data[i * 4] = 255; hd.data[i * 4 + 1] = Math.round(200 * (1 - v)); hd.data[i * 4 + 2] = 40; hd.data[i * 4 + 3] = Math.round(v * 220)
  }
  hx.putImageData(hd, 0, 0)
  const regions = cells.filter(c => c.score > 0.35).sort((p, q) => q.score - p.score).slice(0, 6).map(c => ({ ...c, score: Math.round(c.score * 100) / 100 }))
  return { score: Math.round(score * 1000) / 1000, shift: { dx: best.dx, dy: best.dy }, heatmap: hc.toDataURL('image/png'), regions }
}

export async function loadImageData(src: string | Blob): Promise<{ data: ImageData; origW: number; origH: number; dataUrl: string }> {
  const url = typeof src === 'string' ? src : URL.createObjectURL(src)
  const img = new Image(); img.src = url
  await img.decode()
  const c = document.createElement('canvas'); c.width = W; c.height = H
  const x = c.getContext('2d')!
  const s = Math.min(W / img.naturalWidth, H / img.naturalHeight)
  const dw = img.naturalWidth * s, dh = img.naturalHeight * s
  x.fillStyle = '#000'; x.fillRect(0, 0, W, H)
  x.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh)
  const dataUrl = c.toDataURL('image/jpeg', 0.82)
  if (typeof src !== 'string') URL.revokeObjectURL(url)
  return { data: x.getImageData(0, 0, W, H), origW: img.naturalWidth, origH: img.naturalHeight, dataUrl }
}

export const canvasBlob = (c: HTMLCanvasElement) => new Promise<Blob>(res => c.toBlob(b => res(b!), 'image/jpeg', 0.9))
