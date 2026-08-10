import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'

export function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [needsTotp, setNeedsTotp] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(email, password, totpCode || undefined)
      navigate('/dashboard')
    } catch (err) {
      if (err instanceof ApiError && err.message === 'TOTP code required') {
        setNeedsTotp(true)
      } else {
        setError(err instanceof ApiError ? err.message : 'Login failed')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page">
      <h1>Log in</h1>
      <form onSubmit={submit} style={{ marginTop: '1rem' }}>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {needsTotp && (
          <div className="field">
            <label htmlFor="totp">2FA code</label>
            <input
              id="totp"
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
            />
          </div>
        )}
        {error && <div className="field-error" style={{ marginBottom: '1rem' }}>{error}</div>}
        <button className="button" type="submit" disabled={submitting}>
          Log in
        </button>
      </form>
      <p className="text-muted" style={{ marginTop: '2rem' }}>
        Don't have an account? <Link to="/signup">Sign up</Link>
      </p>
    </div>
  )
}
