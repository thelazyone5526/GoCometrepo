/**
 * One function per backend endpoint (design §6). Every function's real implementation is a
 * one-line `fetch` call behind the `USE_MOCK` flag below — when the backend exists, delete
 * the mock branch (and `./mockData.js`) and each function becomes just its `fetch` line.
 *
 * Flip this to `false` once `backend/app/api/` actually serves these routes.
 */
const USE_MOCK = false

import {
  mockCreateRun,
  mockCustomers,
  mockGetRun,
  mockListRuns,
  mockQuery,
} from './mockData.js'

async function asJson(response) {
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body?.detail ?? JSON.stringify(body)
    } catch {
      detail = response.statusText
    }
    throw new Error(`HTTP ${response.status}: ${detail}`)
  }
  return response.json()
}

/** GET /api/health */
export async function getHealth() {
  if (USE_MOCK) return { status: 'ok' }
  return asJson(await fetch('/api/health'))
}

/** GET /api/customers */
export async function getCustomers() {
  if (USE_MOCK) return mockCustomers()
  return asJson(await fetch('/api/customers'))
}

/**
 * POST /api/runs (file, customer_id, rerun?) -> 202 with run_id, or the existing run for a
 * duplicate upload (design §6, §3.7).
 */
export async function createRun({ file, customerId, rerun = false }) {
  if (USE_MOCK) return mockCreateRun({ file, customerId, rerun })
  const formData = new FormData()
  formData.append('file', file)
  formData.append('customer_id', customerId)
  if (rerun) formData.append('rerun', 'true')
  return asJson(await fetch('/api/runs', { method: 'POST', body: formData }))
}

/** GET /api/runs — recent runs for the history list. */
export async function listRuns() {
  if (USE_MOCK) return mockListRuns()
  return asJson(await fetch('/api/runs'))
}

/** GET /api/runs/{id} — full run: status, current step, fields, validation, decision, metrics. */
export async function getRun(runId) {
  if (USE_MOCK) return mockGetRun(runId)
  return asJson(await fetch(`/api/runs/${encodeURIComponent(runId)}`))
}

/**
 * GET /api/runs/{id}/llm-calls — every logged LLM call attempt for this run (agent, model,
 * fallback/retry, tokens, latency). Backs the call-log table in the run view; not part of
 * `getRun()`'s own response since a run can have many calls and the summary view doesn't
 * need them.
 */
export async function getRunLlmCalls(runId) {
  if (USE_MOCK) return []
  return asJson(await fetch(`/api/runs/${encodeURIComponent(runId)}/llm-calls`))
}

/**
 * POST /api/query (question) — the plain-English "ask a question" endpoint (design §5),
 * called by `AskPanel`. Each real call spends one Gemini call (question -> SQL), so this
 * only fires when the operator actually asks something, never on page load.
 */
export async function postQuery(question) {
  if (USE_MOCK) return mockQuery(question)
  return asJson(
    await fetch('/api/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }),
  )
}
