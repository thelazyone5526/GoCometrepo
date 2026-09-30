import { useState } from 'react'

const ACCEPTED_TYPES = '.pdf,.png,.jpg,.jpeg'

// design §7 area 1: "choose a customer and a file, start a run." Kept to exactly those three
// controls — no drag-and-drop, no client-side size/type pre-check beyond the `accept`
// attribute, since design §6 says the backend itself rejects oversized or wrong-type files.
function UploadPanel({ customers, onStartRun, isSubmitting }) {
  const [customerId, setCustomerId] = useState('')
  const [file, setFile] = useState(null)
  const [rerun, setRerun] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(event) {
    event.preventDefault()
    if (!customerId || !file) {
      setError('Choose a customer and a file before starting a run.')
      return
    }
    setError(null)
    try {
      await onStartRun({ file, customerId, rerun })
      setFile(null)
      setRerun(false)
      event.target.reset()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section className="card" aria-labelledby="upload-heading">
      <h2 id="upload-heading">Start a run</h2>
      <form onSubmit={handleSubmit}>
        <div className="field-row">
          <label htmlFor="customer-select">Customer</label>
          <select
            id="customer-select"
            value={customerId}
            onChange={(event) => setCustomerId(event.target.value)}
            required
          >
            <option value="">Select a customer…</option>
            {customers.map((customer) => (
              <option key={customer.customer_id} value={customer.customer_id}>
                {customer.customer_name}
              </option>
            ))}
          </select>
        </div>

        <div className="field-row">
          <label htmlFor="file-input">Document (PDF, PNG or JPG)</label>
          <input
            id="file-input"
            type="file"
            accept={ACCEPTED_TYPES}
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            required
          />
        </div>

        <div className="field-row field-row--checkbox">
          <input
            id="rerun-checkbox"
            type="checkbox"
            checked={rerun}
            onChange={(event) => setRerun(event.target.checked)}
          />
          <label htmlFor="rerun-checkbox">
            Force a rerun (ignore any existing run for this file)
          </label>
        </div>

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={isSubmitting}>
          {isSubmitting ? 'Starting…' : 'Start run'}
        </button>
      </form>
    </section>
  )
}

export default UploadPanel
