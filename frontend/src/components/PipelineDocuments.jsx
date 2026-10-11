import ProgressSteps from './ProgressSteps.jsx'

// One row per document in the shipment, showing where Part 1's pipeline (prepare -> extract ->
// validate -> route) has got to for that document, plus a link into Part 1's own run screen
// (steps, reasoning, field table, LLM call log) once the document has a run (D10). This is what
// shows that Part 1's agents are the engine running inside the Part 2 workflow.
function PipelineDocuments({ documents, onOpenRun }) {
  if (documents.length === 0) return null

  return (
    <ul className="pipeline-docs">
      {documents.map((doc) => {
        const failed = Boolean(doc.run_escalation_reason)
        // "persist" is the last step; a run can be on it for a moment before its status flips.
        const finished = doc.run_status === 'completed' || doc.run_current_step === 'persist'
        const typeLabel = doc.doc_type ? doc.doc_type.replaceAll('_', ' ') : null

        return (
          <li key={doc.document_id} className="pipeline-docs__item">
            <div className="pipeline-docs__head">
              <span className="pipeline-docs__name">{doc.filename}</span>
              {typeLabel && <span className="pipeline-docs__type">{typeLabel}</span>}
              {doc.origin === 'carried_over' && (
                <span className="pipeline-docs__type">from round {doc.source_cycle}</span>
              )}
            </div>

            {failed ? (
              <p className="error-text" role="alert">
                Failed: {doc.run_escalation_reason}
              </p>
            ) : (
              <ProgressSteps
                currentStep={doc.run_current_step ?? undefined}
                status={finished ? 'completed' : (doc.run_status ?? undefined)}
              />
            )}

            {doc.run_id && onOpenRun && (
              <button type="button" className="link-button" onClick={() => onOpenRun(doc.run_id)}>
                Open pipeline run ›
              </button>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export default PipelineDocuments
