/* Mock implementation of the /api/v1 contract. Mirrors server-side policy so the UI can be exercised end-to-end. */
import type { Report8D, ReportItem, SegregationTask, Containment, Eligibility, FourMEvent, GateCheck, Inspection, Lot, Package, Reason, Recall, RecallImpact, Role, ScanDecision, SealCheck, Shipment, SpcSeries, TraceGraph, TraceNode, TraceEdge, User } from '../api/types'
import { goldenDiff, loadImageData, qualityGate, renderBracket } from '../lib/cv'
import { verifyToken, sha256Hex } from '../lib/token'
import { GENESIS, verifyChain } from '../lib/chain'
import { refreshIfStale, S, audit, init, issuePassport, nextId, now, save, seed, signPackage, sign, verifyAudit, type UnitRow } from './state'

export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: any) { super(message) }
}
type Ctx = { params: string[]; query: URLSearchParams; body: any; user: User | null }
type Handler = (c: Ctx) => any | Promise<any>
const routes: [string, RegExp, Handler][] = []
const r = (m: string, path: string, h: Handler) => routes.push([m, new RegExp('^' + path.replace(/:[a-z_]+/g, '([^/]+)') + '$'), h])

const need = (c: Ctx, ...roles: Role[]) => {
  if (!c.user) throw new HttpError(401, 'UNAUTHENTICATED', 'Sign in required')
  if (roles.length && !roles.some(x => c.user!.roles.includes(x))) throw new HttpError(403, 'ROLE_FORBIDDEN', `Requires role: ${roles.join(' or ')}`)
  return c.user
}
const nf = (what: string) => { throw new HttpError(404, 'NOT_FOUND', `${what} not found`) }
const minsSince = (iso: string) => (Date.now() - new Date(iso).getTime()) / 60_000
const unit = (id: string) => S.units.find(u => u.id === id) ?? nf('Unit')
const passportOf = (u: UnitRow) => S.passports.find(p => p.id === u.passport_id) ?? null
const lineOf = (u: UnitRow) => S.lots.find(l => l.id === u.lot_id)!.line_id
const shipmentOfPkg = (pkgId: string) => S.shipments.find(s => s.loaded_package_ids.includes(pkgId)) ?? null
const isShipped = (u: UnitRow) => !!(u.package_id && shipmentOfPkg(u.package_id))
const activeContainments = () => S.containments.filter(c => c.status === 'ACTIVE')
const containmentsFor = (u: UnitRow) => activeContainments().filter(c => c.affected_unit_ids.includes(u.id) || (c.affected_lot_ids.includes(u.lot_id) && !isShipped(u) && new Date(u.created_at) >= new Date(c.created_at)))
const evtCode = (c: Containment) => S.events.find(e => e.id === c.event_id)?.code ?? c.id

export function eligibility(u: UnitRow): Eligibility {
  const b: Reason[] = []
  if (u.quality_status !== 'PASS') b.push({ code: 'UNIT_NOT_PASS', message: `Quality status is ${u.quality_status}` })
  const pp = passportOf(u)
  if (!pp) b.push({ code: 'NO_PASSPORT', message: 'No quality passport issued' })
  else if (pp.status === 'REVOKED') b.push({ code: 'PASSPORT_REVOKED', message: 'Passport revoked' })
  for (const c of containmentsFor(u)) b.push({ code: 'CONTAINMENT_ACTIVE', message: `4M containment ${evtCode(c)} is active`, ref: c.id })
  if (u.held) b.push({ code: 'QUARANTINED', message: 'Under recall quarantine', ref: S.recalls.find(r => r.executed_at && unitsInScope(r.scope).includes(u.id))?.id })
  return { dispatch_eligible: b.length === 0, blockers: b }
}
const unitView = (u: UnitRow) => ({ ...u, eligibility: eligibility(u) })
function lotView(l: (typeof S.lots)[number]): Lot {
  const us = S.units.filter(u => u.lot_id === l.id)
  const counts = { UNINSPECTED: 0, PASS: 0, HOLD: 0, REJECT: 0 }
  us.forEach(u => counts[u.quality_status]++)
  return { ...l, counts, containment_ids: activeContainments().filter(c => c.affected_lot_ids.includes(l.id)).map(c => c.id), held: !!S.lotHeld[l.id] }
}
function containmentView(c: Containment): Containment {
  const ev = S.events.find(e => e.id === c.event_id)!
  const fresh = S.inspections.filter(i => i.created_at >= c.created_at && i.verdict === 'PASS' && lineOf(unit(i.unit_id)) === c.line_id)
  return {
    ...c,
    obligations: c.obligations.map(o => o.type === 'FULL_INSPECT_FIRST_N'
      ? { ...o, completed: Math.min(o.required, new Set(fresh.filter(i => !c.obligations.find(x => x.type === 'RECHECK_UNIT')?.unit_ids?.includes(i.unit_id)).map(i => i.unit_id)).size) }
      : { ...o, completed: (o.unit_ids ?? []).filter(id => fresh.some(i => i.unit_id === id)).length }),
    ...(ev ? {} : {}),
  }
}
function syncPassportHolds() {
  for (const u of S.units) {
    const pp = passportOf(u); if (!pp || pp.status === 'REVOKED') continue
    pp.status = u.held || u.quality_status !== 'PASS' || containmentsFor(u).length ? 'HELD' : 'ACTIVE'
  }
}

/* ── auth ── */
r('POST', '/auth/login', ({ body }) => {
  const u = S.users.find(x => x.username === body?.username)
  if (!u || body?.password !== 'autoq-dev') throw new HttpError(401, 'INVALID_CREDENTIALS', 'Unknown user or wrong password')
  return { access_token: `mock.${u.username}`, user: u }
})
r('GET', '/auth/me', c => need(c))
const unix = (iso: string) => Math.floor(Date.parse(iso) / 1000)
r('GET', '/health', () => ({ status: 'ok', db: 'mock', key_kid: S.keys[0].kid, version: 'mock-0.1' }))
r('POST', '/demo/reset', async c => { need(c, 'admin', 'quality_manager'); await seed(); return { ok: true } })

/* ── master data ── */
r('GET', '/suppliers', () => ({ items: S.suppliers, next_cursor: null }))
r('GET', '/parts', () => ({ items: S.parts, next_cursor: null }))
r('GET', '/lines', () => ({ items: S.lines, next_cursor: null }))
r('GET', '/heats', () => ({ items: S.heats.map(h => ({ ...h, held: !!S.lotHeld['heat:' + h.id] })), next_cursor: null }))
r('GET', '/customers', () => ({ items: S.customers, next_cursor: null }))
r('GET', '/lots', () => ({ items: S.lots.map(lotView), next_cursor: null }))
r('GET', '/lots/:id', ({ params }) => lotView(S.lots.find(l => l.id === params[0]) ?? nf('Lot')))
r('GET', '/units', ({ query }) => ({ items: S.units.filter(u => !query.get('lot_id') || u.lot_id === query.get('lot_id')).map(unitView), next_cursor: null }))
r('GET', '/units/:id', ({ params }) => {
  const u = S.units.find(x => x.id === params[0] || x.serial === params[0]) ?? nf('Unit')
  return { ...unitView(u), inspections: S.inspections.filter(i => i.unit_id === u.id).reverse(), passport: passportOf(u) }
})
r('GET', '/policies', () => S.policies)

/* ── inspection ── */
let goldenCache: ImageData | null = null
const golden = () => goldenCache ??= renderBracket('golden').getContext('2d')!.getImageData(0, 0, 640, 480)

