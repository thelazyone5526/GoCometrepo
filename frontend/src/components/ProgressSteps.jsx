import { STEP_ORDER } from '../outcomes.js'

const STEP_LABELS = {
  prepare: 'Prepare',
  extract: 'Extract',
  validate: 'Validate',
  route: 'Route',
}

// design §7 area 2: "progress steps (prepare -> extract -> validate -> route) while the run
// is going." A completed run shows every step as done; a processing run highlights the
// current one and marks earlier ones done.
function ProgressSteps({ currentStep, status }) {
  const currentIndex = STEP_ORDER.indexOf(currentStep)

  return (
    <ol className="progress-steps" aria-label="Pipeline progress">
      {STEP_ORDER.map((step, index) => {
        let state = 'pending'
        if (status === 'completed' || index < currentIndex) {
          state = 'done'
        } else if (index === currentIndex) {
          state = 'current'
        }
        return (
          <li
            key={step}
            className={`progress-step progress-step--${state}`}
            aria-current={state === 'current' ? 'step' : undefined}
          >
            <span className="progress-step__marker" aria-hidden="true">
              {state === 'done' ? '✓' : index + 1}
            </span>
            <span className="progress-step__label">
              {STEP_LABELS[step]}
              {state === 'current' && ' (in progress)'}
              {state === 'done' && ' (done)'}
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export default ProgressSteps
