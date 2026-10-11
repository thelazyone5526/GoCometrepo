// The whole journey of one email, left to right (D10 in docs/part2-decisions.md):
// email received -> every document checked by Part 1's pipeline -> cross-check across the
// documents -> shipment decision -> reply drafted.
//
// The last three run in code right after the final document finishes (they are triggered when
// the shipment is next read, D7), so in practice they tick over almost at once rather than
// showing a visible wait. Each step is shown with a word and a symbol, never colour alone.
function ShipmentSteps({ documentsDone, documentsTotal, decided }) {
  const allDocumentsDone = documentsTotal > 0 && documentsDone >= documentsTotal

  // Index of the step that is running now; steps before it are done, steps after are waiting.
  let current
  if (decided) {
    current = 5 // everything done
  } else if (!allDocumentsDone) {
    current = 1
  } else {
    current = 2
  }

  const steps = [
    'Email received',
    `Documents checked (${documentsDone} of ${documentsTotal})`,
    'Cross-check documents',
    'Decide outcome',
    'Reply drafted',
  ]

  return (
    <ol className="progress-steps progress-steps--shipment" aria-label="Shipment progress">
      {steps.map((label, index) => {
        let state = 'pending'
        if (index < current) state = 'done'
        else if (index === current) state = 'current'
        return (
          <li
            key={label}
            className={`progress-step progress-step--${state}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <span className="progress-step__marker" aria-hidden="true">
              {state === 'done' ? '✓' : index + 1}
            </span>
            <span className="progress-step__label">
              {label}
              {state === 'current' && ' (in progress)'}
              {state === 'done' && ' (done)'}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export default ShipmentSteps
