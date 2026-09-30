import { outcomeLabel } from '../outcomes.js'

// design §7 area 1: "a list of recent runs, each with an outcome label." A still-processing
// run has no outcome yet, so it shows its current step instead.
function RunList({ runs, selectedRunId, onSelectRun }) {
  if (runs.length === 0) {
    return <p>No runs yet. Start one above.</p>
  }

  return (
    <ul className="run-list">
      {runs.map((run) => {
        const isSelected = run.id === selectedRunId
        const statusText =
          run.status === 'processing' ? `In progress (${run.current_step})` : outcomeLabel(run.outcome)
        return (
          <li key={run.id}>
            <button
              type="button"
              className={`run-list-item${isSelected ? ' run-list-item--selected' : ''}`}
              onClick={() => onSelectRun(run.id)}
              aria-current={isSelected ? 'true' : undefined}
            >
              <span className="run-list-item__filename">{run.filename}</span>
              <span className="run-list-item__customer">{run.customer_name}</span>
              <span
                className={`outcome-badge outcome-badge--${run.status === 'processing' ? 'processing' : run.outcome}`}
              >
                {statusText}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export default RunList
