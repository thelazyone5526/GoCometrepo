/**
 * MOCK DATA — delete this whole file once the real backend (design §6/§11) exists.
 *
 * Shaped to match design §4's data model as closely as a JSON-over-the-wire shape can:
 *   - `runs` mirrors the `runs` table columns.
 *   - each run's `field_results` array mirrors the `field_results` table columns
 *     (one row per field, joined in here rather than fetched separately, since
 *     GET /api/runs/{id} returns "the full run" per design §6).
 *
 * `api.js` is the only file that imports this module. When the backend is ready, delete
 * this file and flip `USE_MOCK` to `false` in `api.js` — no other file should ever import
 * from here directly.
 */

// The only customer with a rules file in `backend/rules/` right now (design §3.4).
export const MOCK_CUSTOMERS = [{ customer_id: 'acme', customer_name: 'ACME Electronics Pte. Ltd.' }]

// design §3.3 / §3.4: the 8 fields every commercial invoice carries for this customer.
export const FIELD_NAMES = [
  'consignee',
  'hs_code',
  'port_of_loading',
  'port_of_discharge',
  'incoterms',
  'goods_description',
  'gross_weight',
  'invoice_number',
]

function fieldResult(overrides) {
  return {
    field_name: overrides.field_name,
    value: overrides.value ?? null,
    source_text: overrides.source_text ?? overrides.value ?? null,
    page: overrides.page ?? 1,
    model_rating: overrides.model_rating ?? 0.95,
    grounding: overrides.grounding ?? 'exact',
    ocr_reading: overrides.ocr_reading ?? null,
    format_ok: overrides.format_ok ?? true,
    extraction_confidence: overrides.extraction_confidence ?? 0.95,
    verdict: overrides.verdict,
    found: overrides.found ?? overrides.value ?? null,
    expected: overrides.expected ?? null,
    rule_id: overrides.rule_id ?? null,
    verdict_reason: overrides.verdict_reason ?? '',
    verdict_confidence: overrides.verdict_confidence ?? overrides.extraction_confidence ?? 0.95,
  }
}

// --- Run 1: clean submission, every field matches, auto-approved. -------------------------
const RUN_CLEAN = {
  id: 'run_9f2c1a',
  document_id: 'doc_01-clean-correct',
  filename: '01-clean-correct.pdf',
  customer_id: 'acme',
  customer_name: 'ACME Electronics Pte. Ltd.',
  status: 'completed',
  current_step: 'route',
  outcome: 'auto_approved',
  reasoning:
    'All 8 fields matched ACME\u2019s rules. The consignee name, HS code, ports, Incoterm, ' +
    'goods description, gross weight and invoice number all grounded exactly on the page and ' +
    'passed their rule checks, with no field falling below the confidence threshold. No ' +
    'discrepancies were found, so the document is approved without review.',
  decision_source: 'llm',
  override_reason: null,
  escalation_reason: null,
  amendment_draft: null,
  llm_calls: 3,
  fallback_used: false,
  input_tokens: 3421,
  output_tokens: 612,
  cost_usd: 0.0,
  latency_ms: 4820,
  started_at: '2026-09-29T09:12:03Z',
  finished_at: '2026-09-29T09:12:08Z',
  field_results: [
    fieldResult({
      field_name: 'consignee',
      value: 'ACME Electronics Pte. Ltd.',
      verdict: 'match',
      expected: 'ACME Electronics Pte. Ltd.',
      rule_id: 'R-CON-1',
      verdict_reason: 'matches the registered name on file',
    }),
    fieldResult({
      field_name: 'hs_code',
      value: '847130',
      verdict: 'match',
      expected: '847130, 847160, 847330',
      rule_id: 'R-HS-1',
      verdict_reason: 'in list',
    }),
    fieldResult({
      field_name: 'port_of_loading',
      value: 'Shanghai',
      source_text: 'SHANGHAI, CHINA',
      verdict: 'match',
      expected: 'Shanghai, Yantian',
      rule_id: 'R-POL-1',
      verdict_reason: 'in list',
    }),
    fieldResult({
      field_name: 'port_of_discharge',
      value: 'Singapore',
      source_text: 'SINGAPORE',
      verdict: 'match',
      expected: 'Singapore',
      rule_id: 'R-POD-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'incoterms',
      value: 'CIF',
      source_text: 'CIF Singapore',
      verdict: 'match',
      expected: 'CIF',
      rule_id: 'R-INC-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'goods_description',
      value: 'Portable laptop computers, 14-inch',
      verdict: 'match',
      expected: 'specific enough for customs classification',
      rule_id: 'R-DESC-1',
      model_rating: 0.9,
      verdict_reason: 'specific enough for customs classification (judged by model)',
    }),
    fieldResult({
      field_name: 'gross_weight',
      value: '862.4 KG',
      source_text: '862.4 KGS',
      verdict: 'match',
      expected: 'KG',
      rule_id: 'R-GW-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'invoice_number',
      value: 'INV-2026-0451',
      verdict: 'match',
      expected: 'matches ^INV-\\d{4}-\\d{4,}$',
      rule_id: 'R-INV-1',
      verdict_reason: 'matches',
    }),
  ],
}