r('POST', '/inspections', async c => {
  const user = need(c, 'operator', 'quality_engineer', 'quality_manager')
  const f = c.body as FormData
  const u = unit(String(f.get('unit_id')))
  if (u.quality_status === 'REJECT') throw new HttpError(409, 'UNIT_REJECTED', 'Rejected units cannot be re-inspected')
  const lastHold = S.inspections.filter(i => i.unit_id === u.id).at(-1)
  if (u.quality_status === 'HOLD' && lastHold?.review?.action !== 'REINSPECT') throw new HttpError(409, 'HOLD_REVIEW_REQUIRED', 'A quality engineer must review this HOLD before reinspection')
  const image = f.get('image') as Blob
  const fixture = f.get('fixture') ? String(f.get('fixture')) : null
  const { data, origW, origH, dataUrl } = await loadImageData(image)
  const pol = S.policies.inspection
  const gate = qualityGate(data, origW, origH, { blur_min: pol.blur_min, glare_max: pol.glare_max, min_w: pol.min_w, min_h: pol.min_h })
  const sha = sha256Hex(dataUrl); S.evidence[sha] = dataUrl
  const contained = containmentsFor(u).length > 0
  const tight = contained ? 1 - S.policies.containment.threshold_tightening : 1
  const hold_t = Math.round(pol.hold_threshold * tight * 1000) / 1000, rej_t = Math.round(pol.reject_threshold * tight * 1000) / 1000
  // The mock's golden reference is a synthetic render, so it is only "calibrated" for synthetic fixture captures.
  const calibrated = !!fixture
  const reasons: Reason[] = []
  let score: number | null = null, heat: string | null = null, regions: any[] = []
  if (!gate.passed) gate.failures.forEach(code => reasons.push({ code, message: { IMAGE_QUALITY_BLUR: `Sharpness ${gate.blur_laplacian_var} < ${gate.thresholds.blur_min}`, IMAGE_QUALITY_GLARE: `Glare ${(gate.glare_ratio * 100).toFixed(1)}% > ${gate.thresholds.glare_max * 100}%`, IMAGE_QUALITY_RESOLUTION: `${origW}×${origH} below minimum` }[code] ?? code }))
  else if (!calibrated) reasons.push({ code: 'MODEL_UNCALIBRATED', message: 'No calibrated golden reference for this capture source (mock backend only calibrates synthetic fixtures). Fails safe to HOLD.' })
  else {
    const d = goldenDiff(data, golden()); score = d.score; regions = d.regions
    const hsha = sha256Hex(d.heatmap); S.evidence[hsha] = d.heatmap; heat = `mock-evidence:${hsha}`
    if (score >= rej_t) reasons.push({ code: 'SCORE_ABOVE_REJECT', message: `Anomaly score ${score} ≥ reject threshold ${rej_t}` })
    else if (score >= hold_t) reasons.push({ code: 'SCORE_ABOVE_HOLD', message: `Anomaly score ${score} ≥ hold threshold ${hold_t}` })
  }
  const verdict = reasons.some(x => x.code === 'SCORE_ABOVE_REJECT') ? 'REJECT' : reasons.length ? 'HOLD' : 'PASS'
  const insp: Inspection = {
    id: nextId('insp'), unit_id: u.id, station_id: String(f.get('station_id') ?? ''), created_by: user.username, created_at: now(),
    evidence: { image_sha256: sha, image_url: `mock-evidence:${sha}`, heatmap_url: heat },
    quality_gate: gate,
    analysis: { method: 'golden_diff', method_version: 'gd-0.3', calibrated, score, hold_threshold: hold_t, reject_threshold: rej_t, threshold_tightened: contained, regions },
    verdict, reasons, passport: null, synthetic: !!fixture,
  }
  S.inspections.push(insp)
  u.quality_status = verdict
  if (score !== null) S.spc.push({ inspection_id: insp.id, t: insp.created_at, score, synthetic: !!fixture, line_id: lineOf(u), method_version: 'gd-0.3' })
  const pp = passportOf(u)
  if (verdict === 'PASS' && !pp) insp.passport = await issuePassport(u, insp, contained || u.held)
  else if (verdict === 'REJECT' && pp) { pp.status = 'REVOKED'; audit(user.username, 'PASSPORT_REVOKED', 'passport', pp.id, 'Unit rejected on reinspection') }
  else insp.passport = pp
  syncPassportHolds()
  audit(user.username, 'INSPECTION_RECORDED', 'unit', u.id, undefined, { inspection: insp.id, verdict, score: score ?? 'n/a', gate: gate.passed })
  if (insp.passport && verdict === 'PASS' && !pp) audit('system', 'PASSPORT_ISSUED', 'passport', insp.passport.id, undefined, { kid: insp.passport.kid })
  save()
  return insp
})
r('GET', '/inspections', ({ query }) => {
  const lot = query.get('lot_id'), uid = query.get('unit_id')
  return { items: S.inspections.filter(i => (!uid || i.unit_id === uid) && (!lot || unit(i.unit_id).lot_id === lot)).slice().reverse().slice(0, 100), next_cursor: null }
})
r('POST', '/inspections/:id/review', c => {
  const user = need(c, 'quality_engineer', 'quality_manager')
  const insp = S.inspections.find(i => i.id === c.params[0]) ?? nf('Inspection')
  const u = unit(insp.unit_id)
  if (insp.verdict !== 'HOLD' || u.quality_status !== 'HOLD') throw new HttpError(409, 'NOT_ON_HOLD', 'Only HOLD inspections can be reviewed')
  if (!c.body?.reason?.trim()) throw new HttpError(422, 'REASON_REQUIRED', 'A reason is required')
  insp.review = { action: c.body.action, reason: c.body.reason, by: user.username, at: now() }
  if (c.body.action === 'REJECT') { u.quality_status = 'REJECT'; const pp = passportOf(u); if (pp) pp.status = 'REVOKED' }
  audit(user.username, `HOLD_REVIEW_${c.body.action}`, 'unit', u.id, c.body.reason, { inspection: insp.id })
  save(); return insp
})

/* ── crypto ── */
// Same shape as the real API: validity window in Unix seconds.
const publicKeys = () => S.keys.map(({ priv: _p, ...k }) => ({ ...k, alg: 'Ed25519', not_before: unix(k.not_before), not_after: unix(k.not_after) }))
r('GET', '/keys', () => ({ keys: publicKeys() }))
r('POST', '/verify', async c => {
  need(c)
  const v = await verifyToken(String(c.body?.token ?? ''), publicKeys())
  const p: any = v.payload
  let exists = false, status: string | null = null, elig: Eligibility | null = null
  if (v.ok && p.typ === 'PP') { const pp = S.passports.find(x => x.id === p.id); exists = !!pp; status = pp?.status ?? null; if (pp) elig = eligibility(unit(pp.unit_id)) }
  if (v.ok && p.typ === 'PKG') { const k = S.packages.find(x => x.id === p.id); exists = !!k; status = k ? (k.held ? 'HELD' : k.state) : null }
  return { signature_valid: v.ok, failure: v.ok ? null : (v as any).code, kid: v.kid ?? null, typ: p?.typ ?? null, payload: p ?? null, exists, status, eligibility: elig }
})

/* ── 4M ── */
r('GET', '/four-m-events', () => ({ items: S.events.slice().reverse(), next_cursor: null }))
r('POST', '/four-m-events', c => {
  const user = need(c, 'quality_engineer', 'quality_manager')
  const b = c.body
  if (!['MAN', 'MACHINE', 'MATERIAL', 'METHOD'].includes(b?.category)) throw new HttpError(422, 'VALIDATION', 'category required')
  if (!b.description?.trim() || !b.reason?.trim()) throw new HttpError(422, 'VALIDATION', 'description and reason are required')
  const pol = S.policies.containment
  const effective = b.effective_at ?? now()
  const since = new Date(new Date(effective).getTime() - pol.lookback_minutes * 60_000)
  const lineLots = S.lots.filter(l => l.line_id === b.line_id)
  const affectedUnits = S.units.filter(u => lineLots.some(l => l.id === u.lot_id) && !isShipped(u) && u.quality_status !== 'REJECT' && new Date(u.created_at) >= since)
  const recheck = b.planned ? pol.recheck_existing_planned : pol.recheck_existing_unplanned
  const ev: FourMEvent = { id: nextId('fm'), code: `FM-${String(S.events.length + 1).padStart(4, '0')}`, category: b.category, planned: !!b.planned, line_id: b.line_id, description: b.description, reason: b.reason, effective_at: effective, created_by: user.username, created_at: now(), containment_id: '' }
  const cont: Containment = {
    id: nextId('cont'), event_id: ev.id, status: 'ACTIVE', line_id: b.line_id, created_at: now(),
    rule: { first_n: pol.first_n, lookback_minutes: pol.lookback_minutes, threshold_tightening: pol.threshold_tightening, recheck_existing: recheck },
    affected_lot_ids: [...new Set(affectedUnits.map(u => u.lot_id).concat(lineLots.map(l => l.id)))],
    affected_unit_ids: affectedUnits.map(u => u.id),
    obligations: [
      { type: 'FULL_INSPECT_FIRST_N', required: pol.first_n, completed: 0 },
      ...(recheck ? [{ type: 'RECHECK_UNIT' as const, required: affectedUnits.filter(u => u.quality_status === 'PASS').length, completed: 0, unit_ids: affectedUnits.filter(u => u.quality_status === 'PASS').map(u => u.id) }] : []),
    ],
  }
  ev.containment_id = cont.id
  S.events.push(ev); S.containments.push(cont)
  syncPassportHolds()
  audit(user.username, 'FOUR_M_EVENT_LOGGED', 'four_m_event', ev.id, b.reason, { category: b.category, planned: !!b.planned, line: b.line_id })
  audit('policy', 'CONTAINMENT_OPENED', 'containment', cont.id, `Auto-containment for ${ev.code}`, { affected_units: cont.affected_unit_ids.length })
  save(); return { event: ev, containment: containmentView(cont) }
})
r('GET', '/containments', () => ({ items: S.containments.slice().reverse().map(containmentView), next_cursor: null }))
r('GET', '/containments/:id', ({ params }) => containmentView(S.containments.find(c => c.id === params[0]) ?? nf('Containment')))
r('POST', '/containments/:id/clear', c => {
  const user = need(c, 'quality_manager')
  const cont = S.containments.find(x => x.id === c.params[0]) ?? nf('Containment')
  if (cont.status !== 'ACTIVE') throw new HttpError(409, 'NOT_ACTIVE', 'Containment already cleared')
  if (!c.body?.reason?.trim()) throw new HttpError(422, 'REASON_REQUIRED', 'A reason is required')
  const v = containmentView(cont)
  const unmet = v.obligations.filter(o => o.completed < o.required)
  if (unmet.length) throw new HttpError(409, 'CONTAINMENT_NOT_SATISFIED', `${unmet.length} obligation(s) unmet`, { unmet })
  Object.assign(cont, { status: 'CLEARED', cleared_by: user.username, cleared_at: now(), clear_reason: c.body.reason })
  syncPassportHolds()
  audit(user.username, 'CONTAINMENT_CLEARED', 'containment', cont.id, c.body.reason)
  save(); return containmentView(cont)
})

