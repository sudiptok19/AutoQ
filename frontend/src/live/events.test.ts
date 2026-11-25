import { afterEach, beforeEach, describe as group, expect, it, vi } from 'vitest'
import { Seen, backoff, connect, describe, eventsUrl, type PlantEvent } from './events'

const ev = (o: Partial<PlantEvent>): PlantEvent => ({
  event_id: 'evt_1', type: 'DISPATCH_STATE_CHANGED', severity: 'INFO', audible_siren: false, entity_type: 'SHIPMENT', entity_id: 's1',
  reason_codes: [], timestamp: '2026-11-25T10:00:00.000Z', payload: {}, ...o,
})

group('describe', () => {
  it('names an alert from its alert_type and says where it happened', () => {
    const n = describe(ev({ type: 'ALERT_CREATED', severity: 'HIGH', audible_siren: true, reason_codes: ['SIG_INVALID'],
      payload: { alert_type: 'COUNTERFEIT_SUSPECT', package: 'PKG-0004', shipment: 'SHP-0004' } }))
    expect(n).toEqual({ title: 'Counterfeit label suspected', body: 'PKG-0004 · SHP-0004 — SIG_INVALID', href: '/dispatch', tone: 'reject', siren: true })
  })

  it('links a recall alert to the recall and a lot alert to the lot', () => {
    expect(describe(ev({ type: 'ALERT_CREATED', severity: 'CRITICAL', entity_type: 'HEAT', payload: { alert_type: 'QUARANTINE', recall_id: 'rcl_9', scope: 'H-7781' } }))?.href).toBe('/recalls/rcl_9')
    expect(describe(ev({ type: 'ALERT_CREATED', severity: 'HIGH', entity_type: 'LOT', entity_id: 'lot_3', payload: { alert_type: 'SAMPLE_FAILED' } }))?.href).toBe('/lots/lot_3')
  })

  it('falls back to a generic title for an alert type it does not know', () => {
    expect(describe(ev({ type: 'ALERT_CREATED', severity: 'MEDIUM', payload: { alert_type: 'SOMETHING_NEW' } }))?.title).toBe('Alert raised')
  })

  it('raises physical tasks and revocations, but routine scans only refresh views', () => {
    expect(describe(ev({ type: 'SEGREGATION_TASK_CREATED', severity: 'HIGH', payload: { type: 'REMOVAL_REQUIRED', package: 'PKG-0005' } }))?.title).toBe('Physical task: Unload & segregate')
    expect(describe(ev({ type: 'PASSPORT_REVOKED', severity: 'HIGH', payload: { serial: 'L-1', unit_id: 'u1' } }))?.href).toBe('/units/u1')
    expect(describe(ev({ reason_codes: ['SEAL_RECHECK_REQUIRED'], severity: 'MEDIUM' }))).toBeNull()
    expect(describe(ev({ type: 'LOT_4M_CHANGED' }))).toBeNull()
  })
})

group('Seen / backoff / url', () => {
  it('drops repeats and forgets the oldest beyond its window', () => {
    const s = new Seen(2)
    expect([s.add('a'), s.add('a'), s.add('b'), s.add('c'), s.add('a')]).toEqual([true, false, true, true, true])
  })
  it('backs off exponentially to a 15 s cap with jitter', () => {
    expect([0, 1, 2, 10].map(n => backoff(n, () => 0.5))).toEqual([1000, 2000, 4000, 15000])
    expect(backoff(0, () => 0)).toBe(800)
  })
  it('uses wss behind HTTPS and encodes the ticket', () => {
    expect(eventsUrl('a+b', { protocol: 'https:', host: 'plant.local' })).toBe('wss://plant.local/ws/events?ticket=a%2Bb')
    expect(eventsUrl('t', { protocol: 'http:', host: 'localhost:5173' })).toBe('ws://localhost:5173/ws/events?ticket=t')
  })
})

class FakeSocket {
  static all: FakeSocket[] = []
  onopen?: () => void; onmessage?: (m: { data: string }) => void; onclose?: () => void
  closed = false
  constructor(public url: string) { FakeSocket.all.push(this) }
  close() { this.closed = true; this.onclose?.() }
  emit(e: object) { this.onmessage?.({ data: JSON.stringify(e) }) }
}

group('connect', () => {
  beforeEach(() => { vi.useFakeTimers(); FakeSocket.all = [] })
  afterEach(() => vi.useRealTimers())

  const setup = () => {
    let n = 0
    const log = { status: [] as string[], events: [] as string[], syncs: 0 }
    const stop = connect({
      ticket: async () => `t${++n}`, url: t => `ws://x/ws/events?ticket=${t}`, open: u => new FakeSocket(u) as unknown as WebSocket,
      onStatus: s => log.status.push(s), onEvent: e => log.events.push(e.event_id), onSync: () => log.syncs++,
    })
    return { log, stop }
  }

  it('goes live, delivers each event once and ignores garbage', async () => {
    const { log } = setup()
    await vi.runAllTicks()
    const s = FakeSocket.all[0]
    s.onopen!()
    s.emit(ev({ event_id: 'e1' })); s.emit(ev({ event_id: 'e1' })); s.onmessage!({ data: 'not json' }); s.emit(ev({ event_id: 'e2' }))
    expect(log.status).toEqual(['connecting', 'live'])
    expect(log.events).toEqual(['e1', 'e2'])
    expect(log.syncs).toBe(1)
  })

  it('reconnects with a fresh single-use ticket, refetches on reconnect, and still dedupes across sockets', async () => {
    const { log } = setup()
    await vi.runAllTicks()
    FakeSocket.all[0].onopen!(); FakeSocket.all[0].emit(ev({ event_id: 'e1' }))
    FakeSocket.all[0].onclose!()                                      // server restart / Wi-Fi drop
    expect(log.status.at(-1)).toBe('offline')
    await vi.advanceTimersByTimeAsync(1300)
    const second = FakeSocket.all[1]
    expect(second.url).toContain('ticket=t2')
    second.onopen!(); second.emit(ev({ event_id: 'e1' })); second.emit(ev({ event_id: 'e3' }))
    expect(log.events).toEqual(['e1', 'e3'])
    expect(log.syncs).toBe(2)
  })

  it('stops for good when the caller unsubscribes (sign-out)', async () => {
    const { log, stop } = setup()
    await vi.runAllTicks()
    stop()
    expect(FakeSocket.all[0].closed).toBe(true)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSocket.all).toHaveLength(1)
    expect(log.status).not.toContain('offline')
  })
})
