import { describe, expect, it } from 'vitest'
import 'fake-indexeddb/auto'
import { decideLocally, reconciledStatus, reflectedIn, snapshotFor, type QueuedScan, type SnapPayload } from './offline'
import { newKeyPair, signToken } from '../lib/token'
import { chainHash, GENESIS, verifyChain } from '../lib/chain'
import { appendScan, listQueue, updateScan } from './store'

async function fixture() {
  const k = await newKeyPair()
  const pkg = (id: string, order = 'ord_1', items = 8) => signToken({ v: 1, typ: 'PKG', kid: 'k1', id, iat: 1, label: id.toUpperCase(), order, items: Array.from({ length: items }, (_, i) => `pp_${id}_${i}`) }, k.priv)
  const snap: SnapPayload = { v: 1, typ: 'SNAP', kid: 'k1', id: 'snap_1', iat: 1000, exp: 2000, shipment: 'shp_1', order: 'ord_1', expected_units: 16, allowed_packages: ['pkg_a', 'pkg_b', 'pkg_c', 'pkg_h'], held: ['pkg_h'], revoked_kids: [], keys: [{ kid: 'k1', public_key: k.pub, status: 'ACTIVE', not_before: 0, not_after: 5000 }] }
  return { k, pkg, snap }
}
const q = (over: Partial<QueuedScan>): QueuedScan => ({ client_event_id: 'x', seq: 1, prev_hash: '', hash: '', token: '', shipment_id: 'shp_1', scanned_at: '', local_decision: 'PROVISIONAL_ACCEPT', local_reasons: [], local_checks: [], package_id: null, package_label: null, units: 8, status: 'PENDING', ...over })

describe('decideLocally', () => {
  it('provisionally accepts a valid, allowed, unheld package and never claims seal state', async () => {
    const { pkg, snap } = await fixture()
    const r = await decideLocally(await pkg('pkg_a'), snap, [], 1500)
    expect(r.decision).toBe('PROVISIONAL_ACCEPT')
    expect(r.checks.find(c => c.gate === 'SEAL')?.status).toBe('WARN')
  })
  it('blocks held, wrong-order, local duplicate, over-quantity, forged and stale', async () => {
    const { pkg, snap } = await fixture()
    const codes = async (t: string, queue: QueuedScan[] = [], now = 1500) => (await decideLocally(t, snap, queue, now)).reasons.map(r => r.code)
    expect(await codes(await pkg('pkg_h'))).toContain('HELD_AT_SNAPSHOT')
    expect(await codes(await pkg('pkg_a', 'ord_2'))).toContain('WRONG_ORDER')
    expect(await codes(await pkg('pkg_a'), [q({ package_id: 'pkg_a' })])).toContain('ALREADY_SCANNED_LOCALLY')
    expect(await codes(await pkg('pkg_c'), [q({ package_id: 'pkg_a' }), q({ package_id: 'pkg_b' })])).toContain('ORDER_QTY_EXCEEDED')
    const t = await pkg('pkg_a'); const [h, , s] = t.split('.')
    expect(await codes(`${h}.${btoa('{"iat":1,"id":"pkg_a","kid":"k1"}').replace(/=+$/, '')}.${s}`)).toContain('SIG_INVALID')
    expect(await codes(await pkg('pkg_a'), [], 2100)).toContain('SNAPSHOT_STALE')
  })
})

