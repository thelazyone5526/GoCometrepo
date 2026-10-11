import { fieldLabel, verdictMeta } from '../outcomes.js'

const ORDER = { mismatch: 0, uncertain: 1, match: 2, not_applicable: 3 }

function sortByProblemsFirst(fields) {
  return [...fields].sort((a, b) => (ORDER[a.verdict] ?? 4) - (ORDER[b.verdict] ?? 4))
}

// Part 2 screen state 2 (requirement 9.2, 9.4): every field of every document in the
// effective set, with its verdict and confidence as a percentage, ordered problems first,
// then uncertain, then matches. Confidence as a percentage (not the raw 0-1 Part 1 shows,
// design §4 state 2's own wording) and verdict always shown with text plus an icon, never
// colour alone (requirement 8.4, 13.5).
//
// Kept narrow on purpose: the field and its document share one cell (the file name sits under
// the field name), there is one "Found" column instead of the raw and the normalised value, and
// the verdict and the button never wrap -- otherwise the table is too cramped to read.
function ShipmentFieldTable({ fieldResults, onOpenField }) {
  if (fieldResults.length === 0) {
    return <p>No field results yet.</p>
  }

  const sorted = sortByProblemsFirst(fieldResults)
  const clickable = typeof onOpenField === 'function'

  return (
    <div className="table-scroll">
      <table className="field-table field-table--shipment">
        <caption className="visually-hidden">
          Extracted fields and their validation verdicts, across every document
        </caption>
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Confidence</th>
            <th scope="col">Verdict</th>
            <th scope="col">Found</th>
            <th scope="col">Expected</th>
            {clickable && (
              <th scope="col">
                <span className="visually-hidden">Detail</span>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((field) => {
            const { label, icon } = verdictMeta(field.verdict)
            const confidencePct =
              field.verdict_confidence != null
                ? `${Math.round(field.verdict_confidence * 100)}%`
                : '—'
            return (
              <tr key={`${field.document_id}-${field.field_name}`}>
                <th scope="row">
                  {fieldLabel(field.field_name)}
                  <span className="field-table__doc">{field.filename}</span>
                </th>
                <td>{confidencePct}</td>
                <td className={`verdict-cell verdict-cell--${field.verdict} nowrap`}>
                  <span aria-hidden="true">{icon}</span> {label}
                </td>
                <td>{field.found ?? field.value ?? '—'}</td>
                <td>{field.expected ?? '—'}</td>
                {clickable && (
                  <td className="nowrap">
                    {field.verdict !== 'not_applicable' && (
                      <button
                        type="button"
                        className="table-button"
                        onClick={() => onOpenField(field)}
                        aria-label={`View ${fieldLabel(field.field_name)} in ${field.filename}`}
                      >
                        View
                      </button>
                    )}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default ShipmentFieldTable