/* ── orders, packages, seals ── */
r('GET', '/orders', () => ({ items: S.orders, next_cursor: null }))
r('GET', '/orders/:id', ({ params }) => S.orders.find(o => o.id === params[0]) ?? nf('Order'))
const pkgView = (p: Package) => ({ ...p, units: p.unit_ids.map(id => unitView(unit(id))), shipment_id: shipmentOfPkg(p.id)?.id ?? null })
r('GET', '/packages', ({ query }) => ({ items: S.packages.filter(p => !query.get('order_id') || p.order_id === query.get('order_id')).map(pkgView), next_cursor: null }))
r('GET', '/packages/:id', ({ params }) => pkgView(S.packages.find(p => p.id === params[0] || p.label === params[0]) ?? nf('Package')))
r('POST', '/packages', c => {
  const user = need(c, 'operator')
  const o = S.orders.find(x => x.id === c.body?.order_id) ?? nf('Order')
  const p: Package = { id: nextId('pkg'), label: `PKG-${String(S.packages.length + 1).padStart(4, '0')}`, order_id: o.id, state: 'OPEN', unit_ids: [], held: false, hold_reasons: [], token: null, seal_ref_sha256: null, last_seal_check: null, created_at: now() }
  S.packages.push(p); audit(user.username, 'PACKAGE_CREATED', 'package', p.id); save(); return pkgView(p)
})
r('POST', '/packages/:id/items', c => {
  const user = need(c, 'operator')
  const p = S.packages.find(x => x.id === c.params[0]) ?? nf('Package')
  if (p.state !== 'OPEN') throw new HttpError(409, 'PACKAGE_NOT_OPEN', 'Package is sealed')
  const u = S.units.find(x => x.id === c.body?.unit_id || x.serial === c.body?.unit_id) ?? nf('Unit')
  const o = S.orders.find(x => x.id === p.order_id)!
  const blockers = eligibility(u).blockers.slice()
  if (u.part_id !== o.part_id) blockers.push({ code: 'WRONG_PART', message: 'Unit part does not match order' })
  if (u.package_id) blockers.push({ code: 'ALREADY_PACKED', message: 'Unit is already in a package' })
  if (blockers.length) throw new HttpError(409, 'UNIT_NOT_PACKABLE', `${u.serial} cannot be packed`, { blockers })
  p.unit_ids.push(u.id); u.package_id = p.id
  audit(user.username, 'UNIT_PACKED', 'package', p.id, undefined, { unit: u.id }); save(); return pkgView(p)
})
r('DELETE', '/packages/:id/items/:uid', c => {
  need(c, 'operator')
  const p = S.packages.find(x => x.id === c.params[0]) ?? nf('Package')
  if (p.state !== 'OPEN') throw new HttpError(409, 'PACKAGE_NOT_OPEN', 'Package is sealed')
  p.unit_ids = p.unit_ids.filter(x => x !== c.params[1]); unit(c.params[1]).package_id = null; save(); return pkgView(p)
})

async function sealImage(f: FormData) {
  const { data, origW, origH, dataUrl } = await loadImageData(f.get('image') as Blob)
  const gate = qualityGate(data, origW, origH)
  const sha = sha256Hex(dataUrl); S.evidence[sha] = dataUrl
  return { gate, sha }
}
r('POST', '/packages/:id/seal', async c => {
  const user = need(c, 'operator')
  const p = S.packages.find(x => x.id === c.params[0]) ?? nf('Package')
  if (p.state !== 'OPEN') throw new HttpError(409, 'PACKAGE_NOT_OPEN', 'Already sealed')
  if (!p.unit_ids.length) throw new HttpError(409, 'PACKAGE_EMPTY', 'Cannot seal an empty package')
  const { gate, sha } = await sealImage(c.body)
  if (!gate.passed) throw new HttpError(422, 'SEAL_REFERENCE_QUALITY', `Reference photo rejected: ${gate.failures.join(', ')}`)
  p.seal_ref_sha256 = sha
  const chk: SealCheck = { id: nextId('seal'), package_id: p.id, kind: 'ENROLL', result: null, method_version: 'seal-orb-ssim-0.2', metrics: { blur: gate.blur_laplacian_var }, explanation: 'Reference enrolled.', created_by: user.username, created_at: now(), synthetic: !!c.body.get('fixture') }
  S.seal_checks.push(chk); p.last_seal_check = chk
  await signPackage(p); p.state = 'SEALED'
  audit(user.username, 'SEAL_ENROLLED', 'package', p.id, undefined, { ref: sha.slice(0, 16) }); save(); return pkgView(p)
})
r('POST', '/packages/:id/seal-checks', async c => {
  const user = need(c, 'dispatcher', 'quality_engineer', 'quality_manager')
  const p = S.packages.find(x => x.id === c.params[0]) ?? nf('Package')
  if (!p.seal_ref_sha256) throw new HttpError(409, 'SEAL_NOT_ENROLLED', 'No seal reference enrolled')
  const fixture = c.body.get('fixture') ? String(c.body.get('fixture')) : null
  const { gate } = await sealImage(c.body)
  // Mock: real seal registration (ORB+RANSAC+SSIM) lives in the backend. Here, fixture captures map to their
  // documented outcome; real photos can't be registered by the mock and fail safe to UNVERIFIABLE.
  let result: SealCheck['result'], metrics: any, explanation: string
  if (!gate.passed || fixture === 'bad_light') { result = 'UNVERIFIABLE'; metrics = { inliers: 9, inlier_ratio: 0.08, ssim: 0 }; explanation = `Capture not usable (${gate.failures.join(', ') || 'too few keypoints: 9 < 40'}). Recapture required — not treated as tampering or intact.` }
  else if (fixture === 'torn') { result = 'SUSPICIOUS'; metrics = { inliers: 61, inlier_ratio: 0.43, ssim: 0.58 }; explanation = 'Pattern registered (61 inliers) but structural similarity 0.58 < 0.60 in the upper-left region. Escalate to QE.' }
  else if (fixture === 'intact') { result = 'INTACT'; metrics = { inliers: 176, inlier_ratio: 0.69, ssim: 0.84 }; explanation = 'Pattern registered with 176 inliers; SSIM 0.84 ≥ 0.72.' }
  else { result = 'UNVERIFIABLE'; metrics = { inliers: 0, inlier_ratio: 0, ssim: 0 }; explanation = 'Mock backend cannot register real seal photos. Live backend performs ORB+RANSAC+SSIM. Failing safe to UNVERIFIABLE.' }
  const chk: SealCheck = { id: nextId('seal'), package_id: p.id, kind: 'RECHECK', result, method_version: 'seal-orb-ssim-0.2', metrics: { ...metrics, thresholds: { min_inliers: 40, intact_ssim: 0.72, suspicious_ssim: 0.6 } }, explanation, created_by: user.username, created_at: now(), synthetic: !!fixture }
  S.seal_checks.push(chk); p.last_seal_check = chk
  if (result === 'SUSPICIOUS' && p.state === 'SEALED') p.state = 'SEAL_REVIEW'
  audit(user.username, 'SEAL_RECHECK', 'package', p.id, undefined, { result })
  save(); return chk
})