// --- Run 2: one mismatch + one uncertain field, amendment requested. ----------------------
const RUN_AMENDMENT = {
  id: 'run_7b40e5',
  document_id: 'doc_02-clean-two-errors',
  filename: '02-clean-two-errors.pdf',
  customer_id: 'acme',
  customer_name: 'ACME Electronics Pte. Ltd.',
  status: 'completed',
  current_step: 'route',
  outcome: 'amendment_requested',
  reasoning:
    'The HS code on this invoice (847199) is not one of ACME\u2019s three approved codes, so it ' +
    'is a mismatch that customs would reject on filing. The gross weight field could not be ' +
    'read with enough confidence from the scan to be trusted either way. Every other field ' +
    '(consignee, ports, Incoterm, goods description and invoice number) matched cleanly. An ' +
    'amendment is requested so the shipper can correct the HS code and confirm the weight ' +
    'before this proceeds.',
  decision_source: 'llm',
  override_reason: null,
  escalation_reason: null,
  amendment_draft:
    'AMENDMENT REQUEST \u2014 Invoice INV-2026-0512\n\n' +
    'The following needs correction before this shipment can proceed:\n\n' +
    '1. HS Code: the document reads "847199", which is not one of ACME\u2019s approved codes ' +
    '(847130, 847160, 847330). Please confirm the correct HS code for these goods.\n\n' +
    '2. Gross Weight: this field could not be read with confidence from the submitted scan ' +
    '(the printed figure is smudged). Please resend a clearer copy or confirm the weight in ' +
    'writing.\n\n' +
    'All other fields (consignee, ports of loading and discharge, Incoterm, goods description ' +
    'and invoice number) matched ACME\u2019s records and need no changes.\n\n' +
    'Please resubmit a corrected invoice or reply confirming the values above.',
  llm_calls: 4,
  fallback_used: false,
  input_tokens: 4108,
  output_tokens: 890,
  cost_usd: 0.0,
  latency_ms: 6110,
  started_at: '2026-09-29T09:20:11Z',
  finished_at: '2026-09-29T09:20:18Z',
  field_results: [
    fieldResult({
      field_name: 'consignee',
      value: 'ACME Electronics Pte. Ltd.',
      verdict: 'match',
      expected: 'ACME Electronics Pte. Ltd.',
      rule_id: 'R-CON-1',
      verdict_reason: 'matches the registered name on file',
    }),
    fieldResult({
      field_name: 'hs_code',
      value: '847199',
      verdict: 'mismatch',
      found: '847199',
      expected: '847130, 847160, 847330',
      rule_id: 'R-HS-1',
      verdict_reason: 'not in the allowed list',
    }),
    fieldResult({
      field_name: 'port_of_loading',
      value: 'Yantian',
      source_text: 'YANTIAN, CHINA',
      verdict: 'match',
      expected: 'Shanghai, Yantian',
      rule_id: 'R-POL-1',
      verdict_reason: 'in list',
    }),
    fieldResult({
      field_name: 'port_of_discharge',
      value: 'Singapore',
      source_text: 'SINGAPORE',
      verdict: 'match',
      expected: 'Singapore',
      rule_id: 'R-POD-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'incoterms',
      value: 'CIF',
      source_text: 'CIF Singapore',
      verdict: 'match',
      expected: 'CIF',
      rule_id: 'R-INC-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'goods_description',
      value: 'Portable laptop computers, 14-inch',
      verdict: 'match',
      expected: 'specific enough for customs classification',
      rule_id: 'R-DESC-1',
      model_rating: 0.88,
      verdict_reason: 'specific enough for customs classification (judged by model)',
    }),
    fieldResult({
      field_name: 'gross_weight',
      value: '860 KG',
      source_text: '86_.4 KGS',
      grounding: 'near',
      extraction_confidence: 0.41,
      model_rating: 0.5,
      verdict: 'uncertain',
      found: null,
      expected: null,
      rule_id: null,
      verdict_reason: 'could not be read reliably from the scan',
      verdict_confidence: 0.41,
    }),
    fieldResult({
      field_name: 'invoice_number',
      value: 'INV-2026-0512',
      verdict: 'match',
      expected: 'matches ^INV-\\d{4}-\\d{4,}$',
      rule_id: 'R-INV-1',
      verdict_reason: 'matches',
    }),
  ],
}

