import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { canonicalize } from './canonical'
import { verifyToken } from './token'

// Shared AQ1 vectors authored by Astra (backend/tests/vectors/aq1_interop.json).
const V = JSON.parse(readFileSync(resolve(__dirname, '../../../backend/tests/vectors/aq1_interop.json'), 'utf8'))
const keyFor = (status = V.key.status) => [{ kid: V.key.kid, public_key: V.key.public_key, status }]
// The browser verifier checks bytes, canonical form, key presence/revocation and signature; schema,
// version, validity window and type policy are enforced server-side (passport_crypto).
const BROWSER_CODES = new Set(['TOKEN_MALFORMED', 'SIG_INVALID', 'KEY_UNKNOWN', 'KEY_REVOKED'])

describe('AQ1 interop vectors', () => {
  for (const v of V.valid) {
    it(`accepts ${v.name}`, async () => {
      expect(canonicalize(v.payload)).toBe(v.canonical)
      expect((await verifyToken(v.token, keyFor())).ok).toBe(true)
    })
  }
  for (const v of V.invalid.filter((x: any) => BROWSER_CODES.has(x.expected?.code))) {
    it(`rejects ${v.name} with ${v.expected.code}`, async () => {
      const keys = v.expected.code === 'KEY_REVOKED' ? keyFor('REVOKED') : v.expected.code === 'KEY_UNKNOWN' ? [] : keyFor()
      expect(await verifyToken(v.token, v.trusted_keys ?? keys)).toMatchObject({ ok: false, code: v.expected.code })
    })
  }
})
