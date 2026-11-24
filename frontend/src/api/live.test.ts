import { describe, expect, it } from 'vitest'
import { flattenPolicy, liveRequest, liveResponse, previewToRecall } from './live'

describe('liveRequest', () => {
  it('routes the unit page to the v0.2 detail view, leaving list and sub-routes alone', () => {
    expect(liveRequest('GET', '/units/L-2611-A-0001', undefined).path).toBe('/units/L-2611-A-0001/detail')
    expect(liveRequest('GET', '/units?lot_id=lot_1', undefined).path).toBe('/units?lot_id=lot_1')
    expect(liveRequest('GET', '/units/u1/detail', undefined).path).toBe('/units/u1/detail')
    expect(liveRequest('POST', '/units/u1', {}).path).toBe('/units/u1')
  })

  it('declares fixture captures as SYNTHETIC_TEST and never forwards the fixture hint', () => {
    const f = new FormData(); f.set('unit_id', 'u1'); f.set('fixture', 'good'); f.set('station_id', '')
    const { body } = liveRequest('POST', '/inspections', f)
    const out = body as FormData
    expect(out.get('evidence_source')).toBe('SYNTHETIC_TEST')
    expect(out.has('fixture')).toBe(false)
    expect(out.has('station_id')).toBe(false)
  })

  it('leaves real uploads to the server default (LIVE_CAPTURE)', () => {
    const f = new FormData(); f.set('unit_id', 'u1'); f.set('station_id', 'st_l2_vis')
    const out = liveRequest('POST', '/inspections', f).body as FormData
    expect(out.has('evidence_source')).toBe(false)
    expect(out.get('station_id')).toBe('st_l2_vis')
  })
})

describe('liveResponse', () => {
  const preview = {
    scope_type: 'HEAT', scope_id: 'heat_1', scope_label: 'H-7781', units: 40, passports_held: 39, packages_held: 3, query_ms: 14.4,
    unit_counts: { at_plant: 26 }, held_package_ids: ['pkg_3'], blocked_at_plant: [{ package_id: 'pkg_6', label: 'PKG-0006', units: 2 }],
    loaded_not_departed: [], in_transit: [], delivered: [{ shipment_id: 's1', code: 'SHP-0001', customer: 'X', units: 4, action: 'NOTIFY_CUSTOMER' }],
    evidence: { genealogy_query: 'heats -> …', as_of: '2026-11-24T00:00:00Z' }, preview: true,
  }

  it('nests a flat v0.2 recall preview under `affected`', () => {
    const r = liveResponse('POST', '/recalls/preview', preview)
    expect(r).toEqual(previewToRecall(preview))
    expect(r.scope).toEqual({ type: 'HEAT', id: 'heat_1', label: 'H-7781' })
    expect(r.affected.units).toBe(40)
    expect(r.affected.blocked_at_plant[0].label).toBe('PKG-0006')
    expect(r.executed_by).toBeNull()
    expect(r.audit_event_ids).toEqual([])
  })

  it('passes executed recalls and unrelated responses through untouched', () => {
    const executed = { id: 'rcl_1', affected: { units: 1 } }
    expect(liveResponse('POST', '/recalls', executed)).toBe(executed)
    expect(liveResponse('GET', '/lots', { items: [] })).toEqual({ items: [] })
  })

  it('flattens versioned policy settings', () => {
    const p = { version: 'demo-1', demo_default: true, settings: { containment: { first_n: 5 } } }
    expect(liveResponse('GET', '/policies', p)).toEqual(flattenPolicy(p))
    expect(flattenPolicy(p)).toEqual({ containment: { first_n: 5 }, version: 'demo-1', demo_default: true })
  })
})
