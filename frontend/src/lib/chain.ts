import { canonicalize } from './canonical'
import { sha256Hex } from './token'

export const GENESIS = '0'.repeat(64)
/** Chain versions this build can READ (verify). Writers are switched on separately, by the server (policy). */
export const SUPPORTED_CHAIN_VERSIONS = [1, 2] as const
export type ChainVersion = (typeof SUPPORTED_CHAIN_VERSIONS)[number]

export interface ChainCore {
  client_event_id: string; seq: number; token: string; shipment_id: string; scanned_at: string; local_decision: string
  /** Absent = 1 (the original six-field projection). */
  chain_version?: number
  /** v2 only, and hashed: the exact capture snapshot for offline decisions, explicit null for ONLINE. */
  snapshot_id?: string | null
}

export class ChainError extends Error {
  constructor(public code: 'SCHEMA_INVALID' | 'VERSION_UNSUPPORTED', message: string) { super(message) }
}

const EVENT_ID = /^[A-Za-z0-9:._-]{1,80}$/
const RESOURCE_ID = /^[A-Za-z0-9._-]{1,40}$/
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/

/** Mirrors backend passport_crypto._validate_chain_v2 (A4), checked against the shared interop vectors. */
function validateV2(prev: string, e: ChainCore) {
  const bad = (m: string) => { throw new ChainError('SCHEMA_INVALID', m) }
  if (prev !== prev.toLowerCase()) bad('Version 2 requires a lowercase previous scan hash')
  if (!Number.isSafeInteger(e.seq) || e.seq <= 0) bad('Version 2 requires a positive JS-safe scan sequence')
  if (!EVENT_ID.test(e.client_event_id)) bad('Invalid scan event ID')
  if (!RESOURCE_ID.test(e.shipment_id)) bad('Invalid scan shipment ID')
  if (!e.token || !e.scanned_at) bad('Scan token and capture time text must be nonempty')
  for (const s of [e.client_event_id, e.token, e.shipment_id, e.scanned_at, e.local_decision]) if (LONE_SURROGATE.test(s)) bad('Unpaired UTF-16 surrogate')
  if (!('snapshot_id' in e)) bad('Version 2 requires an explicit snapshot ID (null for online events)')
  if (e.local_decision === 'ONLINE') { if (e.snapshot_id !== null) bad('Online events require an explicit null snapshot ID') }
  else if (e.local_decision === 'PROVISIONAL_ACCEPT' || e.local_decision === 'LOCAL_BLOCK') {
    if (typeof e.snapshot_id !== 'string' || !RESOURCE_ID.test(e.snapshot_id)) bad('Offline events require the exact capture snapshot ID')
  } else bad('Invalid local scan decision')
}

/** Per-device hash chain over queued scan events. Detects edits, gaps and reordering after the fact;
 *  it does not authenticate the device or prove nothing was deleted before sync. */
export function chainHash(prev: string, e: ChainCore): string {
  const version = 'chain_version' in e && e.chain_version !== undefined ? e.chain_version : 1
  if (typeof version !== 'number' || !Number.isInteger(version)) throw new ChainError('SCHEMA_INVALID', 'Scan chain version must be an integer')
  if (!(SUPPORTED_CHAIN_VERSIONS as readonly number[]).includes(version)) throw new ChainError('VERSION_UNSUPPORTED', 'Scan chain version is unsupported')
  const base = { client_event_id: e.client_event_id, seq: e.seq, token: e.token, shipment_id: e.shipment_id, scanned_at: e.scanned_at, local_decision: e.local_decision }
  if (version === 1) return sha256Hex(prev + '|' + canonicalize(base))
  validateV2(prev, e)
  return sha256Hex(prev + '|' + canonicalize({ ...base, chain_version: 2, snapshot_id: e.snapshot_id ?? null }))
}

export function verifyChain(events: (ChainCore & { prev_hash: string; hash: string })[], start = GENESIS): { ok: true; head: string } | { ok: false; broken_at: number } {
  let prev = start, lastSeq = -Infinity
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    let h: string
    try { h = chainHash(prev, e) } catch { return { ok: false, broken_at: e.seq } }
    if (e.seq <= lastSeq || e.prev_hash !== prev || h !== e.hash) return { ok: false, broken_at: e.seq }
    prev = e.hash; lastSeq = e.seq
  }
  return { ok: true, head: prev }
}