describe('label signing-key validity (offline)', () => {
  const label = (k: { priv: Uint8Array }, iat: number) => signToken({ v: 1, typ: 'PKG', kid: 'k1', id: 'pkg_a', iat, label: 'PKG_A', order: 'ord_1', items: ['pp_1'] }, k.priv)
  const codes = async (snap: SnapPayload, t: string, now = 1500) => (await decideLocally(t, snap, [], now)).reasons.map(r => r.code)

  it('blocks a label signed before the key became valid', async () => {
    const { k, snap } = await fixture()
    const s = { ...snap, keys: [{ ...snap.keys[0], not_before: 500 }] }
    expect(await codes(s, await label(k, 100))).toContain('KEY_EXPIRED')
    expect(await codes(s, await label(k, 600))).not.toContain('KEY_EXPIRED')
  })
  it('blocks a label signed after the key expired', async () => {
    const { k, snap } = await fixture()
    const s = { ...snap, keys: [{ ...snap.keys[0], status: 'RETIRED', not_after: 800 }] }
    expect(await codes(s, await label(k, 900))).toContain('KEY_EXPIRED')
  })
  it('still accepts a RETIRED key for labels it signed while valid, but not an expired ACTIVE key', async () => {
    const { k, snap } = await fixture()
    const retired = { ...snap, keys: [{ ...snap.keys[0], status: 'RETIRED', not_after: 1200 }] }
    expect((await decideLocally(await label(k, 1100), retired, [], 1500)).decision).toBe('PROVISIONAL_ACCEPT')
    const active = { ...snap, keys: [{ ...snap.keys[0], not_after: 1200 }] }
    expect(await codes(active, await label(k, 1100))).toContain('KEY_EXPIRED')
  })
  it('fails closed when the snapshot key has no validity window', async () => {
    const { k, snap } = await fixture()
    const s = { ...snap, keys: [{ kid: 'k1', public_key: snap.keys[0].public_key }] }
    expect(await codes(s, await label(k, 1100))).toContain('KEY_EXPIRED')
  })
  it('blocks a label dated after the device clock beyond the allowed skew', async () => {
    const { k, snap } = await fixture()
    expect(await codes(snap, await label(k, 1500 + 60))).not.toContain('ISSUED_IN_FUTURE')
    expect(await codes(snap, await label(k, 1500 + 300))).toContain('ISSUED_IN_FUTURE')
  })
  it('a revoked kid still wins over a valid window', async () => {
    const { k, snap } = await fixture()
    expect(await codes({ ...snap, revoked_kids: ['k1'] }, await label(k, 1100))).toContain('KEY_REVOKED')
  })
})

describe('device hash chain', () => {
  it('appends atomically and detects tampering', async () => {
    for (const id of ['a', 'b', 'c']) await appendScan({ token: `t_${id}`, shipment_id: 'shp_1', scanned_at: '2026-11-22T10:00:00Z', local_decision: 'PROVISIONAL_ACCEPT', local_reasons: [], local_checks: [], package_id: id, package_label: id, units: 8, status: 'PENDING' })
    const all = await listQueue()
    expect(all.map(e => e.seq)).toEqual([3, 2, 1])
    expect(verifyChain(all).ok).toBe(true)
    expect(all.at(-1)!.prev_hash).toBe(GENESIS)
    await updateScan({ ...all[1], token: 't_evil' })
    expect(verifyChain(await listQueue())).toEqual({ ok: false, broken_at: 2 })
    expect(chainHash(GENESIS, all[2])).toBe(all[2].hash)
  })
})

describe('sync provenance and reconciliation (A7 handoff)', () => {
  const snaps = [
    { payload: { id: 'snap_1', shipment: 'shp_1', iat: 1000, exp: 2000 } },
    { payload: { id: 'snap_2', shipment: 'shp_2', iat: 1000, exp: 2000 } },
  ]
  const at = (sec: number) => new Date(sec * 1000).toISOString()

  it('cites the snapshot recorded at capture, never the one currently on screen', () => {
    expect(snapshotFor({ snapshot_id: 'snap_old', shipment_id: 'shp_1', scanned_at: at(1500), local_decision: 'PROVISIONAL_ACCEPT' }, snaps)).toBe('snap_old')
  })
  it('for older queue entries, picks the stored snapshot of the SAME shipment whose window covers the capture', () => {
    expect(snapshotFor({ shipment_id: 'shp_2', scanned_at: at(1500), local_decision: 'PROVISIONAL_ACCEPT' }, snaps)).toBe('snap_2')
    expect(snapshotFor({ shipment_id: 'shp_2', scanned_at: at(2500), local_decision: 'PROVISIONAL_ACCEPT' }, snaps)).toBeUndefined()
    expect(snapshotFor({ shipment_id: 'shp_1', scanned_at: at(1500), local_decision: 'ONLINE' }, snaps)).toBeUndefined()
  })
  it('keeps a conflict visible even when it comes back as a replay after a lost response', () => {
    expect(reconciledStatus({ local_decision: 'PROVISIONAL_ACCEPT', status: 'PENDING' }, { client_event_id: 'e', outcome: 'DUPLICATE', original_outcome: 'CONFLICT' })).toBe('CONFLICT')
    expect(reconciledStatus({ local_decision: 'ONLINE', status: 'ACCEPTED' }, { client_event_id: 'e', outcome: 'CONFLICT' })).toBe('CONFLICT')
  })
  it('otherwise keeps an online decision and maps replays to their original outcome', () => {
    expect(reconciledStatus({ local_decision: 'ONLINE', status: 'ACCEPTED' }, { client_event_id: 'e', outcome: 'DUPLICATE' })).toBe('ACCEPTED')
    expect(reconciledStatus({ local_decision: 'PROVISIONAL_ACCEPT', status: 'PENDING' }, { client_event_id: 'e', outcome: 'DUPLICATE', original_outcome: 'ACCEPTED' })).toBe('ACCEPTED')
    expect(reconciledStatus({ local_decision: 'LOCAL_BLOCK', status: 'PENDING' }, { client_event_id: 'e', outcome: 'BLOCKED' })).toBe('BLOCKED')
  })
})

