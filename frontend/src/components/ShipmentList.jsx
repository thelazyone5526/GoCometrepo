import { reviewStateLabel, shipmentOutcomeLabel } from '../outcomes.js'

// Part 2 screen state 1 (requirements 8.1-8.4): incoming and processing. One row per email:
// subject, who sent it, a status badge (progress while running, outcome once decided), and a
// small "N to fix" chip only when there's actually something to fix. A failed shipment still
// shows, with its reason (requirement 13.1). Kept deliberately spare so the list scans fast.
function ShipmentList({ shipments, selectedShipmentId, onSelectShipment }) {
  if (shipments.length === 0) {
    return <p>No shipments yet. Drop an email into the inbox folder to see one appear.</p>
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
          statusText = `${shipment.documents_done} of ${shipment.documents_total} done`
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
                {shipment.customer_name}
                {shipment.reference && ` · ${shipment.reference}`}
              </span>
              <span className={`outcome-badge outcome-badge--${badgeClass}`}>{statusText}</span>
              {isFailed && shipment.failure_reason && (
                <span className="run-list-item__reason">{shipment.failure_reason}</span>
              )}
              {!isFailed && !isProcessing && shipment.discrepancy_count > 0 && (
                <span className="run-list-item__reason">
                  {shipment.discrepancy_count} to fix
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
