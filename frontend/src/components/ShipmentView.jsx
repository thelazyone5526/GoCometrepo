import { useEffect, useState } from 'react'
import { getShipment } from '../api/api.js'
import { documentStatusMeta, reviewStateLabel, shipmentOutcomeLabel } from '../outcomes.js'
import CrossCheckTable from './CrossCheckTable.jsx'
import DiscrepancyDetail from './DiscrepancyDetail.jsx'
import DraftPanel from './DraftPanel.jsx'
import ShipmentFieldTable from './ShipmentFieldTable.jsx'

const POLL_INTERVAL_MS = 2000

// One plain-language line per outcome telling the operator what it means and what to do next,
// so the verification screen answers "what now?" without the operator having to read any
// table (the assignment's "usable in 10 seconds" bar).
const OUTCOME_GUIDANCE = {
  auto_approve:
    'Every check passed. A draft approval is ready below — review it and send it when you’re happy.',
  amendment_request:
    'The supplier needs to correct the problems listed below. A draft amendment is ready — review and send it.',
  human_review:
    'This shipment needs a closer look before anything is sent. The flagged items are listed below.',
}

function isStillProcessing(shipment) {
  return shipment.review_state === 'pending_review' && shipment.outcome === null
}

// Build the plain-language problem list the operator actually acts on: every mismatched field
// (named with found vs expected) and every cross-document inconsistency. This is the same set
// the decision and the draft are built from, surfaced at the top so "what's wrong?" is
// readable before any table (requirement 9.1, 9.4).
function buildProblems(shipment) {
  const problems = []
  for (const f of shipment.field_results) {
    if (f.verdict === 'mismatch') {
      problems.push({
        key: `field-${f.document_id}-${f.field_name}`,
        title: `${f.field_name.replaceAll('_', ' ')} — ${f.filename}`,
        detail: `found “${f.found ?? f.value ?? '—'}”, expected “${f.expected ?? '—'}”`,
        openItem: { type: 'field', field: f, runId: null },
        documentId: f.document_id,
      })
    }
  }
  for (const c of shipment.cross_checks) {
    if (c.result === 'inconsistent') {
      problems.push({
        key: `cross-${c.field_name}`,
        title: `${c.field_name.replaceAll('_', ' ')} differs between documents`,
        detail: c.compared.map((r) => `${r.filename}: “${r.value ?? '(none)'}”`).join('   ·   '),
        openItem: { type: 'cross_check', check: c },
        documentId: null,
      })
    }
  }
  return problems
}

