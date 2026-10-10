import { crossCheckMeta } from '../outcomes.js'

// Reuses the field-table's match/mismatch colour classes (verdict-cell--*) rather than
// defining a parallel set for cross-checks, since "consistent" and "inconsistent" read the
// same way to an operator as "match" and "mismatch" do.
const RESULT_TO_VERDICT_CLASS = {
  consistent: 'match',
  inconsistent: 'mismatch',
  uncertain: 'uncertain',
  not_applicable: 'not_applicable',
}

// Part 2 screen state 2 (requirement 9.3): cross-document checks, in their own section,
// separate from the per-document fields. For an `inconsistent` result, the value found in
// each document involved is shown inline (requirement 3.6, 10.3) rather than requiring a
// click into state 3 just to see what disagreed.
function CrossCheckTable({ crossChecks }) {
  if (crossChecks.length === 0) {
    return <p>No cross-document checks for this shipment.</p>
  }

  return (
    <table className="field-table">
      <caption className="visually-hidden">Cross-document checks</caption>
      <thead>
        <tr>
          <th scope="col">Field</th>
          <th scope="col">Result</th>
          <th scope="col">Values found</th>
        </tr>
      </thead>
      <tbody>
        {crossChecks.map((check) => {
          const { label, icon } = crossCheckMeta(check.result)
          return (
            <tr key={check.field_name}>
              <th scope="row">{check.field_name.replaceAll('_', ' ')}</th>
              <td
                className={`verdict-cell verdict-cell--${RESULT_TO_VERDICT_CLASS[check.result] ?? check.result}`}
              >
                <span aria-hidden="true">{icon}</span> {label}
              </td>
              <td>
                {check.compared.length === 0 ? (
                  '—'
                ) : (
                  <ul className="cross-check-values">
                    {check.compared.map((row) => (
                      <li key={row.document_id}>
                        {row.filename}: {row.value ?? '(no value)'}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default CrossCheckTable