/* ── shipments & dispatch gate ── */
const remainingQty = (s: Shipment) => {
  const o = S.orders.find(x => x.id === s.order_id)!
  const other = S.shipments.filter(x => x.order_id === o.id && x.id !== s.id).flatMap(x => x.loaded_package_ids).reduce((n, id) => n + S.packages.find(p => p.id === id)!.unit_ids.length, 0)
  return o.qty_units - other
}
const loadedUnits = (s: Shipment) => s.loaded_package_ids.reduce((n, id) => n + S.packages.find(p => p.id === id)!.unit_ids.length, 0)
const shipView = (s: Shipment) => ({ ...s, expected_units: remainingQty(s), loaded_units: loadedUnits(s), scans: S.scans.filter(x => (x as any).shipment_id === s.id).slice().reverse() })
r('GET', '/shipments', () => ({ items: S.shipments.map(shipView), next_cursor: null }))
r('GET', '/shipments/:id', ({ params }) => shipView(S.shipments.find(s => s.id === params[0]) ?? nf('Shipment')))

export async function runGate(s: Shipment, token: string, actor: string, client_event_id: string, opts: { commit: boolean }): Promise<ScanDecision> {
  const checks: GateCheck[] = [], reasons: Reason[] = []
  const add = (gate: GateCheck['gate'], status: GateCheck['status'], detail?: string, code?: string, msg?: string) => {
    checks.push({ gate, status, detail }); if (code && status === 'FAIL') reasons.push({ code, message: msg ?? detail ?? code })
  }
  const v = await verifyToken(token, publicKeys())
  const p: any = v.payload
  let pkg: Package | undefined
  if (!v.ok && (v.code === 'TOKEN_MALFORMED' || v.code === 'SIG_INVALID')) add('SIGNATURE', 'FAIL', v.code === 'SIG_INVALID' ? 'Ed25519 signature does not match payload' : 'Not an AQ1 token', v.code)
  else add('SIGNATURE', 'PASS', 'Ed25519 · canonical payload')
  if (v.ok || v.code === 'TOKEN_MALFORMED' || v.code === 'SIG_INVALID') add('KEY', !v.ok && v.code === 'TOKEN_MALFORMED' ? 'SKIP' : 'PASS', v.kid ? `kid ${v.kid}` : undefined)
  else add('KEY', 'FAIL', `kid ${v.kid}`, v.code)
  const hardFail = reasons.length > 0
  if (hardFail) checks.push({ gate: 'IDENTITY', status: 'SKIP' })
  else {
    pkg = p.typ === 'PKG' ? S.packages.find(x => x.id === p.id) : undefined
    if (!pkg) add('IDENTITY', 'FAIL', p.typ !== 'PKG' ? `Token type ${p.typ} — scan the package label` : 'No such package', p.typ !== 'PKG' ? 'TOKEN_MISMATCH' : 'PACKAGE_NOT_FOUND')
    else {
      const items = pkg.unit_ids.map(u => unit(u).passport_id).sort()
      const same = JSON.stringify(items) === JSON.stringify(p.items) && p.order === pkg.order_id
      add('IDENTITY', same ? 'PASS' : 'FAIL', same ? `${pkg.label} · ${items.length} passports` : 'Signed contents differ from record', 'TOKEN_MISMATCH')
    }
  }
  const units = pkg ? pkg.unit_ids.map(unit) : []
  const skipRest = reasons.length > 0
  const gate = (name: GateCheck['gate'], fn: () => void) => skipRest ? checks.push({ gate: name, status: 'SKIP' }) : fn()
  gate('LIFECYCLE', () => {
    const notPass = units.filter(u => u.quality_status !== 'PASS'), revoked = units.filter(u => passportOf(u)?.status === 'REVOKED')
    if (!['SEALED', 'LOADED', 'SEAL_REVIEW'].includes(pkg!.state)) add('LIFECYCLE', 'FAIL', `Package is ${pkg!.state}`, 'PACKAGE_NOT_SEALED')
    else if (revoked.length) add('LIFECYCLE', 'FAIL', `${revoked.length} passport(s) revoked`, 'PASSPORT_REVOKED')
    else if (notPass.length) add('LIFECYCLE', 'FAIL', `${notPass.length} unit(s) not PASS`, 'UNIT_NOT_PASS')
    else add('LIFECYCLE', 'PASS', `${units.length} units PASS · passports valid`)
  })
  gate('CONTAINMENT', () => {
    const cs = [...new Set(units.flatMap(containmentsFor))]
    cs.length ? add('CONTAINMENT', 'FAIL', `${units.filter(u => containmentsFor(u).length).length} unit(s) under ${cs.map(evtCode).join(', ')}`, 'CONTAINMENT_ACTIVE', `4M containment ${cs.map(evtCode).join(', ')} active on affected units`) : add('CONTAINMENT', 'PASS', 'No active 4M containment')
  })
  gate('QUARANTINE', () => {
    const held = units.filter(u => u.held)
    pkg!.held || held.length ? add('QUARANTINE', 'FAIL', `${held.length} unit(s) under recall hold`, 'QUARANTINED') : add('QUARANTINE', 'PASS', 'Not quarantined')
  })
  gate('ORDER', () => {
    if (s.status !== 'OPEN') add('ORDER', 'FAIL', `Manifest ${s.status}`, 'MANIFEST_CLOSED')
    else if (pkg!.order_id !== s.order_id) add('ORDER', 'FAIL', `Package is for ${S.orders.find(o => o.id === pkg!.order_id)?.order_no}, shipment is ${S.orders.find(o => o.id === s.order_id)?.order_no}`, 'WRONG_ORDER')
    else add('ORDER', 'PASS', S.orders.find(o => o.id === s.order_id)!.order_no)
  })
  gate('DUPLICATE', () => {
    const where = shipmentOfPkg(pkg!.id)
    where ? add('DUPLICATE', 'FAIL', `Already loaded on ${where.code}`, 'ALREADY_LOADED') : add('DUPLICATE', 'PASS', 'First accepted load')
  })
  gate('SEAL', () => {
    const sc = pkg!.last_seal_check
    const maxAge = S.policies.seal_recheck_max_age_minutes
    if (!sc) add('SEAL', 'FAIL', 'No seal reference', 'SEAL_NOT_ENROLLED')
    else if (sc.kind === 'ENROLL' || minsSince(sc.created_at) > maxAge) add('SEAL', 'FAIL', `Recheck within ${maxAge} min required`, 'SEAL_RECHECK_REQUIRED')
    else if (sc.result === 'SUSPICIOUS') add('SEAL', 'FAIL', sc.explanation, 'SEAL_SUSPICIOUS')
    else if (sc.result === 'UNVERIFIABLE') add('SEAL', 'FAIL', 'Last recheck unverifiable — recapture', 'SEAL_UNVERIFIABLE')
    else add('SEAL', 'PASS', `INTACT · ${Math.round(minsSince(sc.created_at))} min ago`)
  })
  gate('QUANTITY', () => {
    const after = loadedUnits(s) + units.length, max = remainingQty(s)
    after > max ? add('QUANTITY', 'FAIL', `${after} > ${max} units remaining on order`, 'ORDER_QTY_EXCEEDED') : add('QUANTITY', 'PASS', `${after} / ${max} units`)
  })
  gate('SEQUENCE', () => add('SEQUENCE', 'PASS', 'Seen at packing → seal → dock'))

  const decision: ScanDecision = {
    scan_id: nextId('scan'), decision: reasons.length ? 'BLOCKED' : 'ACCEPTED', replay: false, package_id: pkg?.id ?? null, package_label: pkg?.label ?? (p?.label ? `${p.label}? (unverified claim)` : null),
    reasons, checks, shipment_progress: { loaded_units: loadedUnits(s), expected_units: remainingQty(s), loaded_packages: s.loaded_package_ids.length },
    decided_at: now(), decided_by: actor, client_event_id,
  }
  if (opts.commit) {
    if (decision.decision === 'ACCEPTED') {
      s.loaded_package_ids.push(pkg!.id); pkg!.state = 'LOADED'
      decision.shipment_progress = { loaded_units: loadedUnits(s), expected_units: remainingQty(s), loaded_packages: s.loaded_package_ids.length }
    }
    ;(decision as any).shipment_id = s.id
    S.scans.push(decision)
    audit(actor, decision.decision === 'ACCEPTED' ? 'LOAD_ACCEPTED' : 'LOAD_BLOCKED', 'shipment', s.id, reasons.map(x => x.code).join(',') || undefined, { package: pkg?.id ?? 'unknown', client_event_id })
  }
  return decision
}