// --- Run 3: router picked an outcome code overrode, human review. -------------------------
const RUN_OVERRIDE = {
  id: 'run_4d81ff',
  document_id: 'doc_03-messy-scan',
  filename: '03-messy-scan.jpg',
  customer_id: 'acme',
  customer_name: 'ACME Electronics Pte. Ltd.',
  status: 'completed',
  current_step: 'route',
  outcome: 'human_review',
  reasoning:
    'Gemini proposed auto_approve, but the Incoterm field is mismatched (the document reads ' +
    '"FOB", ACME\u2019s rule requires "CIF") and a mismatch can never be auto-approved. Code ' +
    'overrode the decision to human_review so an operator confirms the Incoterm discrepancy ' +
    'before this proceeds.',
  decision_source: 'code_override',
  override_reason:
    "outcome 'auto_approve' is not in the allowed set {amendment_request, human_review} for a " +
    'document with a mismatched field; overridden to human_review',
  escalation_reason: null,
  amendment_draft: null,
  llm_calls: 3,
  fallback_used: false,
  input_tokens: 3980,
  output_tokens: 540,
  cost_usd: 0.0,
  latency_ms: 5390,
  started_at: '2026-09-29T09:31:47Z',
  finished_at: '2026-09-29T09:31:53Z',
  field_results: [
    fieldResult({
      field_name: 'consignee',
      value: 'ACME Electronics Pte. Ltd.',
      verdict: 'match',
      expected: 'ACME Electronics Pte. Ltd.',
      rule_id: 'R-CON-1',
      verdict_reason: 'matches the registered name on file',
    }),
    fieldResult({
      field_name: 'hs_code',
      value: '847160',
      verdict: 'match',
      expected: '847130, 847160, 847330',
      rule_id: 'R-HS-1',
      verdict_reason: 'in list',
    }),
    fieldResult({
      field_name: 'port_of_loading',
      value: 'Shanghai',
      source_text: 'SHANGHAI, CHINA',
      verdict: 'match',
      expected: 'Shanghai, Yantian',
      rule_id: 'R-POL-1',
      verdict_reason: 'in list',
    }),
    fieldResult({
      field_name: 'port_of_discharge',
      value: 'Singapore',
      source_text: 'SINGAPORE',
      verdict: 'match',
      expected: 'Singapore',
      rule_id: 'R-POD-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'incoterms',
      value: 'FOB',
      verdict: 'mismatch',
      found: 'FOB',
      expected: 'CIF',
      rule_id: 'R-INC-1',
      verdict_reason: 'does not match the required Incoterm',
    }),
    fieldResult({
      field_name: 'goods_description',
      value: 'Portable laptop computers, 14-inch',
      verdict: 'match',
      expected: 'specific enough for customs classification',
      rule_id: 'R-DESC-1',
      model_rating: 0.82,
      verdict_reason: 'specific enough for customs classification (judged by model)',
    }),
    fieldResult({
      field_name: 'gross_weight',
      value: '910.0 KG',
      verdict: 'match',
      expected: 'KG',
      rule_id: 'R-GW-1',
      verdict_reason: 'matches',
    }),
    fieldResult({
      field_name: 'invoice_number',
      value: 'INV-2026-0498',
      verdict: 'match',
      expected: 'matches ^INV-\\d{4}-\\d{4,}$',
      rule_id: 'R-INV-1',
      verdict_reason: 'matches',
    }),
  ],
}

