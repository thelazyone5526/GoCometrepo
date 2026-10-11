import { useState } from 'react'
import { pageImageUrl } from '../api/api.js'
import { fieldLabel, verdictMeta } from '../outcomes.js'

// Part 2 screen state 3 (requirements 10.1-10.4): the detail behind one flagged field or
// cross-document discrepancy, or the reason behind one uncertain item. `item` is one of:
//   { type: 'field', field, runId }
//   { type: 'cross_check', check }
//   { type: 'uncertain', text }
// Closing returns to state 2 (requirement 10.4) -- handled by the caller via `onClose`.
function DiscrepancyDetail({ item, onClose }) {
  return (
    <section className="card discrepancy-detail" aria-labelledby="discrepancy-heading">
      <div className="discrepancy-detail__header">
        <h2 id="discrepancy-heading">Detail</h2>
        <button type="button" onClick={onClose}>
          Back to verification result
        </button>
      </div>
      {item.type === 'field' && <FieldDetail field={item.field} runId={item.runId} />}
      {item.type === 'cross_check' && <CrossCheckDetail check={item.check} />}
      {item.type === 'uncertain' && <UncertainDetail text={item.text} />}
    </section>
  )
}

function FieldDetail({ field, runId }) {
  const { label, icon } = verdictMeta(field.verdict)
  const confidencePct =
    field.verdict_confidence != null ? `${Math.round(field.verdict_confidence * 100)}%` : '—'
  const hasBox = Array.isArray(field.box) && field.box.length === 4
  // `box` is [x0, y0, x1, y1] in pixels of the stored page image (design §3's grounding
  // note), not a 0-1 fraction, so the overlay has to know the image's own pixel size to
  // convert to a CSS percentage -- read once the <img> has loaded.
  const [imageSize, setImageSize] = useState(null)

  return (
    <div>
      <dl className="discrepancy-fields">
        <div>
          <dt>Document</dt>
          <dd>{field.filename}</dd>
        </div>
        <div>
          <dt>Field</dt>
          <dd>{fieldLabel(field.field_name)}</dd>
        </div>
        <div>
          <dt>Verdict</dt>
          <dd className={`verdict-cell verdict-cell--${field.verdict}`}>
            <span aria-hidden="true">{icon}</span> {label}
          </dd>
        </div>
        <div>
          <dt>Found</dt>
          <dd>{field.value ?? field.found ?? '—'}</dd>
        </div>
        <div>
          <dt>Expected</dt>
          <dd>{field.expected ?? '—'}</dd>
        </div>
        <div>
          <dt>Rule ID</dt>
          <dd>{field.rule_id ?? '—'}</dd>
        </div>
        <div>
          <dt>Reason</dt>
          <dd>{field.verdict_reason ?? '—'}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{confidencePct}</dd>
        </div>
        <div>
          <dt>Page</dt>
          <dd>{field.page ?? '—'}</dd>
        </div>
      </dl>

      {field.source_text && (
        <div>
          <h3>Source snippet</h3>
          <p className="source-snippet">"{field.source_text}"</p>
        </div>
      )}

      <div>
        <h3>Page image</h3>
        {runId != null && field.page != null ? (
          <div className="page-image-wrapper">
            <img
              src={pageImageUrl(runId, field.page - 1)}
              alt={`Page ${field.page} of ${field.filename}`}
              className="page-image"
              onLoad={(event) =>
                setImageSize({
                  width: event.target.naturalWidth,
                  height: event.target.naturalHeight,
                })
              }
            />
            {hasBox && imageSize && (
              <div
                className="page-image-box"
                style={{
                  left: `${(field.box[0] / imageSize.width) * 100}%`,
                  top: `${(field.box[1] / imageSize.height) * 100}%`,
                  width: `${((field.box[2] - field.box[0]) / imageSize.width) * 100}%`,
                  height: `${((field.box[3] - field.box[1]) / imageSize.height) * 100}%`,
                }}
                aria-hidden="true"
              />
            )}
          </div>
        ) : (
          <p>Location not available.</p>
        )}
        {!hasBox && runId != null && field.page != null && <p>Location not available on the page.</p>}
      </div>
    </div>
  )
}

function CrossCheckDetail({ check }) {
  return (
    <div>
      <h3>{fieldLabel(check.field_name)} — across documents</h3>
      <p>{check.reason}</p>
      <table className="field-table">
        <thead>
          <tr>
            <th scope="col">Document</th>
            <th scope="col">Value</th>
            <th scope="col">Source snippet</th>
            <th scope="col">Page</th>
          </tr>
        </thead>
        <tbody>
          {check.compared.map((row) => (
            <tr key={row.document_id}>
              <th scope="row">{row.filename}</th>
              <td>{row.value ?? '(no value)'}</td>
              <td>{row.source_text ? `"${row.source_text}"` : '—'}</td>
              <td>{row.page ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function UncertainDetail({ text }) {
  return (
    <div>
      <p>{text}</p>
      <p>This item stays uncertain. There is no action to take on it here.</p>
    </div>
  )
}

export default DiscrepancyDetail