describe('capacity after a refreshed snapshot (P1)', () => {
  const T = (s: string) => Date.parse(s)
  it('does not count loads the server had already committed when the snapshot was downloaded', async () => {
    const { pkg, snap } = await fixture()
    // Snapshot downloaded at 10:05 says 8 units remain of 16. pkg_a was synced at 10:01, so it is already in that 8.
    const synced = q({ package_id: 'pkg_a', status: 'ACCEPTED', scanned_at: '2026-11-26T10:00:00Z', synced_at: '2026-11-26T10:01:00Z' })
    const refreshed = { ...snap, expected_units: 8 }
    const before = await decideLocally(await pkg('pkg_b'), refreshed, [synced], 1500)                                   // old behaviour
    const after = await decideLocally(await pkg('pkg_b'), refreshed, [synced], 1500, T('2026-11-26T10:05:00Z'))
    expect(before.reasons.map(r => r.code)).toContain('ORDER_QTY_EXCEEDED')
    expect(after.decision).toBe('PROVISIONAL_ACCEPT')
  })
  it('still counts what the snapshot cannot know about: unsynced, or synced after it was downloaded', () => {
    const dl = T('2026-11-26T10:05:00Z')
    expect(reflectedIn(q({ status: 'PENDING', scanned_at: '2026-11-26T10:00:00Z' }), dl)).toBe(false)
    expect(reflectedIn(q({ status: 'ACCEPTED', scanned_at: '2026-11-26T10:00:00Z', synced_at: '2026-11-26T10:06:00Z' }), dl)).toBe(false)
    expect(reflectedIn(q({ local_decision: 'ONLINE', status: 'ACCEPTED', scanned_at: '2026-11-26T10:04:00Z' }), dl)).toBe(true)
    expect(reflectedIn(q({ local_decision: 'ONLINE', status: 'PENDING', scanned_at: '2026-11-26T10:04:00Z' }), dl)).toBe(false)   // lost response: unknown
  })
})

describe('chain writer rollout (A4)', () => {
  it('moves the writer only upward, only to supported versions, and flags a build below the floor', async () => {
    const { applyChainPolicy, chainWriter } = await import('./store')
    expect(await applyChainPolicy({ writer_version: 1, min_version: 1 })).toEqual({ writer: 1, outdated: false })
    expect(await applyChainPolicy({ writer_version: 2, min_version: 1 })).toEqual({ writer: 2, outdated: false })
    expect(await applyChainPolicy({ writer_version: 1, min_version: 1 })).toEqual({ writer: 2, outdated: false })   // never back down
    expect(await applyChainPolicy({ writer_version: 3, min_version: 3 })).toEqual({ writer: 2, outdated: true })    // a future floor
    expect(await chainWriter()).toBe(2)
  })
  it('appends v2 entries that hash the capture snapshot, null for online scans', async () => {
    const online = await appendScan({ token: 't_on', shipment_id: 'shp_1', scanned_at: '2026-11-27T10:00:00Z', local_decision: 'ONLINE', local_reasons: [], local_checks: [], package_id: null, package_label: null, units: 0, status: 'PENDING' })
    const off = await appendScan({ token: 't_off', shipment_id: 'shp_1', scanned_at: '2026-11-27T10:01:00Z', local_decision: 'PROVISIONAL_ACCEPT', local_reasons: [], local_checks: [], package_id: 'p', package_label: 'P', units: 1, status: 'PENDING', snapshot_id: 'snap_1' })
    expect([online.chain_version, online.snapshot_id, off.chain_version, off.snapshot_id]).toEqual([2, null, 2, 'snap_1'])
    expect(chainHash(online.prev_hash, online)).toBe(online.hash)
    // (The shared fake IndexedDB still holds the entry an earlier test tampered with, so verify just these two.)
    expect(verifyChain([online, off], online.prev_hash)).toEqual({ ok: true, head: off.hash })
  })
})
