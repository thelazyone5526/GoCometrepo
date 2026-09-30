import { useState } from 'react'

// design §7 area 2: "full reasoning, then the amendment draft (read-only, with a copy
// button)." The assignment narrows the draft to only show when outcome is
// amendment_requested.
function ReasoningAndDraft({ run }) {
  const [copyStatus, setCopyStatus] = useState('idle')

  if (!run.reasoning) return null

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(run.amendment_draft ?? '')
      setCopyStatus('copied')
      setTimeout(() => setCopyStatus('idle'), 2000)
    } catch {
      setCopyStatus('error')
    }
  }

  return (
    <section className="card" aria-labelledby="reasoning-heading">
      <h2 id="reasoning-heading">Reasoning</h2>
      <p className="reasoning-text">{run.reasoning}</p>

      {run.outcome === 'amendment_requested' && run.amendment_draft && (
        <div className="amendment-draft">
          <div className="amendment-draft__header">
            <h3>Amendment draft</h3>
            <button type="button" onClick={handleCopy}>
              {copyStatus === 'copied' ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <pre
            className="amendment-draft__text"
            aria-label="Amendment draft, read only"
            tabIndex={0}
          >
            {run.amendment_draft}
          </pre>
          {copyStatus === 'error' && (
            <p className="error-text" role="alert">
              Could not copy to clipboard.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

export default ReasoningAndDraft
