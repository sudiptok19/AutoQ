import { describe, expect, it } from 'vitest'
import { canonicalize } from './canonical'
import { newKeyPair, signToken, verifyToken } from './token'

describe('canonical JSON', () => {
  it('sorts keys and strips whitespace', () => {
    expect(canonicalize({ b: 1, a: [true, null, 'x'], c: { z: 1, y: 2 } })).toBe('{"a":[true,null,"x"],"b":1,"c":{"y":2,"z":1}}')
  })
  it('rejects floats', () => { expect(() => canonicalize({ a: 0.5 })).toThrow() })
})

describe('AQ1 tokens', () => {
  it('round-trips and detects tampering, unknown and revoked keys', async () => {
    const k = await newKeyPair()
    const keys = [{ kid: 'k1', public_key: k.pub, status: 'ACTIVE' }]
    const t = await signToken({ v: 1, typ: 'PP', kid: 'k1', id: 'pp_1', iat: 1 }, k.priv)
    expect((await verifyToken(t, keys)).ok).toBe(true)

    const [h, p, s] = t.split('.')
    const forgedPayload = btoa(atob(p.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((p.length + 3) % 4)).replace('pp_1', 'pp_2')).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    expect(await verifyToken(`${h}.${forgedPayload}.${s}`, keys)).toMatchObject({ ok: false, code: 'SIG_INVALID' })
    expect(await verifyToken('garbage', keys)).toMatchObject({ ok: false, code: 'TOKEN_MALFORMED' })
    expect(await verifyToken(t, [])).toMatchObject({ ok: false, code: 'KEY_UNKNOWN' })
    expect(await verifyToken(t, [{ ...keys[0], status: 'REVOKED' }])).toMatchObject({ ok: false, code: 'KEY_REVOKED' })
  })
  it('with asOf, enforces the key validity window at signing and now; duplicate kids are untrusted', async () => {
    const k = await newKeyPair()
    const key = { kid: 'k1', public_key: k.pub, status: 'ACTIVE', not_before: 100, not_after: 200 }
    const at = (iat: number) => signToken({ v: 1, typ: 'PP', kid: 'k1', id: 'pp_1', iat }, k.priv)
    expect((await verifyToken(await at(150), [key], { asOf: 160 })).ok).toBe(true)
    expect(await verifyToken(await at(50), [key], { asOf: 160 })).toMatchObject({ ok: false, code: 'KEY_EXPIRED' })
    expect(await verifyToken(await at(150), [key], { asOf: 250 })).toMatchObject({ ok: false, code: 'KEY_EXPIRED' })
    expect((await verifyToken(await at(150), [{ ...key, status: 'RETIRED' }], { asOf: 250 })).ok).toBe(true)
    expect(await verifyToken(await at(150), [key, key], { asOf: 160 })).toMatchObject({ ok: false, code: 'KEY_UNKNOWN' })
    expect(await verifyToken(await at(150), [{ ...key, status: 'WEIRD' }])).toMatchObject({ ok: false, code: 'KEY_UNKNOWN' })
    expect(await verifyToken(await at(190), [key], { asOf: 160 })).toMatchObject({ ok: false, code: 'ISSUED_IN_FUTURE' })
    expect((await verifyToken(await at(190), [key], { asOf: 160, skewSec: 30 })).ok).toBe(true)
  })
})

describe('forgeToken', () => {
  it('produces a well-formed token whose signature fails', async () => {
    const { forgeToken } = await import('./token')
    const k = await newKeyPair()
    const keys = [{ kid: 'k1', public_key: k.pub }]
    const t = await signToken({ v: 1, typ: 'PKG', kid: 'k1', id: 'pkg_1', iat: 1, order: 'ord_1' }, k.priv)
    const f = forgeToken(t, p => { p.order = 'ord_2' })
    expect(await verifyToken(f, keys)).toMatchObject({ ok: false, code: 'SIG_INVALID' })
  })
})
