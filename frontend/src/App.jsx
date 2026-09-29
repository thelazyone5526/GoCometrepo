import { useEffect, useState } from 'react'

// Phase 1 placeholder screen: proves the browser -> Vite proxy -> FastAPI path works.
// The real operator UI replaces this in Phase 10.
function App() {
  const [health, setHealth] = useState({ state: 'loading' })

  useEffect(() => {
    fetch('/api/health')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((body) => setHealth({ state: 'ok', body }))
      .catch((err) => setHealth({ state: 'error', message: err.message }))
  }, [])

  return (
    <main>
      <h1>Trade Document Pipeline</h1>
      <p role="status" aria-live="polite">
        {health.state === 'loading' && 'Checking the backend…'}
        {health.state === 'ok' && `Backend status: ${health.body.status}`}
        {health.state === 'error' &&
          `Backend unreachable (${health.message}). Start it with: python -m app.main`}
      </p>
    </main>
  )
}

export default App
