import { useEffect, useState } from 'react'
import { getRun } from '../api/api.js'
import DecisionCard from './DecisionCard.jsx'
import FieldTable from './FieldTable.jsx'
import ProgressSteps from './ProgressSteps.jsx'
import ReasoningAndDraft from './ReasoningAndDraft.jsx'

const POLL_INTERVAL_MS = 1000

// design §7 area 2, top-to-bottom order: progress steps, decision card, reasoning + draft,
// field table. Polls GET /api/runs/{id} every second while the run is still processing
// (design §2 diagram: "Run view (polls every second)"), and stops once it completes.
function RunView({ runId }) {
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    let timer = null

    async function poll() {
      try {
        const data = await getRun(runId)
        if (cancelled) return
        setRun(data)
        setError(null)
        if (data.status === 'processing') {
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
  }, [runId])

  if (error) {
    return (
      <p className="error-text" role="alert">
        Could not load run: {error}
      </p>
    )
  }

  if (!run) {
    return (
      <p role="status" aria-live="polite">
        Loading run…
      </p>
    )
  }

  return (
    <div className="run-view">
      <h2 className="visually-hidden">Run detail: {run.filename}</h2>
      <ProgressSteps currentStep={run.current_step} status={run.status} />
      <DecisionCard run={run} />
      <ReasoningAndDraft run={run} />
      <section className="card" aria-labelledby="fields-heading">
        <h2 id="fields-heading">Fields</h2>
        <FieldTable fieldResults={run.field_results} />
      </section>
    </div>
  )
}

export default RunView
