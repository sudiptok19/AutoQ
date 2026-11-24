// Hand-written from docs/CONTRACT.md v0.1. Replace with `npm run gen:api` output once the backend serves /openapi.json.

export type Role = 'admin' | 'operator' | 'quality_engineer' | 'quality_manager' | 'dispatcher'
export interface User { id: string; username: string; display_name: string; roles: Role[] }

export type QualityStatus = 'UNINSPECTED' | 'PASS' | 'HOLD' | 'REJECT'
export type PassportStatus = 'ACTIVE' | 'HELD' | 'REVOKED'
export type Verdict = 'PASS' | 'HOLD' | 'REJECT'
export interface Reason { code: string; message: string; ref?: string | null }

export interface Supplier { id: string; name: string; code: string; synthetic?: boolean }
export interface Part { id: string; part_number: string; name: string; drawing_rev: string; inspection_profile_id: string }
export interface Line { id: string; name: string; station_id: string }
export interface Heat { id: string; heat_code: string; supplier_id: string; material_grade: string; certificate_sha256: string; certificate_name: string; received_at: string; held?: boolean }
export interface Lot {
  id: string; lot_code: string; part_id: string; line_id: string; heat_ids: string[]; planned_qty: number; created_at: string
  counts: Record<QualityStatus, number>; containment_ids: string[]; held: boolean
}
export interface Eligibility { dispatch_eligible: boolean; blockers: Reason[] }
export interface Unit {
  id: string; serial: string; lot_id: string; part_id: string; quality_status: QualityStatus
  passport_id: string | null; package_id: string | null; eligibility: Eligibility; created_at: string; held: boolean
}

export interface QualityGate {
  passed: boolean; blur_laplacian_var: number; glare_ratio: number; width: number; height: number
  thresholds: { blur_min: number; glare_max: number; min_w: number; min_h: number }; failures: string[]
}
export interface Analysis {
  method: string; method_version: string; calibrated: boolean; score: number | null
  hold_threshold: number; reject_threshold: number; threshold_tightened: boolean
  regions: { x: number; y: number; w: number; h: number; score: number }[]
}
export interface Passport {
  id: string; unit_id: string; status: PassportStatus; token: string; kid: string; payload_version: number
  issued_at: string; inspection_id: string; inspection_digest: string
}
export interface Inspection {
  id: string; unit_id: string; station_id: string; created_by: string; created_at: string
  evidence: { image_sha256: string; image_url: string; heatmap_url?: string | null; aligned_url?: string | null }
  quality_gate: QualityGate; analysis: Analysis; verdict: Verdict; reasons: Reason[]
  passport: Passport | null; synthetic: boolean; review?: { action: string; reason: string; by: string; at: string } | null
}

export type FourMCategory = 'MAN' | 'MACHINE' | 'MATERIAL' | 'METHOD'
export interface FourMEvent {
  id: string; code: string; category: FourMCategory; planned: boolean; line_id: string; description: string; reason: string
  effective_at: string; created_by: string; created_at: string; containment_id: string
}
export interface Obligation { type: 'FULL_INSPECT_FIRST_N' | 'RECHECK_UNIT'; required: number; completed: number; unit_ids?: string[] }
export interface Containment {
  id: string; event_id: string; status: 'ACTIVE' | 'CLEARED'; line_id: string
  rule: { first_n: number; lookback_minutes: number; threshold_tightening: number; recheck_existing: boolean }
  affected_lot_ids: string[]; affected_unit_ids: string[]; obligations: Obligation[]
  cleared_by?: string | null; cleared_at?: string | null; clear_reason?: string | null; created_at: string
}

export interface Customer { id: string; name: string; city: string }
export interface Order { id: string; order_no: string; customer_id: string; part_id: string; qty_units: number; destination: string; created_at: string }
export type PackageState = 'OPEN' | 'SEALED' | 'SEAL_REVIEW' | 'LOADED' | 'IN_TRANSIT' | 'DELIVERED'
export interface Package {
  id: string; label: string; order_id: string; state: PackageState; unit_ids: string[]; held: boolean; hold_reasons: Reason[]
  token: string | null; seal_ref_sha256: string | null; last_seal_check: SealCheck | null; created_at: string
}
export interface SealCheck {
  id: string; package_id: string; kind: 'ENROLL' | 'RECHECK'; result: 'INTACT' | 'SUSPICIOUS' | 'UNVERIFIABLE' | null; method_version: string
  metrics: Record<string, number | Record<string, number>>; explanation: string; created_by: string; created_at: string; synthetic: boolean
}

