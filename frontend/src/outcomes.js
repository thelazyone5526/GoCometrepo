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