r('POST', '/shipments/:id/scans', async c => {
  const user = need(c, 'dispatcher')
  const s = S.shipments.find(x => x.id === c.params[0]) ?? nf('Shipment')
  const ceid = String(c.body?.client_event_id ?? '')
  if (!ceid) throw new HttpError(422, 'VALIDATION', 'client_event_id required')
  const prior = S.scans.find(x => x.client_event_id === ceid)
  if (prior) return { ...prior, replay: true }
  const d = await runGate(s, String(c.body?.token ?? ''), user.username, ceid, { commit: true })
  save(); return d
})
r('POST', '/shipments/:id/close', c => {
  const user = need(c, 'dispatcher')
  const s = S.shipments.find(x => x.id === c.params[0]) ?? nf('Shipment')
  if (s.status !== 'OPEN') throw new HttpError(409, 'MANIFEST_CLOSED', 'Already closed')
  const problems: Reason[] = []
  const loaded = loadedUnits(s), exp = remainingQty(s)
  if (loaded !== exp && !S.policies.allow_partial_close) problems.push({ code: 'QTY_MISMATCH', message: `${loaded} loaded ≠ ${exp} expected` })
  const held = s.loaded_package_ids.map(id => S.packages.find(p => p.id === id)!).filter(p => p.held)
  if (held.length) problems.push({ code: 'LOADED_PACKAGE_HELD', message: `${held.map(p => p.label).join(', ')} held after loading — unload required` })
  if (c.body?.unsynced_offline_events) problems.push({ code: 'PROVISIONAL_PENDING', message: `${c.body.unsynced_offline_events} offline scan(s) not yet synced` })
  if (problems.length) throw new HttpError(409, 'MANIFEST_NOT_RECONCILED', 'Manifest cannot close', { blockers: problems })
  s.status = 'CLOSED'; audit(user.username, 'MANIFEST_CLOSED', 'shipment', s.id, undefined, { units: loaded }); save(); return shipView(s)
})
r('POST', '/shipments/:id/events', c => {
  const user = need(c, 'dispatcher')
  const s = S.shipments.find(x => x.id === c.params[0]) ?? nf('Shipment')
  const type = c.body?.type
  if (type === 'DEPARTED' && s.status !== 'CLOSED') throw new HttpError(409, 'MANIFEST_OPEN', 'Close the manifest before departure')
  if (type === 'DELIVERED' && s.status !== 'IN_TRANSIT') throw new HttpError(409, 'NOT_IN_TRANSIT', 'Shipment has not departed')
  s.movements.push({ type, location: c.body.location ?? '—', occurred_at: now() })
  if (type === 'DEPARTED') { s.status = 'IN_TRANSIT'; s.loaded_package_ids.forEach(id => (S.packages.find(p => p.id === id)!.state = 'IN_TRANSIT')) }
  if (type === 'DELIVERED') { s.status = 'DELIVERED'; s.loaded_package_ids.forEach(id => (S.packages.find(p => p.id === id)!.state = 'DELIVERED')) }
  audit(user.username, `SHIPMENT_${type}`, 'shipment', s.id); save(); return shipView(s)
})

/* ── trace & recall ── */
function unitsInScope(scope: { type: string; id: string }): string[] {
  switch (scope.type) {
    case 'HEAT': return S.units.filter(u => S.lots.find(l => l.id === u.lot_id)!.heat_ids.includes(scope.id)).map(u => u.id)
    case 'LOT': return S.units.filter(u => u.lot_id === scope.id).map(u => u.id)
    case 'UNIT': return [scope.id]
    case 'PACKAGE': return S.packages.find(p => p.id === scope.id)?.unit_ids ?? []
  }
  return []
}
const scopeLabel = (t: string, id: string) => t === 'HEAT' ? S.heats.find(h => h.id === id)?.heat_code : t === 'LOT' ? S.lots.find(l => l.id === id)?.lot_code : t === 'UNIT' ? S.units.find(u => u.id === id)?.serial : S.packages.find(p => p.id === id)?.label
function resolveScope(q: { type?: string; id?: string; code?: string }) {
  const code = q.code ?? q.id ?? ''
  const h = S.heats.find(x => x.id === code || x.heat_code === code); if (h && (!q.type || q.type === 'HEAT')) return { type: 'HEAT', id: h.id }
  const l = S.lots.find(x => x.id === code || x.lot_code === code); if (l && (!q.type || q.type === 'LOT')) return { type: 'LOT', id: l.id }
  const p = S.packages.find(x => x.id === code || x.label === code); if (p && (!q.type || q.type === 'PACKAGE')) return { type: 'PACKAGE', id: p.id }
  const u = S.units.find(x => x.id === code || x.serial === code); if (u && (!q.type || q.type === 'UNIT')) return { type: 'UNIT', id: u.id }
  const pp = S.passports.find(x => x.id === code); if (pp) return { type: 'UNIT', id: pp.unit_id }
  return nf(`“${code}”`)
}
function impact(unitIds: string[]): RecallImpact {
  const us = unitIds.map(unit)
  const pkgs = [...new Set(us.map(u => u.package_id).filter(Boolean) as string[])].map(id => S.packages.find(p => p.id === id)!)
  const out: RecallImpact = { units: us.length, passports_held: us.filter(u => passportOf(u) && passportOf(u)!.status !== 'REVOKED').length, packages_held: 0, blocked_at_plant: [], loaded_not_departed: [], in_transit: [], delivered: [] }
  const byShip = new Map<string, number>()
  for (const p of pkgs) {
    const n = p.unit_ids.filter(id => unitIds.includes(id)).length
    const s = shipmentOfPkg(p.id)
    if (!s) { out.blocked_at_plant.push({ package_id: p.id, label: p.label, units: n }); out.packages_held++ }
    else byShip.set(s.id, (byShip.get(s.id) ?? 0) + n)
  }
  for (const [sid, n] of byShip) {
    const s = S.shipments.find(x => x.id === sid)!; const cus = S.customers.find(c => c.id === S.orders.find(o => o.id === s.order_id)!.customer_id)!
    if (s.status === 'OPEN' || s.status === 'CLOSED') { out.loaded_not_departed.push({ shipment_id: sid, code: s.code, units: n, action: 'UNLOAD_AND_HOLD' }); out.packages_held += s.loaded_package_ids.length }
    else if (s.status === 'IN_TRANSIT') out.in_transit.push({ shipment_id: sid, code: s.code, vehicle_no: s.vehicle_no, customer: `${cus.name} – ${cus.city}`, units: n, action: 'NOTIFY_AND_INTERCEPT' })
    else out.delivered.push({ shipment_id: sid, code: s.code, customer: `${cus.name} – ${cus.city}`, units: n, action: 'NOTIFY_CUSTOMER' })
  }
  return out
}
r('POST', '/recalls/preview', c => {
  need(c, 'quality_engineer', 'quality_manager')
  const scope = resolveScope(c.body?.scope ?? {})
  const t0 = performance.now(); const ids = unitsInScope(scope); const a = impact(ids)
  const rc: Recall = { id: 'preview', preview: true, scope: { ...scope, label: scopeLabel(scope.type, scope.id)! } as any, reason: '', executed_by: null, executed_at: null, query_ms: Math.round((performance.now() - t0) * 10) / 10, affected: a, evidence: { genealogy_query: 'heat → lot_heats → lot → unit → package_item → package → accepted scan → shipment → movement events', as_of: now() }, audit_event_ids: [] }
  return rc
})
r('POST', '/recalls', c => {
  const user = need(c, 'quality_manager')
  if (!c.body?.reason?.trim()) throw new HttpError(422, 'REASON_REQUIRED', 'A reason is required')
  const scope = resolveScope(c.body?.scope ?? {})
  const t0 = performance.now()
  const ids = unitsInScope(scope); const a = impact(ids)
  const auditIds: string[] = []
  for (const id of ids) unit(id).held = true
  const pkgIds = new Set(ids.map(id => unit(id).package_id).filter(Boolean) as string[])
  for (const pid of pkgIds) {
    const p = S.packages.find(x => x.id === pid)!
    if (p.state === 'IN_TRANSIT' || p.state === 'DELIVERED') continue // cannot be blocked at loading any more — notify only
    p.held = true; p.hold_reasons.push({ code: 'QUARANTINED', message: `Recall of ${scopeLabel(scope.type, scope.id)}: ${c.body.reason}` })
  }
  if (scope.type === 'HEAT') { S.lotHeld['heat:' + scope.id] = true; S.lots.filter(l => l.heat_ids.includes(scope.id)).forEach(l => (S.lotHeld[l.id] = true)) }
  if (scope.type === 'LOT') S.lotHeld[scope.id] = true
  syncPassportHolds()
  const rc: Recall = { id: nextId('rcl'), scope: { ...scope, label: scopeLabel(scope.type, scope.id)! } as any, reason: c.body.reason, executed_by: user.username, executed_at: now(), query_ms: 0, affected: a, evidence: { genealogy_query: 'heat → lot_heats → lot → unit → package_item → package → accepted scan → shipment → movement events', as_of: now() }, audit_event_ids: auditIds }
  const label = scopeLabel(scope.type, scope.id)!
  const task = (type: SegregationTask['type'], shipment_id: string, action: string, pkg?: { id: string; label: string }): SegregationTask => ({
    id: nextId('task'), type, status: 'OPEN', reason: `Recall ${label}: ${c.body.reason}`, package_id: pkg?.id ?? null, package_label: pkg?.label ?? null,
    shipment_id, shipment_code: S.shipments.find(x => x.id === shipment_id)?.code ?? null, recall_id: rc.id, scan_id: null, consignee_action: { action },
    created_at: now(), completed_by: null, completed_at: null, completion_reason: null })
  const tasks: SegregationTask[] = [
    ...a.loaded_not_departed.flatMap(l => S.shipments.find(x => x.id === l.shipment_id)!.loaded_package_ids.filter(pid => S.packages.find(p => p.id === pid)!.unit_ids.some(u => ids.includes(u)))
      .map(pid => task('REMOVAL_REQUIRED', l.shipment_id, 'Physically unload and segregate the package before departure', S.packages.find(p => p.id === pid)))),
    ...a.in_transit.map(t => task('IN_TRANSIT_RECALL', t.shipment_id, `Contact the carrier (${t.vehicle_no}) and ${t.customer}; quarantine on arrival`)),
    ...a.delivered.map(d => task('NOTIFY_CUSTOMER', d.shipment_id, `Notify ${d.customer} to quarantine ${d.units} delivered unit(s)`)),
  ]
  ;(S.tasks ??= []).push(...tasks)
  Object.assign(rc, { task_ids: tasks.map(t => t.id), unit_ids: ids, released_at: null, released_by: null, release_reason: null })
  auditIds.push(audit(user.username, 'RECALL_EXECUTED', 'recall', rc.id, c.body.reason, { scope: scope.type, target: rc.scope.label, units: a.units, packages_held: a.packages_held }).id)
  for (const t of a.in_transit) auditIds.push(audit('policy', 'RECALL_NOTIFY_IN_TRANSIT', 'shipment', t.shipment_id, undefined, { units: t.units }).id)
  for (const d of a.delivered) auditIds.push(audit('policy', 'RECALL_NOTIFY_DELIVERED', 'shipment', d.shipment_id, undefined, { units: d.units }).id)
  rc.query_ms = Math.round((performance.now() - t0) * 10) / 10
  S.recalls.push(rc); save(); return rc
})
const recallView = (rc: Recall) => ({ ...rc, segregation_tasks: ((rc as any).task_ids ?? []).map((id: string) => (S.tasks ?? []).find(t => t.id === id)).filter(Boolean) })
r('GET', '/recalls', () => ({ items: S.recalls.slice().reverse().map(recallView), next_cursor: null }))
r('GET', '/recalls/:id', ({ params }) => recallView(S.recalls.find(x => x.id === params[0]) ?? nf('Recall')))
r('POST', '/recalls/:id/release', c => {
  const user = need(c, 'quality_manager')
  const rc = S.recalls.find(x => x.id === c.params[0]) ?? nf('Recall')
  if (!c.body?.reason?.trim()) throw new HttpError(422, 'REASON_REQUIRED', 'A release reason is required')
  if (rc.released_at) throw new HttpError(409, 'RECALL_RELEASED', 'Recall already released')
  const ids: string[] = (rc as any).unit_ids ?? unitsInScope(rc.scope)
  const stillHeld = new Set(S.recalls.filter(x => x.id !== rc.id && x.executed_at && !x.released_at).flatMap(x => (x as any).unit_ids ?? []))
  for (const id of ids) if (!stillHeld.has(id)) unit(id).held = false
  for (const p of S.packages) if (p.held && p.unit_ids.every(u => !unit(u).held)) { p.held = false; p.hold_reasons = p.hold_reasons.filter(h => h.code !== 'QUARANTINED') }
  if (rc.scope.type === 'HEAT') { delete S.lotHeld['heat:' + rc.scope.id]; S.lots.filter(l => l.heat_ids.includes(rc.scope.id)).forEach(l => delete S.lotHeld[l.id]) }
  if (rc.scope.type === 'LOT') delete S.lotHeld[rc.scope.id]
  syncPassportHolds()
  Object.assign(rc, { released_at: now(), released_by: user.username, release_reason: c.body.reason })
  audit(user.username, 'RECALL_RELEASED', 'recall', rc.id, c.body.reason)
  save()
  return { ...recallView(rc), passports_reactivated: ids.filter(id => passportOf(unit(id))?.status === 'ACTIVE').length }
})
r('GET', '/segregation-tasks', ({ query }) => ({ items: (S.tasks ?? []).filter(t => !query.get('status') || t.status === query.get('status')).slice().reverse(), next_cursor: null }))
r('POST', '/segregation-tasks/:id/complete', c => {
  const user = need(c, 'dispatcher', 'quality_engineer', 'quality_manager')
  const t = (S.tasks ?? []).find(x => x.id === c.params[0]) ?? nf('Task')
  if (!c.body?.reason?.trim()) throw new HttpError(422, 'REASON_REQUIRED', 'Say what was done')
  if (t.status !== 'OPEN') throw new HttpError(409, 'TASK_COMPLETED', 'Task already completed')
  Object.assign(t, { status: 'COMPLETED', completed_by: user.username, completed_at: now(), completion_reason: c.body.reason })
  audit(user.username, 'SEGREGATION_TASK_COMPLETED', 'task', t.id, c.body.reason); save(); return t
})