export type ShipmentStatus = 'OPEN' | 'CLOSED' | 'IN_TRANSIT' | 'DELIVERED'
export interface MovementEvent { type: 'DEPARTED' | 'CHECKPOINT' | 'DELIVERED'; location: string; occurred_at: string }
export interface Shipment {
  id: string; code: string; order_id: string; vehicle_no: string; dock: string; status: ShipmentStatus
  loaded_package_ids: string[]; movements: MovementEvent[]; created_at: string
}
export type GateName = 'SIGNATURE' | 'KEY' | 'IDENTITY' | 'LIFECYCLE' | 'CONTAINMENT' | 'QUARANTINE' | 'ORDER' | 'DUPLICATE' | 'SEAL' | 'QUANTITY' | 'SEQUENCE'
export interface GateCheck { gate: GateName; status: 'PASS' | 'FAIL' | 'SKIP' | 'WARN'; detail?: string }
export interface ScanDecision {
  scan_id: string; decision: 'ACCEPTED' | 'BLOCKED'; replay: boolean; package_id: string | null; package_label?: string | null
  reasons: Reason[]; checks: GateCheck[]
  shipment_progress: { loaded_units: number; expected_units: number; loaded_packages: number }
  decided_at: string; decided_by: string; client_event_id: string; offline?: boolean
}

export type NodeType = 'SUPPLIER' | 'HEAT' | 'LOT' | 'UNIT' | 'INSPECTION' | 'PASSPORT' | 'PACKAGE' | 'ORDER' | 'SHIPMENT' | 'CUSTOMER'
export interface TraceNode { id: string; type: NodeType; label: string; status?: string; held?: boolean; meta?: Record<string, any> }
export interface TraceEdge { from: string; to: string; type: string }
export interface TraceSummary { units: number; packages: number; at_plant: number; loaded: number; in_transit: number; delivered: number }
export interface TraceGraph { root: { type: NodeType; id: string; label: string }; nodes: TraceNode[]; edges: TraceEdge[]; summary: TraceSummary }

export type ScopeType = 'HEAT' | 'LOT' | 'UNIT' | 'PACKAGE'
export interface RecallImpact {
  units: number; passports_held: number; packages_held: number
  blocked_at_plant: { package_id: string; label: string; units: number }[]
  loaded_not_departed: { shipment_id: string; code: string; units: number; action: string }[]
  in_transit: { shipment_id: string; code: string; vehicle_no: string; customer: string; units: number; action: string }[]
  delivered: { shipment_id: string; code: string; customer: string; units: number; action: string }[]
}
export interface Recall {
  id: string; scope: { type: ScopeType; id: string; label: string }; reason: string; executed_by: string | null; executed_at: string | null
  query_ms: number; affected: RecallImpact; evidence: { genealogy_query: string; as_of: string }; audit_event_ids: string[]; preview?: boolean
  unit_counts?: { at_plant: number; loaded_not_departed: number; in_transit: number; delivered: number }
  segregation_tasks?: SegregationTask[]; released_at?: string | null; released_by?: string | null; release_reason?: string | null
}
export type TaskType = 'REMOVAL_REQUIRED' | 'IN_TRANSIT_RECALL' | 'NOTIFY_CUSTOMER' | (string & {})
export interface SegregationTask {
  id: string; type: TaskType; status: 'OPEN' | 'COMPLETED'; reason: string
  package_id: string | null; package_label: string | null; shipment_id: string | null; shipment_code: string | null
  recall_id: string | null; scan_id: string | null; consignee_action: Record<string, any> | null; created_at: string
  completed_by: string | null; completed_at: string | null; completion_reason: string | null
}

export interface SpcPoint { inspection_id: string; t: string; score: number; z: number; cusum: number; synthetic: boolean }
export interface SpcSeries {
  part_id: string; line_id: string; method_version: string
  baseline: { n: number; mean: number; std: number; frozen_at: string | null; status: 'READY' | 'COLLECTING' | 'LOW_VARIANCE' }
  rule: { type: 'CUSUM_UPPER'; k_sigma: number; h_sigma: number }
  points: SpcPoint[]; alerts: { id: string; at_index?: number; inspection_id?: string; status?: string; message: string }[]
}

export interface AuditEvent { id: string; seq: number; at: string; actor: string; action: string; entity_type: string; entity_id: string; reason?: string; data?: Record<string, any>; prev_hash: string; hash: string }

export interface DashboardSummary {
  units_by_status: Record<QualityStatus, number>; active_containments: number; held_packages: number; open_shipments: number
  open_alerts: number; open_tasks?: number; recent_scans: ScanDecision[]; dispatch_eligible_units: number; first_pass_yield: number | null
}

export type ReportItemKind = 'FACT' | 'HYPOTHESIS' | 'PROPOSED' | 'PENDING_HUMAN'
export interface ReportItem { kind: ReportItemKind; text: string; evidence: { type: string; id: string; label: string; sha256?: string | null }[] }
export type ReportSource = 'RECALL' | 'FOUR_M' | 'ALERT'
export interface Report8D {
  id: string; revision_id: string; status: 'DRAFT' | 'APPROVED'; source_type: ReportSource; source_id: string; generator: string
  created_at: string; as_of: string; counts: Record<string, number>; evidence_source: string; meta: { source_label?: string; [k: string]: any }
  sections: { key: string; title: string; items: ReportItem[] }[]; approved_by: string | null; approved_at: string | null
  snapshot_sha256: string; pdf_url: string; pdf_sha256: string | null; revisions: number
}
export interface UploadedEvidence { sha256: string; media_type: string; byte_size: number; url: string; evidence_source: string; purpose: string }

export interface ApiError { code: string; message: string; details?: any; status: number }
export interface Page<T> { items: T[]; next_cursor: string | null }
