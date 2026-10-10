import { useEffect, useState } from 'react'
import { getDraft, markReviewedNoSend, putDraft, sendDraft } from '../api/api.js'

const EMPTY_FORM = { recipient: '', subject: '', body: '' }

function toForm(draft) {
  return { recipient: draft.recipient ?? '', subject: draft.subject ?? '', body: draft.body ?? '' }
}

// Part 2 screen state 4 (requirements 6.6, 11.1-11.4): editable reply, pre-filled from the
// generated draft, with an "edited" marker, a reset to the generated text, a disabled Send
// when a required field is empty, a confirmation step for an approval draft that still has
// discrepancies or uncertain items (requirement 7.4), and a "mark reviewed" path that sends
// nothing (requirement 7.5).
function DraftPanel({ shipment }) {
  const [draft, setDraft] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [loadError, setLoadError] = useState(null)
  const [saveStatus, setSaveStatus] = useState('idle') // idle, saving, saved, error
  const [sendError, setSendError] = useState(null)
  const [needsConfirmation, setNeedsConfirmation] = useState(false)
  const [isSending, setIsSending] = useState(false)

  useEffect(() => {
    let cancelled = false
    getDraft(shipment.shipment_id)
      .then((data) => {
        if (cancelled) return
        setDraft(data)
        setForm(toForm(data))
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [shipment.shipment_id])

  if (loadError) {
    return (
      <p className="error-text" role="alert">
        Could not load draft: {loadError}
      </p>
    )
  }

  if (!draft) {
    return (
      <p role="status" aria-live="polite">
        Loading draft…
      </p>
    )
  }

  const isGeneratedField = (name) => form[name] === (draft[name] ?? '')
  const missingField = !form.recipient.trim() ? 'recipient' : !form.body.trim() ? 'body' : null

  async function saveEdit(nextForm) {
    setSaveStatus('saving')
    try {
      const updated = await putDraft(shipment.shipment_id, nextForm)
      setDraft(updated)
      setForm(toForm(updated))
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 1500)
    } catch (err) {
      setSaveStatus('error')
      setLoadError(err.message)
    }
  }

  function handleFieldChange(name, value) {
    setForm((prev) => ({ ...prev, [name]: value }))
  }

  function handleReset() {
    // R11.1's reset: back to the generated (version 1) text, re-saved as a fresh edit so
    // the server's "current version" matches what the operator now sees.
    const generated = { recipient: draft.recipient, subject: draft.subject, body: draft.body }
    if (!draft.is_edited) {
      setForm(generated)
      return
    }
    saveEdit(generated)
  }

  async function doSend(confirmed) {
    setIsSending(true)
    setSendError(null)
    try {
      await sendDraft(shipment.shipment_id, { confirmed })
      const refreshed = await getDraft(shipment.shipment_id)
      setDraft(refreshed)
      setForm(toForm(refreshed))
      setNeedsConfirmation(false)
    } catch (err) {
      if (err.message.includes('HTTP 409') && !confirmed) {
        setNeedsConfirmation(true)
      } else {
        setSendError(err.message)
      }
    } finally {
      setIsSending(false)
    }
  }

  async function handleSend() {
    if (form.recipient !== draft.recipient || form.subject !== draft.subject || form.body !== draft.body) {
      await saveEdit(form)
    }
    await doSend(false)
  }

  async function handleReviewedNoSend() {
    try {
      await markReviewedNoSend(shipment.shipment_id)
      const refreshed = await getDraft(shipment.shipment_id)
      setDraft(refreshed)
    } catch (err) {
      setSendError(err.message)
    }
  }

  if (draft.sent) {
    return (
      <section className="card draft-panel" aria-labelledby="draft-heading">
        <h2 id="draft-heading">Reply to supplier</h2>
        <p className="draft-sent-label">Sent at {new Date(draft.sent_at).toLocaleString()}</p>
        <dl className="draft-readonly">
          <div>
            <dt>Recipient</dt>
            <dd>{draft.recipient}</dd>
          </div>
          <div>
            <dt>Subject</dt>
            <dd>{draft.subject}</dd>
          </div>
        </dl>
        <pre className="amendment-draft__text" aria-label="Sent message, read only">
          {draft.body}
        </pre>
      </section>
    )
  }

  const hasIssues = shipment.discrepancy_count > 0 || shipment.uncertain_count > 0

  return (
    <section className="card draft-panel" aria-labelledby="draft-heading">
      <h2 id="draft-heading">Reply to supplier</h2>
      <p className="draft-not-sent-label">Not sent</p>

      {draft.render_error && (
        <p className="error-text" role="alert">
          Could not generate a draft automatically: {draft.render_error}. You can still write one
          below.
        </p>
      )}

      {shipment.discrepancy_count > 0 && (
        <p>
          <strong>{shipment.discrepancy_count}</strong> discrepancy(ies) on this shipment.
        </p>
      )}
      {shipment.uncertain_count > 0 && (
        <p>
          <strong>{shipment.uncertain_count}</strong> item(s) need your check (see "Needs your
          check" above) — these are not included in the message below.
        </p>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault()
          handleSend()
        }}
      >
        <div className="field-row">
          <label htmlFor="draft-recipient">
            Recipient {!isGeneratedField('recipient') && <span className="edited-marker">(edited)</span>}
          </label>
          <input
            id="draft-recipient"
            type="text"
            value={form.recipient}
            onChange={(event) => handleFieldChange('recipient', event.target.value)}
            onBlur={() => saveEdit(form)}
          />
        </div>

        <div className="field-row">
          <label htmlFor="draft-subject">
            Subject {!isGeneratedField('subject') && <span className="edited-marker">(edited)</span>}
          </label>
          <input
            id="draft-subject"
            type="text"
            value={form.subject}
            onChange={(event) => handleFieldChange('subject', event.target.value)}
            onBlur={() => saveEdit(form)}
          />
        </div>

        <div className="field-row">
          <label htmlFor="draft-body">
            Message {!isGeneratedField('body') && <span className="edited-marker">(edited)</span>}
          </label>
          <textarea
            id="draft-body"
            rows={10}
            value={form.body}
            onChange={(event) => handleFieldChange('body', event.target.value)}
            onBlur={() => saveEdit(form)}
          />
        </div>

        <div className="draft-actions">
          <button type="button" onClick={handleReset}>
            Reset to generated text
          </button>
          <button type="submit" disabled={!!missingField || isSending}>
            {isSending ? 'Sending…' : 'Send'}
          </button>
          <button type="button" onClick={handleReviewedNoSend} disabled={isSending}>
            Mark reviewed, don't send
          </button>
        </div>

        {missingField && (
          <p className="error-text" role="alert">
            Cannot send: {missingField} is required.
          </p>
        )}

        {saveStatus === 'error' && (
          <p className="error-text" role="alert">
            Could not save your edit.
          </p>
        )}

        {sendError && (
          <p className="error-text" role="alert">
            Could not send: {sendError}
          </p>
        )}
      </form>

      {needsConfirmation && (
        <div className="confirm-dialog" role="alertdialog" aria-labelledby="confirm-heading">
          <h3 id="confirm-heading">Send anyway?</h3>
          <p>
            This shipment is being approved, but it has {shipment.discrepancy_count} discrepancy(ies)
            and {shipment.uncertain_count} uncertain item(s). Send the approval anyway?
          </p>
          <div className="draft-actions">
            <button type="button" onClick={() => doSend(true)} disabled={isSending}>
              Yes, send anyway
            </button>
            <button type="button" onClick={() => setNeedsConfirmation(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {!hasIssues && draft.kind === 'approval' && (
        <p className="draft-hint">No discrepancies or uncertain items — safe to send.</p>
      )}
    </section>
  )
}

export default DraftPanel

