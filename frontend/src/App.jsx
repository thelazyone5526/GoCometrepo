import { useCallback, useEffect, useState } from 'react'
import { createRun, getCustomers, listRuns } from './api/api.js'
import AskPanel from './components/AskPanel.jsx'
import RunList from './components/RunList.jsx'
import RunView from './components/RunView.jsx'
import UploadPanel from './components/UploadPanel.jsx'

// The operator UI (design §7). One page, three areas per §7: upload + history (area 1),
// run view (area 2), Ask (area 3, `AskPanel`) — wired to the real `POST /api/query`
// endpoint, which was already built and tested (Phase 11) but had no UI caller until now.
function App() {
  const [customers, setCustomers] = useState([])
  const [runs, setRuns] = useState([])
  const [selectedRunId, setSelectedRunId] = useState(null)
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

  useEffect(() => {
    getCustomers()
      .then(setCustomers)
      .catch((err) => setLoadError(err.message))
    refreshRuns()
  }, [refreshRuns])

  // The list itself has no push updates, so poll while any run is still processing.
  // Without this, a run's sidebar entry freezes at whatever step it was on when the
  // list was last fetched (e.g. "In progress (prepare)") even after it completes.
  useEffect(() => {
    const hasProcessingRun = runs.some((run) => run.status === 'processing')
    if (!hasProcessingRun) return
    const timer = setTimeout(refreshRuns, 2000)
    return () => clearTimeout(timer)
  }, [runs, refreshRuns])

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

      {loadError && (
        <p className="error-text" role="alert">
          {loadError}
        </p>
      )}

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
    </main>
  )
}

export default App
