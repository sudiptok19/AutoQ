import * as ed from '@noble/ed25519'
import { sha256 } from '@noble/hashes/sha2'
import { b64url, canonicalize, fromB64url, hex, utf8 } from './canonical'

export interface TokenPayload { v: number; typ: 'PP' | 'PKG' | 'SNAP'; kid: string; id: string; iat: number; [k: string]: unknown }
export interface PublicKey { kid: string; public_key: string; status?: string }

export async function signToken(payload: TokenPayload, privateKey: Uint8Array): Promise<string> {
  const bytes = utf8(canonicalize(payload))
  const sig = await ed.signAsync(bytes, privateKey)
  return `AQ1.${b64url(bytes)}.${b64url(sig)}`
}

export type VerifyResult =
  | { ok: true; payload: TokenPayload; kid: string }
  | { ok: false; code: 'TOKEN_MALFORMED' | 'SIG_INVALID' | 'KEY_UNKNOWN' | 'KEY_REVOKED'; payload?: TokenPayload; kid?: string }

/** Verifies an AQ1 token against a key set. Works offline; it proves data authenticity only. */
export async function verifyToken(token: string, keys: PublicKey[]): Promise<VerifyResult> {
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
  const key = keys.find(k => k.kid === payload.kid)
  if (!key) return { ok: false, code: 'KEY_UNKNOWN', payload, kid: payload.kid }
  if (key.status === 'REVOKED') return { ok: false, code: 'KEY_REVOKED', payload, kid: payload.kid }
  let valid = false
  try { valid = sig.length === 64 && await ed.verifyAsync(sig, bytes, fromB64url(key.public_key)) } catch { valid = false }
  return valid ? { ok: true, payload, kid: payload.kid } : { ok: false, code: 'SIG_INVALID', payload, kid: payload.kid }
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
