import { useState } from 'react'
import { simulateIncomingEmail } from '../api/api.js'

// Demo only (D9 in docs/part2-decisions.md). One click drops a new supplier email into the
// watched inbox folder; the inbox watcher then picks it up and the real pipeline runs, so the
// new shipment appears in the list on its own. It stands in for the email arriving -- the real
// trigger is the watcher reading a file, not this button.
function DemoInbox({ onSent }) {
  const [busyKind, setBusyKind] = useState(null)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)

  async function send(kind) {
    setBusyKind(kind)
    setError(null)
    try {
      const result = await simulateIncomingEmail(kind)
      setMessage(`Email ${result.reference} arrived in the inbox.`)
      onSent(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusyKind(null)
    }
  }

  return (
    <section className="card demo-inbox" aria-labelledby="demo-inbox-heading">
      <h2 id="demo-inbox-heading">Incoming email</h2>
      <p className="demo-inbox__tag">Demo only</p>
      <p className="demo-inbox__note">
        Pretend a supplier just emailed three documents. It lands in the inbox and is checked
        automatically.
      </p>
      <div className="demo-inbox__buttons">
        <button type="button" onClick={() => send('clean')} disabled={busyKind !== null}>
          {busyKind === 'clean' ? 'Sending…' : 'Email: clean shipment'}
        </button>
        <button type="button" onClick={() => send('messy')} disabled={busyKind !== null}>
          {busyKind === 'messy' ? 'Sending…' : 'Email: shipment with problems'}
        </button>
      </div>
      {message && (
        <p className="demo-inbox__status" role="status" aria-live="polite">
          {message}
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          Could not send: {error}
        </p>
      )}
    </section>
  )
}

export default DemoInbox
