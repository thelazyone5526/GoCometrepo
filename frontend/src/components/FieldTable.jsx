import { verdictMeta } from '../outcomes.js'

// design §7 area 2 field table + implementation-plan.md Phase 10 ("the field table: both
// confidences, verdict, found vs expected, and rule ID" — its own verify checklist repeats
// "both confidences are visible"). Semantic <table> markup (design §7 accessibility:
// "semantic tables"); verdict is shown with both text and an icon, never colour alone
// (design §7 accessibility + the assignment's explicit callout that this is a real
// requirement).
function FieldTable({ fieldResults }) {
  if (fieldResults.length === 0) {
    return <p>No field results yet.</p>
  }

  return (
    <table className="field-table">
      <caption className="visually-hidden">Extracted fields and their validation verdicts</caption>
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Value</th>
          <th scope="col">Extraction confidence</th>
          <th scope="col">Verdict confidence</th>
          <th scope="col">Verdict</th>
          <th scope="col">Found</th>
          <th scope="col">Expected</th>
          <th scope="col">Rule ID</th>
        </tr>
      </thead>
      <tbody>
        {fieldResults.map((field) => {
          const { label, icon } = verdictMeta(field.verdict)
          return (
            <tr key={field.field_name}>
              <th scope="row">{field.field_name.replaceAll('_', ' ')}</th>
              <td>{field.value ?? '—'}</td>
              <td>{field.extraction_confidence.toFixed(2)}</td>
              <td>{field.verdict_confidence.toFixed(2)}</td>
              <td className={`verdict-cell verdict-cell--${field.verdict}`}>
                <span aria-hidden="true">{icon}</span> {label}
              </td>
              <td>{field.found ?? '—'}</td>
              <td>{field.expected ?? '—'}</td>
              <td>{field.rule_id ?? '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default FieldTable
