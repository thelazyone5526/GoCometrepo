import { reviewStateLabel, shipmentOutcomeLabel } from '../outcomes.js'

// Part 2 screen state 1 (requirements 8.1-8.4): incoming and processing. Each row is one
// email/shipment: sender, subject, received time, attachment count, a "N of M documents
// done" progress count while anything is still processing, replaced by the outcome once
// decided. A failed shipment (unknown customer, no attachments, or a processing error)
// still shows up, with its reason, rather than disappearing (requirement 13.1).
function ShipmentList({ shipments, selectedShipmentId, onSelectShipment }) {
  if (shipments.length === 0) {
    return <p>No shipments yet. Drop a sample email into the inbox folder to see one appear.</p>
  }

  return (
    <ul className="run-list">
      {shipments.map((shipment) => {
        const isSelected = shipment.shipment_id === selectedShipmentId
        const isProcessing =
          shipment.review_state === 'pending_review' && shipment.outcome === null
        const isFailed = shipment.review_state === 'failed'

        let statusText
        let badgeClass
        if (isFailed) {
          statusText = reviewStateLabel('failed')
          badgeClass = 'failed'
        } else if (isProcessing) {
          statusText = `${shipment.documents_done} of ${shipment.documents_total} documents done`
          badgeClass = 'processing'
        } else {
          statusText = shipmentOutcomeLabel(shipment.outcome)
          badgeClass = shipment.outcome
        }

        return (
          <li key={shipment.shipment_id}>
            <button
              type="button"
              className={`run-list-item${isSelected ? ' run-list-item--selected' : ''}`}
              onClick={() => onSelectShipment(shipment.shipment_id)}
              aria-current={isSelected ? 'true' : undefined}
            >
              <span className="run-list-item__filename">
                {shipment.subject || '(no subject)'}
              </span>
              <span className="run-list-item__customer">
                {shipment.sender || 'unknown sender'} · {shipment.customer_name}
                {shipment.reference && ` · ${shipment.reference} (cycle ${shipment.cycle_number})`}
              </span>
              <span className={`outcome-badge outcome-badge--${badgeClass}`}>{statusText}</span>
              {isFailed && shipment.failure_reason && (
                <span className="run-list-item__reason">{shipment.failure_reason}</span>
              )}
              {!isFailed && !isProcessing && (
                <span className="run-list-item__counts">
                  {shipment.discrepancy_count} discrepancies · {shipment.uncertain_count} uncertain
                </span>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export default ShipmentList
