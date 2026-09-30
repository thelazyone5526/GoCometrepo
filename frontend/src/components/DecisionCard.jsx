import { decisionSourceLabel, outcomeLabel } from '../outcomes.js'

function countVerdicts(fieldResults) {
  const counts = { match: 0, mismatch: 0, uncertain: 0 }
  for (const field of fieldResults) {
    if (field.verdict in counts) counts[field.verdict] += 1
  }
  return counts
}

function oneLineReason(reasoning) {
  if (!reasoning) return ''
  const firstSentence = reasoning.split(/(?<=[.!?])\s+/)[0]
  return firstSentence
}

// design §7 area 2 decision card: "outcome, a one-line reason, decision source, any code
// override, and counts (e.g. '6 match · 1 mismatch · 1 uncertain')." Full reasoning text is
// rendered separately by RunView, right below this card, per the assignment's ordering.
function DecisionCard({ run }) {
  if (!run.outcome) {
    return (
      <section className="card decision-card" aria-labelledby="decision-heading">
        <h2 id="decision-heading">Decision</h2>
        <p>Run still in progress — no decision yet.</p>
      </section>
    )
  }

  const counts = countVerdicts(run.field_results)

  return (
    <section className="card decision-card" aria-labelledby="decision-heading">
      <h2 id="decision-heading">Decision</h2>
      <p className={`decision-outcome decision-outcome--${run.outcome}`}>
        {outcomeLabel(run.outcome)}
      </p>
      <p className="decision-reason">{oneLineReason(run.reasoning)}</p>
      <dl className="decision-meta">
        <div>
          <dt>Decision source</dt>
          <dd>{decisionSourceLabel(run.decision_source)}</dd>
        </div>
        <div>
          <dt>Field counts</dt>
          <dd>
            {counts.match} match · {counts.mismatch} mismatch · {counts.uncertain} uncertain
          </dd>
        </div>
      </dl>
      {run.decision_source === 'code_override' && (
        <p className="override-label">
          <strong>Overridden by code.</strong> {run.override_reason}
        </p>
      )}
      <dl className="run-metrics" aria-label="LLM usage for this run">
        <div className="run-metrics__item">
          <dt>LLM calls</dt>
          <dd>{run.llm_calls}</dd>
        </div>
        <div className="run-metrics__item">
          <dt>Tokens (in / out)</dt>
          <dd>
            {run.input_tokens.toLocaleString()} / {run.output_tokens.toLocaleString()}
          </dd>
        </div>
        <div className="run-metrics__item">
          <dt>Latency</dt>
          <dd>{(run.latency_ms / 1000).toFixed(1)}s</dd>
        </div>
        <div className="run-metrics__item">
          <dt>Fallback model used</dt>
          <dd>{run.fallback_used ? 'Yes' : 'No'}</dd>
        </div>
      </dl>
    </section>
  )
}

export default DecisionCard
