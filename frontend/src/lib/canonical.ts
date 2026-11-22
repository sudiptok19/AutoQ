/** Canonical JSON per CONTRACT §6: sorted keys, no whitespace, no floats. */
export function canonicalize(v: unknown): string {
  if (v === null || typeof v === 'boolean' || typeof v === 'string') return JSON.stringify(v)
  if (typeof v === 'number') {
    if (!Number.isInteger(v)) throw new Error('canonical JSON forbids non-integer numbers')
    return String(v)
  }
  if (Array.isArray(v)) return `[${v.map(canonicalize).join(',')}]`
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).filter(k => o[k] !== undefined).sort().map(k => `${JSON.stringify(k)}:${canonicalize(o[k])}`).join(',')}}`
  }
  throw new Error(`cannot canonicalize ${typeof v}`)
}

const enc = new TextEncoder()
export const utf8 = (s: string) => enc.encode(s)

export function b64url(bytes: Uint8Array): string {
  let s = ''
  for (const b of bytes) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
export function fromB64url(s: string): Uint8Array {
  if (!/^[A-Za-z0-9_-]*$/.test(s)) throw new Error('invalid base64url')
  const pad = s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)
  const bin = atob(pad)
  return Uint8Array.from(bin, c => c.charCodeAt(0))
}
export const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, '0')).join('')
