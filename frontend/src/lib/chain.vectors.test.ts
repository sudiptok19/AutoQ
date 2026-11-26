import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { ChainError, chainHash, verifyChain } from './chain'

// Astra's cross-language vectors (A4): the same file the Python suite checks.
const V = JSON.parse(readFileSync(fileURLToPath(new URL('../../../backend/tests/vectors/aq1_interop.json', import.meta.url)), 'utf8'))

describe('device-chain interop vectors', () => {
  it('reproduces the legacy v1 hash', async () => {
    expect(chainHash(V.chain.prev_hash, V.chain.event)).toBe(V.chain.hash)
  })
  const byName: Record<string, any> = Object.fromEntries(V.chain_v2.valid.map((c: any) => [c.name, c]))
  it.each(V.chain_v2.valid.map((c: any) => [c.name, c]) as [string, any][])('reproduces v2 %s', (_n: string, c: any) => {
    expect(chainHash(c.prev_hash, c.event)).toBe(c.hash)
  })
  // Same construction as backend test_chain_v2_rejects_invalid_interoperability_vectors: base event + changes − omit.
  it.each(V.chain_v2.invalid.map((c: any) => [c.name, c]) as [string, any][])('rejects v2 %s exactly as the server does', (_n: string, c: any) => {
    const base = byName[c.base]
    const event: any = { ...base.event, ...(c.changes ?? {}) }
    for (const f of c.omit ?? []) delete event[f]
    try { chainHash(c.prev_hash ?? base.prev_hash, event); expect.unreachable(`${c.name} hashed`) }
    catch (e) { expect(e).toBeInstanceOf(ChainError); expect((e as ChainError).code).toBe(c.code) }
  })
  it('verifies one chain that moves from v1 history to v2 events', () => {
    const v1 = { ...V.chain.event, prev_hash: V.chain.prev_hash, hash: V.chain.hash }
    const v2 = { ...V.chain_v2.valid[0].event, prev_hash: V.chain_v2.valid[0].prev_hash, hash: V.chain_v2.valid[0].hash }
    expect(verifyChain([v2, v1])).toEqual({ ok: true, head: V.chain_v2.valid[0].hash })
    expect(verifyChain([v1, { ...v2, snapshot_id: 'snap-other' }]).ok).toBe(false)     // the snapshot is inside the v2 hash
  })
})
