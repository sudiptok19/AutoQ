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
}
export interface LocalResult { decision: LocalDecision; reasons: Reason[]; checks: GateCheck[]; package_id: string | null; package_label: string | null; units: number }

/**
 * On-device gate using only the signed snapshot. Its answer is PROVISIONAL: the snapshot can be stale
 * (new holds/recalls, other devices' scans), and seal state is never decided offline.
 */
export async function decideLocally(token: string, snap: SnapPayload, queue: QueuedScan[], nowSec: number): Promise<LocalResult> {
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
      const after = mine.reduce((n, q) => n + q.units, 0) + units
      if (after > snap.expected_units) fail('QUANTITY', 'ORDER_QTY_EXCEEDED', `${after} > ${snap.expected_units} expected`)
      else pass('QUANTITY', `${after} / ${snap.expected_units} units (provisional)`)
    }
  }
  return { decision: reasons.length ? 'LOCAL_BLOCK' : 'PROVISIONAL_ACCEPT', reasons, checks, package_id: v.ok && p.typ === 'PKG' ? p.id : null, package_label: label, units }
}
