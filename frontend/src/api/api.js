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

// --- Part 2: shipments (design §2, requirements 8-11) -------------------------------------
// No mock branch: Part 2 was built against the real backend from the start, so there is no
// `mockData.js` equivalent to keep in sync, unlike the Part 1 functions above.

/** GET /api/shipments — state 1's list. */
export async function listShipments() {
  return asJson(await fetch('/api/shipments'))
}

/** GET /api/shipments/{id} — states 2 and 3's payload in one call. */
export async function getShipment(shipmentId) {
  return asJson(await fetch(`/api/shipments/${encodeURIComponent(shipmentId)}`))
}

/** GET /api/shipments/{id}/draft */
export async function getDraft(shipmentId) {
  return asJson(await fetch(`/api/shipments/${encodeURIComponent(shipmentId)}/draft`))
}

/** PUT /api/shipments/{id}/draft — store an operator edit as a new version. */
export async function putDraft(shipmentId, { recipient, subject, body }) {
  return asJson(
    await fetch(`/api/shipments/${encodeURIComponent(shipmentId)}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipient, subject, body }),
    }),
  )
}

/** POST /api/shipments/{id}/send — the operator's Send. */
export async function sendDraft(shipmentId, { confirmed = false } = {}) {
  return asJson(
    await fetch(`/api/shipments/${encodeURIComponent(shipmentId)}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed }),
    }),
  )
}

/** POST /api/shipments/{id}/reviewed — reviewed without sending. */
export async function markReviewedNoSend(shipmentId) {
  return asJson(
    await fetch(`/api/shipments/${encodeURIComponent(shipmentId)}/reviewed`, {
      method: 'POST',
    }),
  )
}

/** POST /api/shipments/{id}/documents/{document_id}/rerun */
export async function rerunDocument(shipmentId, documentId) {
  return asJson(
    await fetch(
      `/api/shipments/${encodeURIComponent(shipmentId)}/documents/${encodeURIComponent(documentId)}/rerun`,
      { method: 'POST' },
    ),
  )
}

/**
 * The URL for a page image (requirement 10.2): `GET /api/runs/{run_id}/pages/{n}`, `n`
 * 0-based. Reused as-is from Part 1 (design §2: "no new image route"); a `page` field on a
 * field result is 1-based, so the caller passes `page - 1`.
 */
export function pageImageUrl(runId, pageIndex) {
  return `/api/runs/${encodeURIComponent(runId)}/pages/${pageIndex}`
}

// --- Demo only (D9 in docs/part2-decisions.md) ---------------------------------------------

/**
 * POST /api/demo/incoming-email — drops a fresh supplier email into the watched inbox folder,
 * standing in for a real email arriving. `kind` is "clean" or "messy". The watcher does the rest.
 */
export async function simulateIncomingEmail(kind) {
  return asJson(
    await fetch('/api/demo/incoming-email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind }),
    }),
  )
}
