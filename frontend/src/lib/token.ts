import * as ed from '@noble/ed25519'
import { sha256 } from '@noble/hashes/sha2'
import { b64url, canonicalize, fromB64url, hex, utf8 } from './canonical'

export interface TokenPayload { v: number; typ: 'PP' | 'PKG' | 'SNAP'; kid: string; id: string; iat: number; [k: string]: unknown }
export interface PublicKey { kid: string; public_key: string; status?: string; not_before?: number; not_after?: number }
export interface VerifyOptions {
  /** Check time (Unix seconds). When given, the key must have been valid when the token was signed (iat), and an
   *  ACTIVE key must still be valid now — the same rule as the server (passport_crypto._trusted_key). Fails closed
   *  when the key carries no validity window. */
  asOf?: number
  /** Allowed clock skew for a token whose iat is after asOf (device clocks). Default 0. */
  skewSec?: number
}

export async function signToken(payload: TokenPayload, privateKey: Uint8Array): Promise<string> {
  const bytes = utf8(canonicalize(payload))
  const sig = await ed.signAsync(bytes, privateKey)
  return `AQ1.${b64url(bytes)}.${b64url(sig)}`
}

export type VerifyResult =
  | { ok: true; payload: TokenPayload; kid: string }
  | { ok: false; code: 'TOKEN_MALFORMED' | 'SIG_INVALID' | 'KEY_UNKNOWN' | 'KEY_REVOKED' | 'KEY_EXPIRED' | 'ISSUED_IN_FUTURE'; payload?: TokenPayload; kid?: string }

/** Verifies an AQ1 token against a key set. Works offline; it proves data authenticity only. */
const uint = (n: unknown): n is number => typeof n === 'number' && Number.isSafeInteger(n) && n >= 0

export async function verifyToken(token: string, keys: PublicKey[], opts: VerifyOptions = {}): Promise<VerifyResult> {
  const parts = token.trim().split('.')
  if (parts.length !== 3 || parts[0] !== 'AQ1') return { ok: false, code: 'TOKEN_MALFORMED' }
  let bytes: Uint8Array, sig: Uint8Array, payload: TokenPayload
  try {
    bytes = fromB64url(parts[1]); sig = fromB64url(parts[2])
    payload = JSON.parse(new TextDecoder().decode(bytes))
    if (typeof payload !== 'object' || payload === null || typeof payload.kid !== 'string') throw new Error('bad payload')
    // Reject non-canonical encodings so one payload has exactly one valid byte form.
    if (canonicalize(payload) !== new TextDecoder().decode(bytes)) throw new Error('non-canonical')
  } catch { return { ok: false, code: 'TOKEN_MALFORMED' } }
  const matches = keys.filter(k => k.kid === payload.kid)
  const fail = (code: Exclude<VerifyResult, { ok: true }>['code']): VerifyResult => ({ ok: false, code, payload, kid: payload.kid })
  if (matches.length !== 1) return fail('KEY_UNKNOWN')
  const key = matches[0]
  if (key.status === 'REVOKED') return fail('KEY_REVOKED')
  if (key.status !== undefined && key.status !== 'ACTIVE' && key.status !== 'RETIRED') return fail('KEY_UNKNOWN')
  if (opts.asOf !== undefined) {
    const { not_before: nb, not_after: na } = key, iat = payload.iat
    if (!uint(iat)) return fail('TOKEN_MALFORMED')
    if (!uint(nb) || !uint(na) || !(nb <= iat && iat <= na)) return fail('KEY_EXPIRED')
    if ((key.status ?? 'ACTIVE') === 'ACTIVE' && !(nb <= opts.asOf && opts.asOf <= na)) return fail('KEY_EXPIRED')
  }
  let valid = false
  try { valid = sig.length === 64 && await ed.verifyAsync(sig, bytes, fromB64url(key.public_key)) } catch { valid = false }
  if (!valid) return fail('SIG_INVALID')
  if (opts.asOf !== undefined && payload.iat > opts.asOf + (opts.skewSec ?? 0)) return fail('ISSUED_IN_FUTURE')
  return { ok: true, payload, kid: payload.kid }
}

export function decodeTokenUnsafe(token: string): TokenPayload | null {
  try { return JSON.parse(new TextDecoder().decode(fromB64url(token.split('.')[1]))) } catch { return null }
}

export const sha256Hex = (data: Uint8Array | string) => hex(sha256(typeof data === 'string' ? utf8(data) : data))

export async function newKeyPair() {
  const priv = ed.utils.randomPrivateKey()
  const pub = await ed.getPublicKeyAsync(priv)
  return { priv, pub: b64url(pub) }
}

/** Demo attacker: rewrite a field in the payload, re-encode canonically, keep the old signature. */
export function forgeToken(token: string, mutate: (p: Record<string, any>) => void): string {
  const [h, , s] = token.split('.')
  const p = decodeTokenUnsafe(token) as Record<string, any>
  mutate(p)
  return `${h}.${b64url(utf8(canonicalize(p)))}.${s}`
}
