import { useCallback, useEffect, useState } from 'react'
import { createRun, getCustomers, listRuns } from './api/api.js'
import RunList from './components/RunList.jsx'
import RunView from './components/RunView.jsx'
import UploadPanel from './components/UploadPanel.jsx'

// The operator UI (design §7), replacing the Phase 1 health-check placeholder. One page,
// three areas per §7: upload + history (area 1), run view (area 2). The third area, Ask
// (§7 area 3), is out of scope for this pass — see api.js's `postQuery`, which is stubbed
// but not wired into any component.
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
        </div>
      </div>
    </main>
  )
}

export default App
