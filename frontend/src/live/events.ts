/** Real-time plant events (live mode). The server relays committed outbox rows over /ws/events
 *  (contracts/ws_events.schema.json). The socket is a speed-up, never the source of truth: every
 *  event only tells the UI to refetch, and a reconnect refetches everything (REST is the catch-up). */

export type Severity = 'INFO' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export interface PlantEvent {
  event_id: string
  type: 'ALERT_CREATED' | 'ALERT_RESOLVED' | 'PASSPORT_REVOKED' | 'LOT_4M_CHANGED' | 'DISPATCH_STATE_CHANGED' | 'SEGREGATION_TASK_CREATED' | 'SPC_DRIFT_WARNING'
  severity: Severity
  audible_siren: boolean
  entity_type: string
  entity_id: string
  reason_codes: string[]
  timestamp: string
  payload: Record<string, any>
}

export interface Notice { title: string; body: string; href: string; tone: 'reject' | 'hold' | 'info'; siren: boolean }

const ALERT: Record<string, string> = {
  COUNTERFEIT_SUSPECT: 'Counterfeit label suspected',
  SEAL_TAMPER: 'Seal tamper suspected',
  FOUR_M_HOLD: '4M change point: line contained',
  QUARANTINE: 'Recall quarantine executed',
  QUARANTINE_BLOCK: 'Quarantined package at the gate',
  REVOKED_SCAN: 'Revoked passport scanned',
  DUPLICATE_LOAD: 'Duplicate load attempt',
  DEPARTURE_BLOCKED: 'Departure blocked',
  SPC_DRIFT: 'Process drift on a station',
  SAMPLE_FAILED: 'Lot sample failed',
  OFFLINE_CHAIN_BROKEN: 'Scanner event chain broken',
  OFFLINE_CONFLICT: 'Offline scan rejected on sync',
}
const TASK: Record<string, string> = { REMOVAL_REQUIRED: 'Unload & segregate', SEGREGATION_REQUIRED: 'Segregate', IN_TRANSIT_RECALL: 'Intercept in transit', NOTIFY_CUSTOMER: 'Notify customer' }

function hrefFor(e: PlantEvent): string {
  const p = e.payload
  if (p.recall_id) return `/recalls/${p.recall_id}`
  switch (e.entity_type) {
    case 'SHIPMENT': return '/dispatch'
    case 'PACKAGE': return '/dispatch'
    case 'LINE': case 'CONTAINMENT': return '/containment'
    case 'STATION': return '/spc'
    case 'LOT': return `/lots/${e.entity_id}`
    case 'UNIT': return `/units/${e.entity_id}`
    default: return '/'
  }
}

/** The pop-up for an event, or null when it should only refresh what is on screen.
 *  Notices are reserved for things a person must act on; routine scans just update the views. */
export function describe(e: PlantEvent): Notice | null {
  const p = e.payload ?? {}
  const codes = e.reason_codes?.join(' · ')
  const tone = e.severity === 'CRITICAL' || e.severity === 'HIGH' ? 'reject' : e.severity === 'MEDIUM' ? 'hold' : 'info'
  switch (e.type) {
    case 'ALERT_CREATED': {
      const where = [p.package, p.shipment, p.scope, p.lot_code].filter(Boolean).join(' · ')
      return { title: ALERT[p.alert_type] ?? 'Alert raised', body: [where, codes].filter(Boolean).join(' — '), href: hrefFor(e), tone, siren: e.audible_siren }
    }
    case 'SEGREGATION_TASK_CREATED':
      return { title: `Physical task: ${TASK[p.type] ?? String(p.type ?? 'follow-up').toLowerCase()}`, body: p.package ? `Package ${p.package}` : 'Shipment-level action', href: '/recalls', tone, siren: e.audible_siren }
    case 'PASSPORT_REVOKED':
      return { title: 'Passport revoked', body: [p.serial, codes].filter(Boolean).join(' — '), href: p.unit_id ? `/units/${p.unit_id}` : '/', tone, siren: e.audible_siren }
    default:
      return e.audible_siren ? { title: e.type.replaceAll('_', ' ').toLowerCase(), body: codes, href: hrefFor(e), tone, siren: true } : null
  }
}

/** Remembers the last N event ids: the relay delivers at-least-once and a reconnect may overlap. */
export class Seen {
  private ids = new Set<string>(); private order: string[] = []
  constructor(private max = 500) {}
  /** true the first time an id is seen */
  add(id: string): boolean {
    if (this.ids.has(id)) return false
    this.ids.add(id); this.order.push(id)
    if (this.order.length > this.max) this.ids.delete(this.order.shift()!)
    return true
  }
}

/** 1 s, 2 s, 4 s … capped at 15 s, with ±20 % jitter so a plant full of tablets doesn't reconnect in lockstep. */
export const backoff = (attempt: number, rand = Math.random) => Math.round(Math.min(15_000, 1000 * 2 ** attempt) * (0.8 + rand() * 0.4))

export type SocketStatus = 'connecting' | 'live' | 'offline'

export interface SocketDeps {
  ticket: () => Promise<string>
  url: (ticket: string) => string
  open?: (url: string) => WebSocket
  onEvent: (e: PlantEvent) => void
  onStatus: (s: SocketStatus) => void
  /** fired on every successful (re)connect: the moment to refetch whatever may have been missed */
  onSync: () => void
}

/** One authenticated subscription with automatic reconnect. A fresh single-use ticket per attempt. */
export function connect(d: SocketDeps): () => void {
  const open = d.open ?? (u => new WebSocket(u))
  const seen = new Seen()
  let ws: WebSocket | null = null, attempt = 0, timer: ReturnType<typeof setTimeout> | undefined, stopped = false
  const retry = () => { if (stopped) return; d.onStatus('offline'); timer = setTimeout(start, backoff(attempt++)) }
  async function start() {
    if (stopped) return
    d.onStatus('connecting')
    let t: string
    try { t = await d.ticket() } catch { retry(); return }
    if (stopped) return
    const sock = ws = open(d.url(t))
    sock.onopen = () => { attempt = 0; d.onStatus('live'); d.onSync() }
    sock.onmessage = m => {
      let e: PlantEvent
      try { e = JSON.parse(String(m.data)) } catch { return }
      if (e?.event_id && seen.add(e.event_id)) d.onEvent(e)
    }
    sock.onclose = () => { if (ws === sock) { ws = null; retry() } }
  }
  start()
  return () => { stopped = true; clearTimeout(timer); const s = ws; ws = null; s?.close() }
}

export function eventsUrl(ticket: string, loc: Pick<Location, 'protocol' | 'host'> = location): string {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/ws/events?ticket=${encodeURIComponent(ticket)}`
}