/* ── 8D reports (template drafts; PDF rendering is live-backend only) ── */
function draft8d(type: string, id: string): Pick<Report8D, 'meta' | 'counts' | 'sections'> {
  const pending = (text: string): ReportItem => ({ kind: 'PENDING_HUMAN', text, evidence: [] })
  const fact = (text: string, ev: ReportItem['evidence']): ReportItem => ({ kind: 'FACT', text, evidence: ev })
  if (type === 'RECALL') {
    const rc = S.recalls.find(x => x.id === id) ?? nf('Recall')
    const ev = [{ type: 'RECALL', id: rc.id, label: `Recall of ${rc.scope.type.toLowerCase()} ${rc.scope.label}` }]
    const tasks = recallView(rc).segregation_tasks as SegregationTask[]
    const sum = (xs: { units: number }[]) => xs.reduce((n, x) => n + x.units, 0)
    return {
      meta: { source_label: ev[0].label }, counts: { affected_units: rc.affected.units, held_packages: rc.affected.packages_held },
      sections: [
        { key: 'D1', title: 'Team', items: [pending('Name the recall team and the supplier contact.')] },
        { key: 'D2', title: 'Problem description', items: [fact(`Quarantine of ${rc.scope.type.toLowerCase()} ${rc.scope.label}: ${rc.reason}`, ev),
          fact(`${rc.affected.units} units affected: ${sum(rc.affected.in_transit)} in transit, ${sum(rc.affected.delivered)} delivered.`, ev)] },
        { key: 'D3', title: 'Interim containment', items: [fact(`${rc.affected.packages_held} package(s) held; ${tasks.length} physical action task(s) raised.`, tasks.map(t => ({ type: 'TASK', id: t.id, label: `${t.type} ${t.status}` })))] },
        { key: 'D4', title: 'Root cause', items: [pending('Root cause at the material/supplier level not yet verified.')] },
        { key: 'D5', title: 'Permanent corrective action', items: [pending('Define the permanent corrective action with the supplier.')] },
        { key: 'D6', title: 'Verification of effectiveness', items: [pending('Verification pending.')] },
        { key: 'D7', title: 'Prevent recurrence', items: [pending('Recurrence prevention pending.')] },
        { key: 'D8', title: 'Closure and recognition', items: [pending('Closure requires quality-manager approval with review evidence.')] },
      ],
    }
  }
  const ev = S.events.find(e => e.id === id || e.code === id) ?? nf('4M event')
  const cont = S.containments.find(x => x.id === ev.containment_id)
  const evd = [{ type: 'FOUR_M', id: ev.id, label: `${ev.code} ${ev.category}` }]
  return {
    meta: { source_label: `4M change point ${ev.code}` }, counts: { affected_units: cont?.affected_unit_ids.length ?? 0 },
    sections: [
      { key: 'D1', title: 'Team', items: [pending('Name the line team and maintenance owner.')] },
      { key: 'D2', title: 'Problem description', items: [fact(`${ev.planned ? 'Planned' : 'Unplanned'} ${ev.category} change on ${S.lines.find(l => l.id === ev.line_id)?.name}: ${ev.description} (${ev.reason}).`, evd)] },
      { key: 'D3', title: 'Interim containment', items: [fact(`${cont?.affected_unit_ids.length ?? 0} unshipped unit(s) held; containment ${cont?.status ?? 'unknown'}.`, evd)] },
      { key: 'D4', title: 'Root cause', items: [{ kind: 'HYPOTHESIS', text: `The ${ev.category.toLowerCase()} change may have altered the process; not verified.`, evidence: evd }] },
      { key: 'D5', title: 'Permanent corrective action', items: [pending('Define the corrective action.')] },
      { key: 'D6', title: 'Verification of effectiveness', items: [pending('Verification pending.')] },
      { key: 'D7', title: 'Prevent recurrence', items: [{ kind: 'PROPOSED', text: 'Require first-off approval before restarting after this kind of change.', evidence: [] }] },
      { key: 'D8', title: 'Closure and recognition', items: [pending('Closure requires quality-manager approval with review evidence.')] },
    ],
  }
}
const reportView = (x: Report8D) => x
r('POST', '/reports/8d', c => {
  const user = need(c, 'quality_engineer', 'quality_manager')
  const src = c.body?.source ?? {}
  if (!['RECALL', 'FOUR_M'].includes(src.type)) throw new HttpError(422, 'VALIDATION', 'Mock supports RECALL and FOUR_M sources')
  const body = draft8d(src.type, src.id)
  const rep: Report8D = { id: nextId('rpt'), revision_id: nextId('rrev'), status: 'DRAFT', source_type: src.type, source_id: src.id, generator: 'template-8d-0.1',
    created_at: now(), as_of: now(), evidence_source: 'MOCK', approved_by: null, approved_at: null, snapshot_sha256: '', pdf_url: '', pdf_sha256: null, revisions: 1, ...body }
  rep.snapshot_sha256 = sha256Hex(JSON.stringify(body))
  S.reports.push(rep); audit(user.username, 'REPORT_DRAFTED', 'report', rep.id, undefined, { source: `${src.type}:${src.id}` }); save(); return reportView(rep)
})
r('GET', '/reports', () => ({ items: S.reports.slice().reverse().map(reportView), next_cursor: null }))
r('GET', '/reports/:id', ({ params }) => reportView(S.reports.find(x => x.id === params[0]) ?? nf('Report')))
r('POST', '/reports/:id/approve', c => {
  const user = need(c, 'quality_manager')
  const rep: Report8D = S.reports.find(x => x.id === c.params[0]) ?? nf('Report')
  if (rep.status === 'APPROVED') throw new HttpError(409, 'REPORT_APPROVED', 'Report already approved')
  const ev: string[] = c.body?.review_evidence_ids ?? []
  if (!ev.length || ev.some(s => !S.evidence[s])) throw new HttpError(409, 'REPORT_INCOMPLETE', 'Approval requires review evidence in the evidence store')
  Object.assign(rep, { status: 'APPROVED', approved_by: user.username, approved_at: now(), revisions: rep.revisions + 1, revision_id: nextId('rrev') })
  rep.snapshot_sha256 = sha256Hex(JSON.stringify(rep.sections) + rep.approved_at)
  audit(user.username, 'REPORT_APPROVED', 'report', rep.id, c.body.reason, { review_evidence: ev.join(',') }); save(); return rep
})
r('POST', '/evidence', async c => {
  const user = need(c, 'quality_engineer', 'quality_manager')
  const f = (c.body as FormData).get('file') as Blob
  const bytes = new Uint8Array(await f.arrayBuffer())
  const sha = sha256Hex(bytes); S.evidence[sha] = `data:${f.type || 'application/octet-stream'};base64,${btoa(String.fromCharCode(...bytes.slice(0, 2_000_000)))}`
  audit(user.username, 'EVIDENCE_UPLOADED', 'evidence', sha); save()
  return { sha256: sha, media_type: f.type, byte_size: bytes.length, url: `mock-evidence:${sha}`, evidence_source: 'UPLOAD', purpose: 'REVIEW_RECORD' }
})

