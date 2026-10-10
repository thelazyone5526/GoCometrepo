import { useCallback, useEffect, useState } from 'react'
import { createRun, getCustomers, listRuns, listShipments } from './api/api.js'
import AskPanel from './components/AskPanel.jsx'
import RunList from './components/RunList.jsx'
import RunView from './components/RunView.jsx'
import ShipmentList from './components/ShipmentList.jsx'
import ShipmentView from './components/ShipmentView.jsx'
import UploadPanel from './components/UploadPanel.jsx'

// The operator UI (design §7, and part2-shipment-workflow design §4). One page, three areas
// per §7: upload + history (area 1), run view (area 2), Ask (area 3, `AskPanel`) — wired to
// the real `POST /api/query` endpoint. Part 2 adds a view toggle (design §4): the Part 1
// single-document view above, and a shipment view reading the inbox-triggered pipeline this
// session adds, side by side rather than one replacing the other.
function App() {
  const [view, setView] = useState('shipments')
  const [customers, setCustomers] = useState([])
  const [runs, setRuns] = useState([])
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [shipments, setShipments] = useState([])
  const [selectedShipmentId, setSelectedShipmentId] = useState(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [loadError, setLoadError] = useState(null)

  const refreshRuns = useCallback(async () => {
    try {
      const data = await listRuns()
      setRuns(data)
    } catch (err) {
      setLoadError(err.message)
    }
  }, [])

  const refreshShipments = useCallback(async () => {
    try {
      const data = await listShipments()
      setShipments(data)
    } catch (err) {
      setLoadError(err.message)
    }
  }, [])

  useEffect(() => {
    getCustomers()
      .then(setCustomers)
      .catch((err) => setLoadError(err.message))
    refreshRuns()
    refreshShipments()
  }, [refreshRuns, refreshShipments])

  // The list itself has no push updates, so poll while any run is still processing.
  // Without this, a run's sidebar entry freezes at whatever step it was on when the
  // list was last fetched (e.g. "In progress (prepare)") even after it completes.
  useEffect(() => {
    const hasProcessingRun = runs.some((run) => run.status === 'processing')
    if (!hasProcessingRun) return
    const timer = setTimeout(refreshRuns, 2000)
    return () => clearTimeout(timer)
  }, [runs, refreshRuns])

  // Part 2 design §4 D1: poll the shipment list every 2s while anything is still
  // processing (no outcome yet and not failed), same pattern as the run list above.
  useEffect(() => {
    const hasProcessingShipment = shipments.some(
      (s) => s.review_state === 'pending_review' && s.outcome === null,
    )
    if (!hasProcessingShipment) return
    const timer = setTimeout(refreshShipments, 2000)
    return () => clearTimeout(timer)
  }, [shipments, refreshShipments])

  async function handleStartRun({ file, customerId, rerun }) {
    setIsSubmitting(true)
    try {
      const result = await createRun({ file, customerId, rerun })
      await refreshRuns()
      setSelectedRunId(result.run_id)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <main>
      <h1>Trade Document Pipeline</h1>

      <div className="view-toggle" role="group" aria-label="Choose view">
        <button
          type="button"
          aria-pressed={view === 'shipments'}
          onClick={() => setView('shipments')}
        >
          Shipments
        </button>
        <button
          type="button"
          aria-pressed={view === 'single-document'}
          onClick={() => setView('single-document')}
        >
          Single document
        </button>
      </div>

      {loadError && (
        <p className="error-text" role="alert">
          {loadError}
        </p>
      )}

      {view === 'shipments' ? (
        <div className="layout">
          <div className="layout__sidebar">
            <section className="card" aria-labelledby="shipments-heading">
              <h2 id="shipments-heading">Shipments</h2>
              <ShipmentList
                shipments={shipments}
                selectedShipmentId={selectedShipmentId}
                onSelectShipment={setSelectedShipmentId}
              />
            </section>
          </div>

          <div className="layout__main">
            {selectedShipmentId ? (
              <ShipmentView key={selectedShipmentId} shipmentId={selectedShipmentId} />
            ) : (
              <p>Select a shipment from the list.</p>
            )}
          </div>
        </div>
      ) : (
        <div className="layout">
          <div className="layout__sidebar">
            <UploadPanel
              customers={customers}
              onStartRun={handleStartRun}
              isSubmitting={isSubmitting}
            />
            <section className="card" aria-labelledby="history-heading">
              <h2 id="history-heading">Recent runs</h2>
              <RunList
                runs={runs}
                selectedRunId={selectedRunId}
                onSelectRun={setSelectedRunId}
              />
            </section>
          </div>

          <div className="layout__main">
            {selectedRunId ? (
              <RunView key={selectedRunId} runId={selectedRunId} />
            ) : (
              <p>Select a run from the list, or start a new one.</p>
            )}
            <AskPanel />
          </div>
        </div>
      )}
    </main>
  )
}

export default App
