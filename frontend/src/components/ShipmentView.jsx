import { useEffect, useState } from 'react'
import { getShipment } from '../api/api.js'
import {
  documentStatusMeta,
  reviewStateLabel,
  shipmentOutcomeLabel,
} from '../outcomes.js'
import CrossCheckTable from './CrossCheckTable.jsx'
import DiscrepancyDetail from './DiscrepancyDetail.jsx'
import DraftPanel from './DraftPanel.jsx'
import ShipmentFieldTable from './ShipmentFieldTable.jsx'

const POLL_INTERVAL_MS = 2000

function isStillProcessing(shipment) {
  return shipment.review_state === 'pending_review' && shipment.outcome === null
}

// Part 2 screen states 1 (per-shipment progress) and 2 (verification result), combined --
// which one is shown depends only on whether the shipment has an outcome yet (requirement
// 8.2, 8.3, 9.1). Polls GET /api/shipments/{id} every 2s while still processing (design §4's
// settled decision D1), stopping once an outcome exists.
function ShipmentView({ shipmentId }) {
  const [shipment, setShipment] = useState(null)
  const [error, setError] = useState(null)
  // state 3 (requirement 10): which detail, if any, is open on top of state 2. `null` means
  // state 2 is showing. Reset whenever the shipment changes (a fresh poll result still has
  // the same field shape, so this is only reset on shipmentId change via the key prop in
  // App.jsx, not on every poll).
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
        <h2>{shipment.subject || '(no subject)'}</h2>
        <p className="error-text" role="alert">
          Failed: {shipment.failure_reason}
        </p>
      </div>
    )
  }

  if (isStillProcessing(shipment)) {
    const own = shipment.documents.filter((doc) => doc.origin === 'own_email')
    const doneCount = own.filter((doc) => doc.run_status !== 'processing' && doc.run_status !== 'waiting').length
    return (
      <div className="run-view">
        <h2>{shipment.subject || '(no subject)'}</h2>
        <p role="status" aria-live="polite">
          {doneCount} of {own.length} documents done
        </p>
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

  const matchCount = shipment.field_results.filter((f) => f.verdict === 'match').length
  const mismatchCount = shipment.field_results.filter((f) => f.verdict === 'mismatch').length
  const uncertainFieldCount = shipment.field_results.filter((f) => f.verdict === 'uncertain').length
  const docTypesPresent = [
    ...new Set(
      shipment.documents
        .filter((doc) => doc.origin === 'own_email' && doc.doc_type)
        .map((doc) => doc.doc_type.replaceAll('_', ' ')),
    ),
  ]
  const carriedOver = shipment.documents.filter((doc) => doc.origin === 'carried_over')
  const runIdByDocument = new Map(shipment.documents.map((doc) => [doc.document_id, doc.run_id]))

  if (openDetail) {
    return (
      <DiscrepancyDetail item={openDetail} onClose={() => setOpenDetail(null)} />
    )
  }

  return (
    <div className="run-view">
      <h2 className="visually-hidden">Shipment detail: {shipment.subject}</h2>

      <section className="card decision-card" aria-labelledby="shipment-decision-heading">
        <h2 id="shipment-decision-heading">Decision</h2>
        <p className={`decision-outcome decision-outcome--${shipment.outcome}`}>
          {shipmentOutcomeLabel(shipment.outcome)}
        </p>
        <p className="decision-reason">{shipment.reasoning}</p>
        <dl className="decision-meta">
          <div>
            <dt>Review state</dt>
            <dd>{reviewStateLabel(shipment.review_state)}</dd>
          </div>
          <div>
            <dt>Field counts</dt>
            <dd>
              {matchCount} match · {mismatchCount} mismatch · {uncertainFieldCount} uncertain
            </dd>
          </div>
          <div>
            <dt>Cycle</dt>
            <dd>{shipment.cycle_number}</dd>
          </div>
        </dl>
        <p>
          Documents received: {docTypesPresent.length > 0 ? docTypesPresent.join(', ') : 'none'}
        </p>
        {carriedOver.length > 0 && (
          <div>
            <p>Carried over from an earlier submission:</p>
            <ul>
              {carriedOver.map((doc) => (
                <li key={doc.document_id}>
                  {doc.filename} ({doc.doc_type?.replaceAll('_', ' ') ?? 'unknown type'}) — from
                  cycle {doc.source_cycle}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {shipment.uncertain_items.length > 0 && (
        <section className="card" aria-labelledby="uncertain-heading">
          <h2 id="uncertain-heading">Needs your check</h2>
          <ul>
            {shipment.uncertain_items.map((item, index) => (
              <li key={index}>
                {item}{' '}
                <button
                  type="button"
                  onClick={() => setOpenDetail({ type: 'uncertain', text: item })}
                >
                  Why?
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card" aria-labelledby="shipment-fields-heading">
        <h2 id="shipment-fields-heading">Fields</h2>
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
      </section>

      <section className="card" aria-labelledby="cross-checks-heading">
        <h2 id="cross-checks-heading">Cross-document checks</h2>
        <CrossCheckTable
          crossChecks={shipment.cross_checks}
          onOpenCheck={(check) => setOpenDetail({ type: 'cross_check', check })}
        />
      </section>

      {shipment.attachments.some((a) => a.status !== 'accepted') && (
        <section className="card" aria-labelledby="attachments-heading">
          <h2 id="attachments-heading">Attachments</h2>
          <ul>
            {shipment.attachments.map((a, index) => (
              <li key={index}>
                {a.filename}: {a.status}
                {a.reason && ` (${a.reason})`}
              </li>
            ))}
          </ul>
        </section>
      )}

      <DraftPanel shipment={shipment} />
    </div>
  )
}

export default ShipmentView