r('GET', '/trace', ({ query }) => {
  const code = query.get('q') ?? query.get('heat') ?? query.get('lot') ?? query.get('unit') ?? query.get('package') ?? query.get('serial') ?? ''
  const scope = resolveScope({ code })
  const ids = unitsInScope(scope); const us = ids.map(unit)
  const nodes = new Map<string, TraceNode>(); const edges: TraceEdge[] = []; const seen = new Set<string>()
  const node = (n: TraceNode) => { if (!nodes.has(n.id)) nodes.set(n.id, n) }
  const edge = (from: string, to: string, type: string, meta?: any) => { const k = `${from}>${to}`; if (seen.has(k)) { const e = edges.find(x => x.from === from && x.to === to) as any; if (e?.units) e.units++; return } seen.add(k); edges.push({ from, to, type, ...(meta ?? {}) }) }
  const showUnits = scope.type === 'UNIT' || scope.type === 'PACKAGE'
  for (const u of us) {
    const lot = S.lots.find(l => l.id === u.lot_id)!
    node({ id: lot.id, type: 'LOT', label: lot.lot_code, status: lotView(lot).containment_ids.length ? 'CONTAINMENT_ACTIVE' : undefined, held: !!S.lotHeld[lot.id], meta: { line: S.lines.find(l => l.id === lot.line_id)?.name } })
    for (const hid of lot.heat_ids) {
      const h = S.heats.find(x => x.id === hid)!; const sup = S.suppliers.find(x => x.id === h.supplier_id)!
      node({ id: sup.id, type: 'SUPPLIER', label: sup.name, meta: { code: sup.code } })
      node({ id: h.id, type: 'HEAT', label: h.heat_code, held: !!S.lotHeld['heat:' + h.id], meta: { grade: h.material_grade, cert: h.certificate_sha256.slice(0, 12) } })
      edge(sup.id, h.id, 'SUPPLIED'); edge(h.id, lot.id, 'MADE_FROM')
    }
    let tail = lot.id
    if (showUnits) {
      node({ id: u.id, type: 'UNIT', label: u.serial, status: u.quality_status, held: u.held }); edge(lot.id, u.id, 'CONTAINS'); tail = u.id
      const pp = passportOf(u); if (pp) { node({ id: pp.id, type: 'PASSPORT', label: pp.id, status: pp.status }); edge(u.id, pp.id, 'CERTIFIED_BY') }
    }
    if (!u.package_id) continue
    const p = S.packages.find(x => x.id === u.package_id)!
    node({ id: p.id, type: 'PACKAGE', label: p.label, status: p.state, held: p.held, meta: { units: p.unit_ids.length } })
    edge(tail, p.id, 'PACKED_IN', { units: 1 })
    const s = shipmentOfPkg(p.id)
    if (s) {
      const cus = S.customers.find(c => c.id === S.orders.find(o => o.id === s.order_id)!.customer_id)!
      node({ id: s.id, type: 'SHIPMENT', label: s.code, status: s.status, meta: { vehicle: s.vehicle_no } })
      node({ id: cus.id, type: 'CUSTOMER', label: cus.name, meta: { city: cus.city } })
      edge(p.id, s.id, 'LOADED_ON'); edge(s.id, cus.id, s.status === 'DELIVERED' ? 'DELIVERED_TO' : 'BOUND_FOR')
    }
  }
  const a = impact(ids)
  const g: TraceGraph = { root: { type: scope.type as any, id: scope.id, label: scopeLabel(scope.type, scope.id)! }, nodes: [...nodes.values()], edges,
    summary: { units: ids.length, packages: new Set(us.map(u => u.package_id).filter(Boolean)).size, at_plant: us.filter(u => !u.package_id || !shipmentOfPkg(u.package_id)).length, loaded: a.loaded_not_departed.reduce((n, x) => n + x.units, 0), in_transit: a.in_transit.reduce((n, x) => n + x.units, 0), delivered: a.delivered.reduce((n, x) => n + x.units, 0) } }
  return g
})