// --- Run 4: still processing, to exercise the progress steps + polling. -------------------
// `current_step` advances by one every time `mockGetRun` is called for this run, so opening
// the run view and watching it poll (design §7: "polls every second") actually shows movement
// without a backend. It settles into RUN_PROCESSING_FINAL on the last step.
const PROCESSING_STEP_ORDER = ['prepare', 'extract', 'validate', 'route']
let processingStepIndex = 0

const RUN_PROCESSING_FINAL = {
  ...RUN_CLEAN,
  id: 'run_1e77aa',
  document_id: 'doc_live-demo',
  filename: 'live-demo-invoice.pdf',
  started_at: '2026-09-29T10:02:00Z',
}

function currentProcessingRun() {
  if (processingStepIndex >= PROCESSING_STEP_ORDER.length - 1) {
    return {
      ...RUN_PROCESSING_FINAL,
      status: 'completed',
      current_step: 'route',
    }
  }
  return {
    id: 'run_1e77aa',
    document_id: 'doc_live-demo',
    filename: 'live-demo-invoice.pdf',
    customer_id: 'acme',
    customer_name: 'ACME Electronics Pte. Ltd.',
    status: 'processing',
    current_step: PROCESSING_STEP_ORDER[processingStepIndex],
    outcome: null,
    reasoning: null,
    decision_source: null,
    override_reason: null,
    escalation_reason: null,
    amendment_draft: null,
    llm_calls: processingStepIndex,
    fallback_used: false,
    input_tokens: null,
    output_tokens: null,
    cost_usd: null,
    latency_ms: null,
    started_at: '2026-09-29T10:02:00Z',
    finished_at: null,
    field_results: [],
  }
}

const RUNS_BY_ID = {
  [RUN_CLEAN.id]: RUN_CLEAN,
  [RUN_AMENDMENT.id]: RUN_AMENDMENT,
  [RUN_OVERRIDE.id]: RUN_OVERRIDE,
}

function summarize(run) {
  // What GET /api/runs (the list) needs — not the full field_results payload.
  return {
    id: run.id,
    document_id: run.document_id,
    filename: run.filename,
    customer_id: run.customer_id,
    customer_name: run.customer_name,
    status: run.status,
    current_step: run.current_step,
    outcome: run.outcome,
    started_at: run.started_at,
    finished_at: run.finished_at,
  }
}

export function mockListRuns() {
  const processing = currentProcessingRun()
  return [summarize(processing), ...Object.values(RUNS_BY_ID).map(summarize)]
}

export function mockGetRun(runId) {
  if (runId === 'run_1e77aa') {
    const run = currentProcessingRun()
    if (processingStepIndex < PROCESSING_STEP_ORDER.length - 1) {
      processingStepIndex += 1
    }
    return run
  }
  const run = RUNS_BY_ID[runId]
  if (!run) {
    throw new Error(`No mock run with id ${runId}`)
  }
  return run
}

export function mockCustomers() {
  return MOCK_CUSTOMERS
}

let mockRunCounter = 0

export function mockCreateRun({ file, customerId, rerun }) {
  // Design §6: a duplicate upload (same file hash + customer) returns the existing run
  // unless `rerun` is set. Mocked here by filename match against the fixed runs above, since
  // there's no real hashing without a backend.
  const existing = Object.values(RUNS_BY_ID).find(
    (run) => run.filename === file?.name && run.customer_id === customerId,
  )
  if (existing && !rerun) {
    return { run_id: existing.id, status: existing.status, duplicate: true }
  }
  mockRunCounter += 1
  const newId = `run_mock_${mockRunCounter}`
  RUNS_BY_ID[newId] = {
    ...RUN_CLEAN,
    id: newId,
    document_id: `doc_${newId}`,
    filename: file?.name ?? 'uploaded-file.pdf',
    customer_id: customerId,
    customer_name:
      MOCK_CUSTOMERS.find((c) => c.customer_id === customerId)?.customer_name ?? customerId,
    status: 'processing',
    current_step: 'prepare',
    outcome: null,
    started_at: new Date().toISOString(),
    finished_at: null,
  }
  return { run_id: newId, status: 'processing', duplicate: false }
}

export function mockQuery(question) {
  return {
    answer: 'Query stubbed: no backend and no query engine wired up in this mock yet.',
    explanation: `Received the question "${question}" but this is mock data only (design §5 is not implemented here).`,
    sql: null,
    columns: [],
    rows: [],
  }
}
