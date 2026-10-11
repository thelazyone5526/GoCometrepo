// Shared outcome/verdict labels, kept in one place so the run list and the decision card
// never drift apart on wording.
//
// Note (flag for reconciliation, design §10.1): §3.5 (Router) names outcomes
// `auto_approve` / `human_review` / `amendment_request`, but §4 (the `runs.outcome` column)
// names them `auto_approved` / `human_review` / `amendment_requested`. This UI uses the §4
// (past-tense) names throughout, since GET /api/runs/{id} is documented as reading the
// `runs` table. If the real API ever answers with the §3.5 names instead, `OUTCOME_LABELS`
// below is the one place to add them.
export const OUTCOME_LABELS = {
  auto_approved: 'Auto-approved',
  human_review: 'Needs human review',
  amendment_requested: 'Amendment requested',
}

export function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] ?? outcome ?? 'Unknown'
}

export const VERDICT_META = {
  match: { label: 'Match', icon: '✓' },
  mismatch: { label: 'Mismatch', icon: '✕' },
  uncertain: { label: 'Uncertain', icon: '?' },
  not_applicable: { label: 'Not applicable', icon: '–' },
}

export function verdictMeta(verdict) {
  return VERDICT_META[verdict] ?? { label: verdict ?? 'Unknown', icon: '?' }
}

export const DECISION_SOURCE_LABELS = {
  llm: 'model decision',
  code_override: 'overridden by code',
  fallback: 'fallback template',
}

export function decisionSourceLabel(source) {
  return DECISION_SOURCE_LABELS[source] ?? source ?? 'unknown'
}

export const STEP_ORDER = ['prepare', 'extract', 'validate', 'route']

// Part 2 (part2-shipment-workflow design §2): the shipment-level outcome names, from
// `shipment/decision.py`'s `ShipmentOutcome` — present-tense, unlike `runs.outcome` above,
// since `shipment_reviews.outcome` stores exactly what the Router/decision module emits
// with no §10.1-style remapping.
export const SHIPMENT_OUTCOME_LABELS = {
  auto_approve: 'Recommended for approval',
  human_review: 'Needs human review',
  amendment_request: 'Amendment requested',
}

export function shipmentOutcomeLabel(outcome) {
  return SHIPMENT_OUTCOME_LABELS[outcome] ?? outcome ?? 'Not decided yet'
}

// `shipment_reviews.review_state` (design §3): pending_review, approved_sent,
// amendment_sent, reviewed_no_send, failed.
export const REVIEW_STATE_LABELS = {
  pending_review: 'Pending review',
  approved_sent: 'Approved and sent',
  amendment_sent: 'Amendment sent',
  reviewed_no_send: 'Reviewed, not sent',
  failed: 'Failed',
}

export function reviewStateLabel(state) {
  return REVIEW_STATE_LABELS[state] ?? state ?? 'Unknown'
}

// `shipment_documents`/`runs` document status, as `api/shipment_routes.py::_document_status`
// reports it: waiting (no run started yet), processing, completed — plus a derived "failed"
// when a completed run carries an escalation reason (requirement 8.2, 8.4).
export function documentStatusMeta(doc) {
  if (doc.run_status === 'waiting') {
    return { label: 'Waiting', icon: '…' }
  }
  if (doc.run_status === 'processing') {
    return { label: 'Processing', icon: '…' }
  }
  if (doc.run_escalation_reason) {
    return { label: 'Failed', icon: '✕' }
  }
  return { label: 'Done', icon: '✓' }
}

// A field name as a person would write it: "hs_code" -> "HS code", "gross_weight" ->
// "Gross weight". Used on the Part 2 shipment screen instead of CSS capitalisation, which turned
// "hs code" into "Hs Code" and file names into "Bill-Of-Lading.Pdf".
const FIELD_LABEL_OVERRIDES = { hs_code: 'HS code' }

export function fieldLabel(name) {
  if (FIELD_LABEL_OVERRIDES[name]) return FIELD_LABEL_OVERRIDES[name]
  const spaced = (name ?? '').replaceAll('_', ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export const CROSS_CHECK_META = {
  consistent: { label: 'Consistent', icon: '✓' },
  inconsistent: { label: 'Inconsistent', icon: '✕' },
  uncertain: { label: 'Uncertain', icon: '?' },
  not_applicable: { label: 'Not applicable', icon: '–' },
}

export function crossCheckMeta(result) {
  return CROSS_CHECK_META[result] ?? { label: result ?? 'Unknown', icon: '?' }
}
