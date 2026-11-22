import { describe, expect, it } from 'vitest'
import 'fake-indexeddb/auto'
import { decideLocally, type QueuedScan, type SnapPayload } from './offline'
import { newKeyPair, signToken } from '../lib/token'
import { chainHash, GENESIS, verifyChain } from '../lib/chain'
import { appendScan, listQueue, updateScan } from './store'

async function fixture() {
  const k = await newKeyPair()
  const pkg = (id: string, order = 'ord_1', items = 8) => signToken({ v: 1, typ: 'PKG', kid: 'k1', id, iat: 1, label: id.toUpperCase(), order, items: Array.from({ length: items }, (_, i) => `pp_${id}_${i}`) }, k.priv)
  const snap: SnapPayload = { v: 1, typ: 'SNAP', kid: 'k1', id: 'snap_1', iat: 1000, exp: 2000, shipment: 'shp_1', order: 'ord_1', expected_units: 16, allowed_packages: ['pkg_a', 'pkg_b', 'pkg_c', 'pkg_h'], held: ['pkg_h'], revoked_kids: [], keys: [{ kid: 'k1', public_key: k.pub }] }
  return { pkg, snap }
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
    expect(await codes(`${h}.${btoa('{"id":"pkg_a","kid":"k1"}').replace(/=+$/, '')}.${s}`)).toContain('SIG_INVALID')
    expect(await codes(await pkg('pkg_a'), [], 2100)).toContain('SNAPSHOT_STALE')
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
