/**
 * In-browser mock backend state. Implements CONTRACT.md semantics so the UI is fully usable
 * before the real backend exists. It is NOT the authority — the FastAPI backend is.
 * Seed follows CONTRACT §9 (see docs/CONTRACT.md).
 */
import type {
  AuditEvent, Containment, Customer, FourMEvent, Heat, Inspection, Line, Lot, Order, Package, Part, Passport,
  QualityStatus, Recall, ScanDecision, SealCheck, Shipment, Supplier, User,
} from '../api/types'
import { b64url, canonicalize, fromB64url } from '../lib/canonical'
import { newKeyPair, sha256Hex, signToken } from '../lib/token'

export interface UnitRow { id: string; serial: string; lot_id: string; part_id: string; quality_status: QualityStatus; passport_id: string | null; package_id: string | null; created_at: string; held: boolean }
export type LotRow = Omit<Lot, 'counts' | 'containment_ids' | 'held'>
export interface KeyRow { kid: string; public_key: string; status: 'ACTIVE' | 'RETIRED' | 'REVOKED'; priv: string; not_before: string; not_after: string }
export interface SpcRow { inspection_id: string; t: string; score: number; synthetic: boolean; line_id: string; method_version: string }

export interface Policies {
  containment: { first_n: number; lookback_minutes: number; threshold_tightening: number; recheck_existing_unplanned: boolean; recheck_existing_planned: boolean }
  inspection: { hold_threshold: number; reject_threshold: number; blur_min: number; glare_max: number; min_w: number; min_h: number }
  seal_recheck_max_age_minutes: number; snapshot_ttl_minutes: number; allow_partial_close: boolean
  retention_years_provisional: number
}

export interface MockState {
  version: number; counter: number; seeded_at: string
  keys: KeyRow[]; users: User[]; suppliers: Supplier[]; parts: Part[]; lines: Line[]; heats: Heat[]; lots: LotRow[]
  lotHeld: Record<string, boolean>
  units: UnitRow[]; inspections: Inspection[]; passports: Passport[]; events: FourMEvent[]; containments: Containment[]
  customers: Customer[]; orders: Order[]; packages: Package[]; seal_checks: SealCheck[]; shipments: Shipment[]; scans: ScanDecision[]
  recalls: Recall[]; audit: AuditEvent[]; evidence: Record<string, string>; spc: SpcRow[]; policies: Policies; alerts_acked: string[]
  reports: any[]
  device_heads?: Record<string, { head: string; seq: number }>
}

const KEY = 'autoq-mock-v1'
export let S: MockState

export const now = () => new Date().toISOString()
const minsAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

export function nextId(prefix: string) { S.counter++; return `${prefix}_${S.counter.toString(36).padStart(6, '0')}` }

/* Cross-tab: another tab (e.g. the phone scanner view) wrote newer state → reload before the next request. */
let dirty = false
if (typeof window !== 'undefined') window.addEventListener('storage', e => { if (e.key === KEY) dirty = true })
export function refreshIfStale() { if (dirty) { dirty = false; load() } }

export function save() { try { localStorage.setItem(KEY, JSON.stringify(S)) } catch { /* quota or private mode: state stays in memory */ } }
export function load(): boolean {
  try { const raw = localStorage.getItem(KEY); if (raw) { const s = JSON.parse(raw); if (s.version === 1) { S = s; return true } } } catch { /* ignore */ }
  return false
}

/** Make audit/canonical data safe: canonical JSON forbids floats. */
function intSafe(v: any): any {
  if (typeof v === 'number') return Number.isInteger(v) ? v : String(v)
  if (Array.isArray(v)) return v.map(intSafe)
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).filter(([, x]) => x !== undefined).map(([k, x]) => [k, intSafe(x)]))
  return v
}

export function audit(actor: string, action: string, entity_type: string, entity_id: string, reason?: string, data?: Record<string, any>) {
  const prev = S.audit[S.audit.length - 1]
  const body = intSafe({ seq: (prev?.seq ?? 0) + 1, at: now(), actor, action, entity_type, entity_id, reason: reason ?? null, data: data ?? {} })
  const prev_hash = prev?.hash ?? '0'.repeat(64)
  const ev: AuditEvent = { id: nextId('aud'), ...body, prev_hash, hash: sha256Hex(prev_hash + canonicalize(body)) }
  S.audit.push(ev)
  return ev
}

export function verifyAudit() {
  let prev = '0'.repeat(64)
  for (const e of S.audit) {
    const { id: _i, prev_hash, hash, ...body } = e
    if (prev_hash !== prev || sha256Hex(prev + canonicalize(intSafe(body))) !== hash) return { ok: false, broken_at: e.seq, length: S.audit.length }
    prev = hash
  }
  return { ok: true, length: S.audit.length, head: prev }
}