/* ── SPC ── */
function computeSpc(line: string, mv: string): SpcSeries {
  const pts = S.spc.filter(p => p.line_id === line && p.method_version === mv).sort((a, b) => a.t.localeCompare(b.t))
  const N = 30, k = 0.5, h = 5
  const base = pts.slice(0, N).map(p => p.score)
  const mean = base.reduce((a, b) => a + b, 0) / (base.length || 1)
  const std = Math.sqrt(base.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, base.length - 1))
  const status = base.length < N ? 'COLLECTING' : std < 0.005 ? 'LOW_VARIANCE' : 'READY'
  let cs = 0; const alerts: SpcSeries['alerts'] = []
  const points = pts.map((p, i) => {
    const z = status === 'READY' ? (p.score - mean) / std : 0
    if (i >= N && status === 'READY') { cs = Math.max(0, cs + z - k); if (cs > h && !alerts.length) alerts.push({ id: `alert_spc_${line}_${i}`, at_index: i, message: `Suspected upward drift: CUSUM ${cs.toFixed(1)}σ > ${h}σ (k=${k}σ). Inspect tooling / process — this is not a defect prediction.` }) }
    return { inspection_id: p.inspection_id, t: p.t, score: p.score, z: Math.round(z * 100) / 100, cusum: Math.round(cs * 100) / 100, synthetic: p.synthetic }
  })
  const series: SpcSeries = { part_id: 'part_44120', line_id: line, method_version: mv, baseline: { n: base.length, mean: Math.round(mean * 1000) / 1000, std: Math.round(std * 1000) / 1000, frozen_at: pts[N - 1]?.t ?? null, status }, rule: { type: 'CUSUM_UPPER', k_sigma: k, h_sigma: h }, points, alerts }
  return series
}
r('GET', '/spc/series', ({ query }) => computeSpc(query.get('line_id') ?? 'line_2', query.get('method_version') ?? 'gd-0.3'))
const openAlerts = () => S.lines.flatMap(l => { const ser = computeSpc(l.id, 'gd-0.3'); return ser.alerts.filter(a => !S.alerts_acked.includes(a.id)).map(a => ({ ...a, type: 'SPC_DRIFT', line_id: l.id, created_at: ser.points[a.at_index ?? 0].t })) })
r('GET', '/alerts', () => ({ items: openAlerts(), next_cursor: null }))
r('POST', '/alerts/:id/ack', c => { const u = need(c, 'quality_engineer', 'quality_manager'); S.alerts_acked.push(c.params[0]); audit(u.username, 'ALERT_ACKNOWLEDGED', 'alert', c.params[0], c.body?.reason); save(); return { ok: true } })

/* ── audit & dashboard ── */
r('GET', '/audit', ({ query }) => ({ items: S.audit.filter(e => !query.get('entity_id') || e.entity_id === query.get('entity_id')).slice().reverse().slice(0, 200), next_cursor: null }))
r('GET', '/audit/verify', () => verifyAudit())
r('GET', '/dashboard/summary', () => {
  const by = { UNINSPECTED: 0, PASS: 0, HOLD: 0, REJECT: 0 }; S.units.forEach(u => by[u.quality_status]++)
  const done = S.inspections.length ? S.inspections.filter(i => i.verdict === 'PASS').length / S.inspections.length : null
  return { units_by_status: by, active_containments: activeContainments().length, held_packages: S.packages.filter(p => p.held || p.state === 'SEAL_REVIEW').length, open_shipments: S.shipments.filter(s => s.status === 'OPEN').length, open_alerts: openAlerts().length, open_tasks: (S.tasks ?? []).filter(t => t.status === 'OPEN').length, recent_scans: S.scans.slice(-8).reverse(), dispatch_eligible_units: S.units.filter(u => eligibility(u).dispatch_eligible && !isShipped(u)).length, first_pass_yield: done }
})

/* ── offline ── */
r('GET', '/offline/snapshot', async c => {
  need(c, 'dispatcher')
  const s = S.shipments.find(x => x.id === c.query.get('shipment_id')) ?? nf('Shipment')
  const ttl = S.policies.snapshot_ttl_minutes * 60
  const allowed = S.packages.filter(p => p.order_id === s.order_id && !shipmentOfPkg(p.id)).map(p => p.id)
  const held = S.packages.filter(p => p.held || p.unit_ids.some(u => containmentsFor(unit(u)).length || unit(u).held)).map(p => p.id)
  const token = await sign({ typ: 'SNAP', id: nextId('snap'), exp: Math.floor(Date.now() / 1000) + ttl, shipment: s.id, order: s.order_id, expected_units: remainingQty(s), allowed_packages: allowed, held, revoked_kids: S.keys.filter(k => k.status === 'REVOKED').map(k => k.kid), keys: publicKeys().filter(k => k.status !== 'REVOKED').map(({ kid, public_key, status, not_before, not_after }) => ({ kid, public_key, status, not_before, not_after })) })
  return { token }
})
// Settlement/recovery are enforced by the live backend; the mock accepts them so the scanner flow works offline-only.
r('POST', '/offline/snapshots/:id/settle', c => { need(c, 'dispatcher'); return { snapshot_id: c.params[0], settlement: 'ACKNOWLEDGED' } })
r('POST', '/offline/snapshots/:id/recover', c => { need(c, 'quality_manager'); return { snapshot_id: c.params[0], settlement: 'RECOVERED' } })
r('POST', '/sync/scans', async c => {
  const user = need(c, 'dispatcher')
  const results = []
  const dev = String(c.body?.device_id ?? '')
  S.device_heads ??= {}
  const known = S.device_heads[dev] ?? { head: GENESIS, seq: 0 }
  const fresh = ((c.body?.events ?? []) as any[]).filter(e => e.seq > known.seq).sort((a, b) => a.seq - b.seq)
  const chain = verifyChain(fresh, known.head)
  if (!chain.ok) {
    audit(user.username, 'DEVICE_CHAIN_BROKEN', 'device', dev, `Event chain broken at seq ${chain.broken_at}`)
    save()
    return { results: [], chain: { ok: false, broken_at: chain.broken_at, server_head: known.head } }
  }
  if (fresh.length) S.device_heads[dev] = { head: fresh.at(-1).hash, seq: fresh.at(-1).seq }
  for (const e of c.body?.events ?? []) {
    const prior = S.scans.find(x => x.client_event_id === e.client_event_id)
    if (prior) { results.push({ client_event_id: e.client_event_id, outcome: 'DUPLICATE', scan: prior, note: 'Already synced' }); continue }
    const s = S.shipments.find(x => x.id === e.shipment_id); if (!s) { results.push({ client_event_id: e.client_event_id, outcome: 'BLOCKED', note: 'Unknown shipment' }); continue }
    if (e.local_decision !== 'PROVISIONAL_ACCEPT') {
      // Blocked on the device → never physically loaded. Record the attempt; never count it.
      const d = await runGate(s, e.token, user.username, e.client_event_id, { commit: false })
      const rec = { ...d, decision: 'BLOCKED' as const, offline: true, reasons: e.local_reasons?.length ? e.local_reasons : d.reasons, shipment_id: s.id }
      S.scans.push(rec); audit(user.username, 'OFFLINE_SCAN_RECORDED', 'shipment', s.id, 'blocked on device', { client_event_id: e.client_event_id })
      results.push({ client_event_id: e.client_event_id, outcome: 'BLOCKED', scan: rec, note: 'Blocked on device — recorded, not loaded' }); continue
    }
    const d = await runGate(s, e.token, user.username, e.client_event_id, { commit: true }); d.offline = true
    const conflict = (e.local_decision === 'PROVISIONAL_ACCEPT') !== (d.decision === 'ACCEPTED')
    results.push({ client_event_id: e.client_event_id, outcome: conflict ? 'CONFLICT' : d.decision, scan: d, note: conflict ? (d.decision === 'BLOCKED' ? `Server blocked: ${d.reasons.map(x => x.code).join(', ')} — physically unload ${d.package_label}` : 'Server accepted a locally-blocked scan') : '' })
  }
  save(); return { results, chain: { ok: true, server_head: S.device_heads[dev]?.head ?? GENESIS } }
})

/* ── dispatcher ── */
let ready: Promise<void> | null = null
export async function mockFetch(method: string, path: string, headers: Record<string, string>, body: any): Promise<{ status: number; json: any }> {
  ready ??= init()
  await ready
  refreshIfStale()
  await new Promise(res => setTimeout(res, 90 + Math.random() * 140))
  const url = new URL(path, 'http://mock')
  const p = url.pathname.replace(/^\/api\/v1/, '')
  const auth = headers['Authorization']?.replace('Bearer mock.', '')
  const user = S.users.find(u => u.username === auth) ?? null
  for (const [m, re, h] of routes) {
    const mm = p.match(re)
    if (m === method && mm) {
      try { return { status: 200, json: await h({ params: mm.slice(1).map(decodeURIComponent), query: url.searchParams, body, user }) } }
      catch (e) {
        if (e instanceof HttpError) return { status: e.status, json: { error: { code: e.code, message: e.message, details: e.details } } }
        console.error(e); return { status: 500, json: { error: { code: 'MOCK_ERROR', message: String(e) } } }
      }
    }
  }
  return { status: 404, json: { error: { code: 'NO_ROUTE', message: `${method} ${p} not in mock` } } }
}
export const mockEvidence = (sha: string) => S?.evidence[sha]
