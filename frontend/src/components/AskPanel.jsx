import { useState } from 'react'
import { postQuery } from '../api/api.js'

const EXAMPLE_QUESTIONS = [
  'How many documents were flagged for review this week?',
  'Show me everything pending review for customer acme.',
  'What is the most common mismatched field?',
]

// design §7 area 3 / §2 diagram's "Ask box": a question in, a grounded answer out. Every
// answer always shows the SQL that produced it (design §5 step 4, "no citations, no
// answer" — Nova's own principle) — including on a refusal, so the box is never a black
// box even when the gate says no (design §8: "query not allowed").
function AskPanel() {
  const [question, setQuestion] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  async function runQuestion(q) {
    const trimmed = q.trim()
    if (!trimmed) return
    setIsLoading(true)
    setError(null)
    try {
      const data = await postQuery(trimmed)
      setResult(data)
    } catch (err) {
      setError(err.message)
      setResult(null)
    } finally {
      setIsLoading(false)
    }
  }

  function handleSubmit(event) {
    event.preventDefault()
    runQuestion(question)
  }

  function handleExampleClick(example) {
    setQuestion(example)
    runQuestion(example)
  }

  const isRefusal = result && result.sql === null && result.columns.length === 0 && result.rows.length === 0

  return (
    <section className="card" aria-labelledby="ask-heading">
      <h2 id="ask-heading">Ask a question</h2>
      <p>
        Ask about the stored runs in plain English — e.g. "how many shipments were flagged
        this week?" This calls Gemini to write a read-only query, so each question spends one
        real LLM call.
      </p>

      <form onSubmit={handleSubmit}>
        <div className="field-row">
          <label htmlFor="ask-question">Your question</label>
          <input
            id="ask-question"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="How many shipments were flagged this week?"
          />
        </div>
        <button type="submit" disabled={isLoading || !question.trim()}>
          {isLoading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      <div className="ask-examples">
        <span>Try:</span>
        {EXAMPLE_QUESTIONS.map((example) => (
          <button
            key={example}
            type="button"
            className="ask-example-button"
            onClick={() => handleExampleClick(example)}
            disabled={isLoading}
          >
            {example}
          </button>
        ))}
      </div>

      {error && (
        <p className="error-text" role="alert">
          Could not get an answer: {error}
        </p>
      )}

      {result && (
        <div className="ask-result" role="status" aria-live="polite">
          {isRefusal ? (
            <p className="error-text">Query not allowed: {result.answer}</p>
          ) : result.columns.length === 0 ? (
            <p className="ask-result__answer">{String(result.answer)}</p>
          ) : (
            <table className="ask-result-table">
              <caption className="visually-hidden">Query results</caption>
              <thead>
                <tr>
                  {result.columns.map((col) => (
                    <th scope="col" key={col}>
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{cell === null ? '—' : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="ask-result__explanation">{result.explanation}</p>
          {result.sql && (
            <details className="ask-result__sql">
              <summary>SQL used</summary>
              <pre>{result.sql}</pre>
            </details>
          )}
        </div>
      )}
    </section>
  )
}

export default AskPanel
