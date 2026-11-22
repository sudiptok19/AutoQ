import { canonicalize } from './canonical'
import { sha256Hex } from './token'

export const GENESIS = '0'.repeat(64)
export interface ChainCore { client_event_id: string; seq: number; token: string; shipment_id: string; scanned_at: string; local_decision: string }

/** Per-device hash chain over queued scan events. Detects edits, gaps and reordering after the fact;
 *  it does not authenticate the device or prove nothing was deleted before sync. */
export const chainHash = (prev: string, e: ChainCore) =>
  sha256Hex(prev + '|' + canonicalize({ client_event_id: e.client_event_id, seq: e.seq, token: e.token, shipment_id: e.shipment_id, scanned_at: e.scanned_at, local_decision: e.local_decision }))

export function verifyChain(events: (ChainCore & { prev_hash: string; hash: string })[], start = GENESIS): { ok: true; head: string } | { ok: false; broken_at: number } {
  let prev = start, lastSeq = -Infinity
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    if (e.seq <= lastSeq || e.prev_hash !== prev || chainHash(prev, e) !== e.hash) return { ok: false, broken_at: e.seq }
    prev = e.hash; lastSeq = e.seq
  }
  return { ok: true, head: prev }
}
