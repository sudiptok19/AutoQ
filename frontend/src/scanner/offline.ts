import type { GateCheck, Reason } from '../api/types'
import { verifyToken, type PublicKey, type TokenPayload } from '../lib/token'

export interface SnapPayload extends TokenPayload {
  typ: 'SNAP'; exp: number; shipment: string; order: string; expected_units: number
  allowed_packages: string[]; held: string[]; revoked_kids: string[]; keys: PublicKey[]
}
export interface StoredSnapshot { token: string; payload: SnapPayload; verified_at: string; shipment_code: string; demo_labels: { id: string; label: string; token: string }[] }
export type LocalDecision = 'PROVISIONAL_ACCEPT' | 'LOCAL_BLOCK' | 'ONLINE'
export type QueueStatus = 'PENDING' | 'ACCEPTED' | 'BLOCKED' | 'DUPLICATE' | 'CONFLICT'
export interface QueuedScan {
  client_event_id: string; seq: number; prev_hash: string; hash: string
  token: string; shipment_id: string; scanned_at: string
  local_decision: LocalDecision; local_reasons: Reason[]; local_checks: GateCheck[]
  package_id: string | null; package_label: string | null; units: number
  status: QueueStatus; server_reasons?: Reason[]; server_checks?: GateCheck[]; note?: string; synced_at?: string
  /** The signed snapshot this offline decision was made under. Sent per event: a batch can span shipments and refreshes.
   *  Under chain v2 it is part of the hash (explicit null for ONLINE). */
  snapshot_id?: string | null
  /** Device-chain version this entry was hashed under (absent = 1). */
  chain_version?: number
}

/** What the server said about one event on sync. A DUPLICATE carries the ORIGINAL outcome (e.g. after a lost response). */
export interface SyncResult { client_event_id: string; outcome: string; original_outcome?: string; note?: string; segregation_task_id?: string | null }

/** The snapshot to cite for an event: the one recorded at capture, else (events queued before this field existed)
 *  the stored snapshot for its shipment whose validity window contains the capture time. */
export function snapshotFor(ev: Pick<QueuedScan, 'snapshot_id' | 'shipment_id' | 'scanned_at' | 'local_decision' | 'chain_version'>,
                            stored: { payload: Pick<SnapPayload, 'id' | 'shipment' | 'iat' | 'exp'> }[]): string | undefined {
  if (ev.chain_version === 2) return ev.snapshot_id ?? undefined   // hashed: exactly what was captured, never back-filled
  if (ev.snapshot_id) return ev.snapshot_id
  if (ev.local_decision === 'ONLINE') return undefined
  const t = Date.parse(ev.scanned_at) / 1000
  return stored.find(s => s.payload.shipment === ev.shipment_id && s.payload.iat <= t && t < s.payload.exp)?.payload.id
}

/** Queue status after sync. A conflict — first time or replayed after a lost response — always wins, because it carries
 *  a physical instruction. An online scan otherwise keeps the decision it already received. */
export function reconciledStatus(ev: Pick<QueuedScan, 'local_decision' | 'status'>, r: SyncResult): QueueStatus {
  if (r.outcome === 'CONFLICT' || (r.outcome === 'DUPLICATE' && r.original_outcome === 'CONFLICT')) return 'CONFLICT'
  if (ev.local_decision === 'ONLINE' && ev.status !== 'PENDING') return ev.status
  if (r.outcome === 'DUPLICATE' && r.original_outcome && ['ACCEPTED', 'BLOCKED'].includes(r.original_outcome)) return r.original_outcome as QueueStatus
  return r.outcome as QueueStatus
}
export interface LocalResult { decision: LocalDecision; reasons: Reason[]; checks: GateCheck[]; package_id: string | null; package_label: string | null; units: number }

/**
 * On-device gate using only the signed snapshot. Its answer is PROVISIONAL: the snapshot can be stale
 * (new holds/recalls, other devices' scans), and seal state is never decided offline.
 */
/** Did the server already count this entry when it computed a snapshot's remaining capacity? Only if the server
 *  committed it before the snapshot was downloaded. Both times are this device's clock, so their ORDER is reliable:
 *  an online decision commits before its response (≈ scanned_at); a provisional one commits at sync (synced_at). */
export function reflectedIn(q: Pick<QueuedScan, 'local_decision' | 'scanned_at' | 'synced_at' | 'status'>, downloadedAtMs: number): boolean {
  const committed = q.local_decision === 'ONLINE' ? (q.status === 'ACCEPTED' ? Date.parse(q.scanned_at) : NaN) : q.synced_at ? Date.parse(q.synced_at) : NaN
  return Number.isFinite(committed) && committed < downloadedAtMs
}