export const activeKey = () => S.keys.find(k => k.status === 'ACTIVE')!
export async function sign(payload: Record<string, any>) {
  const k = activeKey()
  return signToken({ v: 1, kid: k.kid, iat: Math.floor(Date.now() / 1000), ...payload } as any, fromB64url(k.priv))
}

export async function issuePassport(unit: UnitRow, insp: Inspection, held: boolean): Promise<Passport> {
  const lot = S.lots.find(l => l.id === unit.lot_id)!
  const part = S.parts.find(p => p.id === unit.part_id)!
  const heats = lot.heat_ids.map(h => S.heats.find(x => x.id === h)!.heat_code)
  const id = nextId('pp')
  const digest = 'sha256:' + sha256Hex(canonicalize({ id: insp.id, verdict: insp.verdict, image: insp.evidence.image_sha256, method: insp.analysis.method_version }))
  const token = await sign({ typ: 'PP', id, unit: unit.id, serial: unit.serial, lot: lot.lot_code, part: part.part_number, heats, insp: digest })
  const pp: Passport = { id, unit_id: unit.id, status: held ? 'HELD' : 'ACTIVE', token, kid: activeKey().kid, payload_version: 1, issued_at: now(), inspection_id: insp.id, inspection_digest: digest }
  S.passports.push(pp); unit.passport_id = id
  return pp
}

export async function signPackage(pkg: Package) {
  const items = pkg.unit_ids.map(u => S.units.find(x => x.id === u)!.passport_id!).sort()
  pkg.token = await sign({ typ: 'PKG', id: pkg.id, label: pkg.label, order: pkg.order_id, items, seal: pkg.seal_ref_sha256 })
}

function rnd(seed: number) { return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296) }
function gauss(r: () => number) { return Math.sqrt(-2 * Math.log(r() + 1e-9)) * Math.cos(2 * Math.PI * r()) }

