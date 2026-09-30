import { useEffect, useState } from 'react'
import { getRunLlmCalls } from '../api/api.js'

// Evidence behind the metrics strip on the decision card: one row per real LLM call attempt
// (agent, model, whether it was a retry or a fallback-model call, tokens, latency, status).
// Collapsed by default so it doesn't compete with the decision/reasoning above it — expand
// it to see, e.g., a failed primary-model attempt followed by a successful fallback call.
function CallLogTable({ runId }) {
  const [calls, setCalls] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open || calls !== null) return
    let cancelled = false
    getRunLlmCalls(runId)
      .then((data) => {
        if (!cancelled) setCalls(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [open, runId, calls])

  return (
    <section className="card">
      <details onToggle={(e) => setOpen(e.target.open)}>
        <summary className="call-log__summary">LLM call log</summary>
        {error && (
          <p className="error-text" role="alert">
            Could not load call log: {error}
          </p>
        )}
        {open && !error && calls === null && <p role="status">Loading call log…</p>}
        {calls !== null && calls.length === 0 && <p>No LLM calls recorded for this run.</p>}
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
