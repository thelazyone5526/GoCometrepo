import { useEffect, useState } from 'react'
import { getRunLlmCalls } from '../api/api.js'

const POLL_INTERVAL_MS = 1500

// Evidence behind the metrics strip on the decision card: one row per real LLM call attempt
// (agent, model, whether it was a retry or a fallback-model call, tokens, latency, status).
// Collapsed by default so it doesn't compete with the decision/reasoning above it — expand
// it to see, e.g., a failed primary-model attempt followed by a successful fallback call.
//
// `isRunInProgress` makes this poll every 1.5s while the run is still going, since each call
// attempt is now written to `llm_calls` the moment it happens (see `graph.build.
// _live_recorder`) rather than only once the whole run finishes -- so a retry loop or a
// fallback-model wait is visible live, not just in hindsight.
function CallLogTable({ runId, isRunInProgress }) {
  const [calls, setCalls] = useState(null)
  const [error, setError] = useState(null)
  // Starts expanded for an in-progress run (there's something worth watching live), and
  // collapsed for an already-finished one (the decision card above already says what
  // happened; this is supporting evidence, not the headline).
  const [open, setOpen] = useState(isRunInProgress)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    let timer = null

    async function poll() {
      try {
        const data = await getRunLlmCalls(runId)
        if (cancelled) return
        setCalls(data)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err.message)
      }
      if (!cancelled && isRunInProgress) {
        timer = setTimeout(poll, POLL_INTERVAL_MS)
      }
    }

    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [open, runId, isRunInProgress])

  return (
    <section className="card">
      <details open={open} onToggle={(e) => setOpen(e.target.open)}>
        <summary className="call-log__summary">
          LLM call log{isRunInProgress ? ' (live)' : ''}
        </summary>
        {error && (
          <p className="error-text" role="alert">
            Could not load call log: {error}
          </p>
        )}
        {open && !error && calls === null && <p role="status">Loading call log…</p>}
        {open && !error && calls !== null && calls.length === 0 && (
          <p role="status">
            {isRunInProgress
              ? 'No LLM calls yet — waiting on the first one to complete…'
              : 'No LLM calls recorded for this run.'}
          </p>
        )}
        {calls !== null && calls.length > 0 && (
          <table className="call-log-table">
            <caption className="visually-hidden">
              Every LLM call attempt made during this run, in order
            </caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Agent</th>
                <th scope="col">Model</th>
                <th scope="col">Attempt</th>
                <th scope="col">Status</th>
                <th scope="col">Tokens (in / out)</th>
                <th scope="col">Latency</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call, index) => (
                <tr key={`${call.created_at}-${index}`}>
                  <th scope="row">{index + 1}</th>
                  <td>{call.agent}</td>
                  <td>
                    {call.model}
                    {call.is_fallback && <span className="fallback-tag"> (fallback)</span>}
                  </td>
                  <td>{call.attempt}</td>
                  <td className={`call-log-status--${call.status}`}>{call.status}</td>
                  <td>
                    {call.input_tokens.toLocaleString()} / {call.output_tokens.toLocaleString()}
                  </td>
                  <td>{(call.latency_ms / 1000).toFixed(2)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </section>
  )
}

export default CallLogTable
