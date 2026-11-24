/* Live-mode seam between the screens and the real FastAPI backend (CONTRACT v0.2).
 * The backend is authoritative; this file only adapts transport details so screens written against the
 * v0.1 view types keep working. Each rule is small, named and tested. Never invent data here. */
import type { Recall } from './types'

/** Request-side rewrites: path + upload provenance. */
export function liveRequest(method: string, path: string, body: unknown): { path: string; body: unknown } {
  // The unit page needs inspections + passport history, which the v0.2 API serves from /detail.
  const unit = method === 'GET' && path.match(/^\/units\/([^/?]+)$/)
  if (unit) path = `/units/${unit[1]}/detail`
  if (typeof FormData !== 'undefined' && body instanceof FormData) {
    // A fixture capture is a SYNTHETIC render: say so to the server, which stores it as SYNTHETIC_TEST
    // and badges every result. Camera and file uploads keep the server default (LIVE_CAPTURE).
    if (body.has('fixture')) {
      body.delete('fixture')
      body.set('evidence_source', 'SYNTHETIC_TEST')
    }
    if (body.get('station_id') === '') body.delete('station_id')
  }
  return { path, body }
}

/** Response-side adapters, keyed by method + path. */
export function liveResponse(method: string, path: string, json: any): any {
  if (json == null) return json
  const p = path.split('?')[0]
  if (method === 'POST' && p === '/recalls/preview') return previewToRecall(json)
  if (method === 'GET' && p === '/policies') return flattenPolicy(json)
  return json
}

/** v0.2 preview is flat; executed recalls already nest the same impact under `affected`. */
export function previewToRecall(d: any): Recall {
  return {
    id: 'preview', preview: true,
    scope: { type: d.scope_type, id: d.scope_id, label: d.scope_label },
    reason: '', executed_by: null, executed_at: null, query_ms: d.query_ms ?? 0,
    affected: {
      units: d.units, passports_held: d.passports_held, packages_held: d.packages_held,
      blocked_at_plant: d.blocked_at_plant ?? [], loaded_not_departed: d.loaded_not_departed ?? [],
      in_transit: d.in_transit ?? [], delivered: d.delivered ?? [],
    },
    evidence: d.evidence, audit_event_ids: [],
  }
}

/** v0.2 publishes versioned policies as {version, settings}; screens read the settings directly. */
export const flattenPolicy = (d: any) => (d?.settings ? { ...d.settings, version: d.version, demo_default: d.demo_default } : d)