export async function decideLocally(token: string, snap: SnapPayload, queue: QueuedScan[], nowSec: number, downloadedAtMs?: number): Promise<LocalResult> {
  const checks: GateCheck[] = [], reasons: Reason[] = []
  const fail = (gate: GateCheck['gate'], code: string, detail: string) => { checks.push({ gate, status: 'FAIL', detail }); reasons.push({ code, message: detail }) }
  const pass = (gate: GateCheck['gate'], detail: string) => checks.push({ gate, status: 'PASS', detail })

  const fresh = nowSec < snap.exp
  if (!fresh) reasons.push({ code: 'SNAPSHOT_STALE', message: `Offline snapshot expired ${Math.round((nowSec - snap.exp) / 60)} min ago — reconnect to refresh` })

  const keys = snap.keys.map(k => ({ ...k, status: snap.revoked_kids.includes(k.kid) ? 'REVOKED' : 'ACTIVE' }))
  const v = await verifyToken(token, keys)
  const p: any = v.payload
  if (!v.ok && (v.code === 'TOKEN_MALFORMED' || v.code === 'SIG_INVALID')) fail('SIGNATURE', v.code, v.code === 'SIG_INVALID' ? 'Ed25519 signature does not match payload' : 'Not an AQ1 token')
  else pass('SIGNATURE', 'Ed25519 · verified on device')
  if (!v.ok && (v.code === 'KEY_UNKNOWN' || v.code === 'KEY_REVOKED')) fail('KEY', v.code, `kid ${v.kid} not trusted in snapshot`)
  else if (v.ok) pass('KEY', `kid ${v.kid} (cached)`)

  const label = (p?.label as string) ?? null
  const units = Array.isArray(p?.items) ? p.items.length : 0
  if (v.ok) {
    if (p.typ !== 'PKG') fail('IDENTITY', 'TOKEN_MISMATCH', `Token type ${p.typ} — scan the package label`)
    else {
      pass('IDENTITY', `${label} · ${units} passports (signed)`)
      if (p.order !== snap.order) fail('ORDER', 'WRONG_ORDER', 'Package belongs to a different order')
      else if (!snap.allowed_packages.includes(p.id)) fail('ORDER', 'NOT_IN_SNAPSHOT', 'Not in this manifest snapshot (loaded elsewhere or created later)')
      else pass('ORDER', 'In manifest snapshot')
      if (snap.held.includes(p.id)) fail('QUARANTINE', 'HELD_AT_SNAPSHOT', 'Held (containment or recall) when the snapshot was issued')
      else checks.push({ gate: 'QUARANTINE', status: 'WARN', detail: 'Not held as of snapshot — newer holds unknown offline' })
      // Count what this device already loaded: provisional loads not yet refused, plus server-accepted loads.
      const mine = queue.filter(q => q.shipment_id === snap.shipment && q.package_id &&
        ((q.local_decision === 'PROVISIONAL_ACCEPT' && q.status !== 'BLOCKED' && q.status !== 'CONFLICT') || q.status === 'ACCEPTED'))
      if (mine.some(q => q.package_id === p.id)) fail('DUPLICATE', 'ALREADY_SCANNED_LOCALLY', 'Already loaded via this device (online or provisional)')
      else checks.push({ gate: 'DUPLICATE', status: 'WARN', detail: 'No local duplicate — other devices unknown offline' })
      checks.push({ gate: 'SEAL', status: 'WARN', detail: 'Seal state not decidable offline — server checks on sync' })
      // Capacity: the snapshot's expected_units is what was LEFT when it was issued, so loads the server had already
      // committed by then must not be counted again (a refreshed snapshot would otherwise double-count them).
      const counted = downloadedAtMs === undefined ? mine : mine.filter(q => !reflectedIn(q, downloadedAtMs))
      const after = counted.reduce((n, q) => n + q.units, 0) + units
      if (after > snap.expected_units) fail('QUANTITY', 'ORDER_QTY_EXCEEDED', `${after} > ${snap.expected_units} expected`)
      else pass('QUANTITY', `${after} / ${snap.expected_units} units (provisional)`)
    }
  }
  return { decision: reasons.length ? 'LOCAL_BLOCK' : 'PROVISIONAL_ACCEPT', reasons, checks, package_id: v.ok && p.typ === 'PKG' ? p.id : null, package_label: label, units }
}