// Part 2 screen states 1 (per-shipment progress) and 2 (verification result), combined --
// which one is shown depends only on whether the shipment has an outcome yet. Polls
// GET /api/shipments/{id} every 2s while still processing (design §4's decision D1).
function ShipmentView({ shipmentId }) {
  const [shipment, setShipment] = useState(null)
  const [error, setError] = useState(null)
  const [openDetail, setOpenDetail] = useState(null)

  useEffect(() => {
    let cancelled = false
    let timer = null

    async function poll() {
      try {
        const data = await getShipment(shipmentId)
        if (cancelled) return
        setShipment(data)
        setError(null)
        if (isStillProcessing(data)) {
          timer = setTimeout(poll, POLL_INTERVAL_MS)
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
    }

    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [shipmentId])

  if (error) {
    return (
      <p className="error-text" role="alert">
        Could not load shipment: {error}
      </p>
    )
  }

  if (!shipment) {
    return (
      <p role="status" aria-live="polite">
        Loading shipment…
      </p>
    )
  }

  if (shipment.review_state === 'failed') {
    return (
      <div className="run-view">
        <section className="status-banner status-banner--failed">
          <p className="status-banner__eyebrow">{shipment.subject || '(no subject)'}</p>
          <h2 className="status-banner__outcome">Could not process this email</h2>
          <p className="status-banner__guidance">{shipment.failure_reason}</p>
        </section>
      </div>
    )
  }

  if (isStillProcessing(shipment)) {
    const own = shipment.documents.filter((doc) => doc.origin === 'own_email')
    const doneCount = own.filter(
      (doc) => doc.run_status !== 'processing' && doc.run_status !== 'waiting',
    ).length
    return (
      <div className="run-view">
        <section className="status-banner status-banner--processing">
          <p className="status-banner__eyebrow">{shipment.subject || '(no subject)'}</p>
          <h2 className="status-banner__outcome">Checking documents…</h2>
          <p className="status-banner__guidance" role="status" aria-live="polite">
            {doneCount} of {own.length} documents done
          </p>
        </section>
        <ul className="document-progress-list">
          {own.map((doc) => {
            const { label, icon } = documentStatusMeta(doc)
            return (
              <li key={doc.document_id}>
                <span aria-hidden="true">{icon}</span> {doc.filename}: {label}
              </li>
            )
          })}
        </ul>
      </div>
    )
  }

  // --- state 2: verification result ---------------------------------------------------

  if (openDetail) {
    return <DiscrepancyDetail item={openDetail} onClose={() => setOpenDetail(null)} />
  }

  const runIdByDocument = new Map(shipment.documents.map((doc) => [doc.document_id, doc.run_id]))
  const matchCount = shipment.field_results.filter((f) => f.verdict === 'match').length
  const docTypesPresent = [
    ...new Set(
      shipment.documents
        .filter((doc) => doc.origin === 'own_email' && doc.doc_type)
        .map((doc) => doc.doc_type.replaceAll('_', ' ')),
    ),
  ]
  const carriedOver = shipment.documents.filter((doc) => doc.origin === 'carried_over')
  const problems = buildProblems(shipment)

  function openProblem(problem) {
    const item = problem.openItem
    if (item.type === 'field') {
      setOpenDetail({ ...item, runId: runIdByDocument.get(problem.documentId) ?? null })
    } else {
      setOpenDetail(item)
    }
  }

  return (
    <div className="run-view">
      <h2 className="visually-hidden">Shipment detail: {shipment.subject}</h2>

      <section
        className={`status-banner status-banner--${shipment.outcome}`}
        aria-labelledby="shipment-decision-heading"
      >
        <p className="status-banner__eyebrow">
          {shipment.subject}
          {shipment.reference && ` · ${shipment.reference}`}
          {shipment.cycle_number > 1 && ` · submission round ${shipment.cycle_number}`}
        </p>
        <h2 id="shipment-decision-heading" className="status-banner__outcome">
          {shipmentOutcomeLabel(shipment.outcome)}
        </h2>
        <p className="status-banner__guidance">
          {OUTCOME_GUIDANCE[shipment.outcome] ?? shipment.reasoning}
        </p>
        <p className="status-banner__counts">
          <span className="count-chip count-chip--problem">
            {shipment.discrepancy_count} to fix
          </span>
          <span className="count-chip count-chip--uncertain">
            {shipment.uncertain_count} to check
          </span>
          <span className="count-chip count-chip--ok">{matchCount} matched</span>
          <span className="count-chip">
            {docTypesPresent.length} document{docTypesPresent.length === 1 ? '' : 's'}
          </span>
        </p>
      </section>

      {problems.length > 0 && (
        <section className="card problems-card" aria-labelledby="problems-heading">
          <h2 id="problems-heading" className="problems-card__heading">
            Problems to fix ({problems.length})
          </h2>
          <ul className="problems-list">
            {problems.map((p) => (
              <li key={p.key}>
                <button type="button" className="problem-item" onClick={() => openProblem(p)}>
                  <span className="problem-item__title">
                    <span aria-hidden="true">✕</span> {p.title}
                  </span>
                  <span className="problem-item__detail">{p.detail}</span>
                  <span className="problem-item__cue" aria-hidden="true">
                    View where on the page ›
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {shipment.uncertain_items.length > 0 && (
        <section className="card needs-check-card" aria-labelledby="uncertain-heading">
          <h2 id="uncertain-heading">Needs your check ({shipment.uncertain_items.length})</h2>
          <p className="needs-check-card__note">
            The agent wasn’t sure about these. They aren’t included in the reply to the supplier.
          </p>
          <ul>
            {shipment.uncertain_items.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      )}

      <DraftPanel shipment={shipment} />

      <details className="card details-card">
        <summary className="details-card__summary">Full field-by-field breakdown</summary>
        <p className="details-card__hint">
          Documents received: {docTypesPresent.length > 0 ? docTypesPresent.join(', ') : 'none'}
          {' · '}status: {reviewStateLabel(shipment.review_state)}
        </p>
        {carriedOver.length > 0 && (
          <p className="details-card__hint">
            Carried over from an earlier submission:{' '}
            {carriedOver
              .map(
                (doc) =>
                  `${doc.filename} (${doc.doc_type?.replaceAll('_', ' ') ?? 'unknown type'}, from round ${doc.source_cycle})`,
              )
              .join('; ')}
          </p>
        )}

        <h3>Every field, every document</h3>
        <ShipmentFieldTable
          fieldResults={shipment.field_results}
          onOpenField={(field) =>
            setOpenDetail({
              type: 'field',
              field,
              runId: runIdByDocument.get(field.document_id) ?? null,
            })
          }
        />

        <h3>Consistency across documents</h3>
        <CrossCheckTable
          crossChecks={shipment.cross_checks}
          onOpenCheck={(check) => setOpenDetail({ type: 'cross_check', check })}
        />

        {shipment.attachments.some((a) => a.status !== 'accepted') && (
          <>
            <h3>Attachments not processed</h3>
            <ul>
              {shipment.attachments
                .filter((a) => a.status !== 'accepted')
                .map((a, index) => (
                  <li key={index}>
                    {a.filename}: {a.status}
                    {a.reason && ` (${a.reason})`}
                  </li>
                ))}
            </ul>
          </>
        )}
      </details>
    </div>
  )
}

export default ShipmentView