export async function seed() {
  const kp = await newKeyPair()
  S = {
    version: 1, counter: 0, seeded_at: now(),
    keys: [{ kid: 'aq-2026-11-a', public_key: kp.pub, status: 'ACTIVE', priv: b64url(kp.priv), not_before: minsAgo(60 * 24 * 7), not_after: new Date(Date.now() + 864e5 * 180).toISOString() }],
    users: [
      { id: 'usr_admin', username: 'admin', display_name: 'Admin (dev)', roles: ['admin'] },
      { id: 'usr_ravi', username: 'op.ravi', display_name: 'Ravi Kumar', roles: ['operator'] },
      { id: 'usr_ananya', username: 'qe.ananya', display_name: 'Ananya Sen', roles: ['quality_engineer'] },
      { id: 'usr_farhan', username: 'qm.farhan', display_name: 'Farhan Ali', roles: ['quality_manager', 'quality_engineer'] },
      { id: 'usr_kiran', username: 'disp.kiran', display_name: 'Kiran Das', roles: ['dispatcher'] },
    ],
    suppliers: [{ id: 'sup_shree', name: 'Shree Forgings Pvt Ltd (demo)', code: 'SUP-014', synthetic: true }],
    parts: [{ id: 'part_44120', part_number: 'PN-44120', name: 'Brake caliper bracket', drawing_rev: 'C', inspection_profile_id: 'prof_bracket' }],
    lines: [{ id: 'line_1', name: 'Line 1', station_id: 'st_l1_vis' }, { id: 'line_2', name: 'Line 2', station_id: 'st_l2_vis' }],
    heats: [
      { id: 'heat_7781', heat_code: 'H-7781', supplier_id: 'sup_shree', material_grade: 'SG 500/7', certificate_name: 'MTC_H-7781.pdf', certificate_sha256: sha256Hex('demo mill certificate H-7781'), received_at: minsAgo(60 * 24 * 6) },
      { id: 'heat_7790', heat_code: 'H-7790', supplier_id: 'sup_shree', material_grade: 'SG 500/7', certificate_name: 'MTC_H-7790.pdf', certificate_sha256: sha256Hex('demo mill certificate H-7790'), received_at: minsAgo(60 * 24 * 3) },
    ],
    lots: [
      { id: 'lot_2611a', lot_code: 'L-2611-A', part_id: 'part_44120', line_id: 'line_2', heat_ids: ['heat_7781'], planned_qty: 48, created_at: minsAgo(60 * 30) },
      { id: 'lot_2611b', lot_code: 'L-2611-B', part_id: 'part_44120', line_id: 'line_1', heat_ids: ['heat_7790'], planned_qty: 20, created_at: minsAgo(60 * 5) },
    ],
    lotHeld: {},
    units: [], inspections: [], passports: [], events: [], containments: [],
    customers: [
      { id: 'cus_ggn', name: 'Demo Auto Assembly Plant', city: 'Gurugram' },
      { id: 'cus_kol', name: 'Demo Service Depot', city: 'Kolkata' },
    ],
    orders: [
      { id: 'ord_1001', order_no: 'SO-1001', customer_id: 'cus_ggn', part_id: 'part_44120', qty_units: 40, destination: 'Gurugram · Gate 4', created_at: minsAgo(60 * 48) },
      { id: 'ord_1002', order_no: 'SO-1002', customer_id: 'cus_kol', part_id: 'part_44120', qty_units: 16, destination: 'Kolkata · Bay 2', created_at: minsAgo(60 * 20) },
    ],
    packages: [], seal_checks: [], shipments: [], scans: [], recalls: [], audit: [], evidence: {}, spc: [], alerts_acked: [], reports: [],
    policies: {
      containment: { first_n: 5, lookback_minutes: 30, threshold_tightening: 0.15, recheck_existing_unplanned: true, recheck_existing_planned: false },
      inspection: { hold_threshold: 0.45, reject_threshold: 0.7, blur_min: 80, glare_max: 0.12, min_w: 320, min_h: 240 },
      seal_recheck_max_age_minutes: 60, snapshot_ttl_minutes: 240, allow_partial_close: false, retention_years_provisional: 11,
    },
  }
  audit('system', 'DEMO_SEED', 'system', 'seed', 'Demo seed per CONTRACT §9')

  const r = rnd(42)
  const mkUnit = (lot: string, code: string, i: number, created: string): UnitRow => ({
    id: nextId('unit'), serial: `BRK-${code}-${String(i).padStart(4, '0')}`, lot_id: lot, part_id: 'part_44120',
    quality_status: 'UNINSPECTED', passport_id: null, package_id: null, created_at: created, held: false,
  })
  const seedInspection = async (u: UnitRow, verdict: 'PASS' | 'HOLD' | 'REJECT', score: number, at: string, line: string, by = 'op.ravi') => {
    const fixture = verdict === 'REJECT' ? 'defect' : 'good'
    const insp: Inspection = {
      id: nextId('insp'), unit_id: u.id, station_id: line === 'line_2' ? 'st_l2_vis' : 'st_l1_vis', created_by: by, created_at: at,
      evidence: { image_sha256: sha256Hex(`fixture:${fixture}:${S.counter}`), image_url: `fixture:${fixture}:${S.counter}`, heatmap_url: null },
      quality_gate: { passed: true, blur_laplacian_var: Math.round(180 + r() * 80), glare_ratio: 0, width: 640, height: 480, thresholds: { blur_min: 80, glare_max: 0.12, min_w: 320, min_h: 240 }, failures: [] },
      analysis: { method: 'golden_diff', method_version: 'gd-0.3', calibrated: true, score, hold_threshold: 0.45, reject_threshold: 0.7, threshold_tightened: false, regions: [] },
      verdict, reasons: verdict === 'PASS' ? [] : [{ code: verdict === 'HOLD' ? 'SCORE_ABOVE_HOLD' : 'SCORE_ABOVE_REJECT', message: `Anomaly score ${score} ≥ ${verdict === 'HOLD' ? 0.45 : 0.7}` }],
      passport: null, synthetic: true,
    }
    S.inspections.push(insp); u.quality_status = verdict
    if (verdict === 'PASS') insp.passport = await issuePassport(u, insp, false)
    return insp
  }

  // SPC baseline: 60 historical synthetic scores on Line 2 — stable for 40, then a slow upward drift
  for (let i = 0; i < 60; i++) {
    const drift = i >= 40 ? (i - 40) * 0.0065 : 0
    const s = Math.max(0.02, Math.min(0.6, 0.17 + gauss(r) * 0.035 + drift))
    S.spc.push({ inspection_id: `hist_${i}`, t: minsAgo(60 * 26 - i * 25), score: Math.round(s * 1000) / 1000, synthetic: true, line_id: 'line_2', method_version: 'gd-0.3' })
  }

  // Lot L-2611-A (Line 2, H-7781): 40 units inspected+packed, 8 freshly produced & uninspected.
  const lotA: UnitRow[] = []
  for (let i = 1; i <= 48; i++) {
    const created = i <= 16 ? minsAgo(60 * 28 - i) : i <= 32 ? minsAgo(60 * 3 - i) : i <= 40 ? minsAgo(22 - (i - 32)) : minsAgo(12 - (i - 40))
    const u = mkUnit('lot_2611a', '2611A', i, created); S.units.push(u); lotA.push(u)
    if (i <= 40) await seedInspection(u, 'PASS', Math.round((0.12 + r() * 0.12) * 1000) / 1000, new Date(new Date(created).getTime() + 90_000).toISOString(), 'line_2')
  }
  // Lot L-2611-B (Line 1, H-7790): mixed results
  const lotB: UnitRow[] = []
  for (let i = 1; i <= 20; i++) {
    const u = mkUnit('lot_2611b', '2611B', i, minsAgo(60 * 4 - i * 5)); S.units.push(u); lotB.push(u)
    const at = minsAgo(60 * 4 - i * 5 - 2)
    if (i <= 12) await seedInspection(u, 'PASS', Math.round((0.1 + r() * 0.15) * 1000) / 1000, at, 'line_1', 'op.ravi')
    else if (i <= 14) await seedInspection(u, 'HOLD', Math.round((0.5 + r() * 0.1) * 1000) / 1000, at, 'line_1')
    else if (i === 15) await seedInspection(u, 'REJECT', 0.86, at, 'line_1')
  }

  // Packages for SO-1001 (8 units each)
  const mkPkg = async (label: string, order: string, units: UnitRow[], created: string, state: Package['state'], sealCheckMins: number | null) => {
    const pkg: Package = { id: nextId('pkg'), label, order_id: order, state: 'OPEN', unit_ids: units.map(u => u.id), held: false, hold_reasons: [], token: null, seal_ref_sha256: null, last_seal_check: null, created_at: created }
    units.forEach(u => (u.package_id = pkg.id))
    if (state !== 'OPEN') {
      pkg.seal_ref_sha256 = sha256Hex(`seal:${label}`)
      const enroll: SealCheck = { id: nextId('seal'), package_id: pkg.id, kind: 'ENROLL', result: null, method_version: 'seal-orb-ssim-0.2', metrics: { keypoints_ref: 790 + Math.round(r() * 60) }, explanation: 'Reference enrolled.', created_by: 'op.ravi', created_at: created, synthetic: true }
      S.seal_checks.push(enroll)
      pkg.last_seal_check = enroll
      if (sealCheckMins !== null) {
        const re: SealCheck = { id: nextId('seal'), package_id: pkg.id, kind: 'RECHECK', result: 'INTACT', method_version: 'seal-orb-ssim-0.2', metrics: { inliers: 180, inlier_ratio: 0.71, ssim: 0.86 }, explanation: 'Pattern registered with 180 inliers; SSIM 0.86 ≥ 0.72.', created_by: 'disp.kiran', created_at: minsAgo(sealCheckMins), synthetic: true }
        S.seal_checks.push(re); pkg.last_seal_check = re
      }
      await signPackage(pkg)
      pkg.state = state
    }
    S.packages.push(pkg)
    return pkg
  }
  const p1 = await mkPkg('PKG-0001', 'ord_1001', lotA.slice(0, 8), minsAgo(60 * 27), 'DELIVERED', null)
  const p2 = await mkPkg('PKG-0002', 'ord_1001', lotA.slice(8, 16), minsAgo(60 * 26), 'IN_TRANSIT', null)
  await mkPkg('PKG-0003', 'ord_1001', lotA.slice(16, 24), minsAgo(60 * 2.5), 'SEALED', 8)
  await mkPkg('PKG-0004', 'ord_1001', lotA.slice(24, 32), minsAgo(60 * 2), 'SEALED', 6)
  await mkPkg('PKG-0005', 'ord_1001', lotA.slice(32, 40), minsAgo(14), 'SEALED', 4)
  await mkPkg('PKG-0006', 'ord_1002', lotB.slice(0, 6), minsAgo(40), 'OPEN', null)

  S.shipments.push(
    { id: 'shp_0001', code: 'SHP-0001', order_id: 'ord_1001', vehicle_no: 'HR-55-AK-2093', dock: 'Dock 1', status: 'DELIVERED', loaded_package_ids: [p1.id], created_at: minsAgo(60 * 26.5),
      movements: [{ type: 'DEPARTED', location: 'Plant gate', occurred_at: minsAgo(60 * 26) }, { type: 'DELIVERED', location: 'Gurugram · Gate 4', occurred_at: minsAgo(60 * 4) }] },
    { id: 'shp_0002', code: 'SHP-0002', order_id: 'ord_1001', vehicle_no: 'WB-02-AK-4471', dock: 'Dock 2', status: 'IN_TRANSIT', loaded_package_ids: [p2.id], created_at: minsAgo(60 * 25.5),
      movements: [{ type: 'DEPARTED', location: 'Plant gate', occurred_at: minsAgo(60 * 25) }, { type: 'CHECKPOINT', location: 'NH-19 · Dhanbad toll', occurred_at: minsAgo(60 * 9) }] },
    { id: 'shp_0003', code: 'SHP-0003', order_id: 'ord_1001', vehicle_no: 'WB-04-BC-1186', dock: 'Dock 3', status: 'OPEN', loaded_package_ids: [], created_at: minsAgo(30), movements: [] },
  )
  save()
}

export async function init() { if (!load()) await seed() }
