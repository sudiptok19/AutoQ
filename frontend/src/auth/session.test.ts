import { describe, expect, it } from 'vitest'
import { restoreSession } from './session'

const u = { id: 'usr_kiran', username: 'disp.kiran', display_name: 'Kiran Das', roles: ['dispatcher'] } as any
const err = (status: number, code: string) => Object.assign(new Error(code), { status, code })

describe('restoreSession', () => {
  it('verifies online', async () => {
    expect(await restoreSession(async () => u, null)).toEqual({ kind: 'verified', user: u })
  })
  it('keeps the cached operator when the network is down (airplane mode PWA reopen)', async () => {
    expect(await restoreSession(async () => { throw err(0, 'NETWORK_UNAVAILABLE') }, u)).toEqual({ kind: 'unverified', user: u, reason: 'NETWORK_UNAVAILABLE' })
  })
  it('keeps the cached operator on a gateway error (502 from Caddy while backend restarts)', async () => {
    expect((await restoreSession(async () => { throw err(502, 'HTTP_502') }, u)).kind).toBe('unverified')
  })
  it('signs out only when the server rejects the token', async () => {
    expect(await restoreSession(async () => { throw err(401, 'TOKEN_EXPIRED') }, u)).toEqual({ kind: 'signed_out', reason: 'TOKEN_EXPIRED' })
    expect((await restoreSession(async () => { throw err(403, 'ROLE_FORBIDDEN') }, u)).kind).toBe('signed_out')
  })
  it('cannot invent an identity offline without a cached one', async () => {
    expect((await restoreSession(async () => { throw err(0, 'NETWORK_UNAVAILABLE') }, null)).kind).toBe('signed_out')
  })
})
