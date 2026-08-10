import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiRequest, ApiError } from '../api/client'

export function Landing() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'submitting' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setStatus('submitting')
    setError(null)
    try {
      await apiRequest('/beta-requests', { method: 'POST', body: { email } })
      setStatus('done')
    } catch (err) {
      setStatus('error')
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
    }
  }

  return (
    <div className="page">
      <h1>Know before you go.</h1>
      <p className="text-muted">
        Tides, wind, waves, and what's biting — for any US coastal fishing spot. We're in
        private beta right now. Request access and we'll reach out.
      </p>

      {status === 'done' ? (
        <div className="card" style={{ marginTop: '1rem' }}>
          You're on the list — we'll email you when there's a spot open.
        </div>
      ) : (
        <form onSubmit={submit} style={{ marginTop: '1rem' }}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
            {error && <span className="field-error">{error}</span>}
          </div>
          <button className="button" type="submit" disabled={status === 'submitting'}>
            Request beta access
          </button>
        </form>
      )}

      <p className="text-muted" style={{ marginTop: '2rem' }}>
        Already have access? <Link to="/login">Log in</Link>
      </p>
    </div>
  )
}
