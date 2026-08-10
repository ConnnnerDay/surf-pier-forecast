import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { ApiError } from '../api/client'

export function Signup() {
  const { signup } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [dateOfBirth, setDateOfBirth] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await signup(email, password, dateOfBirth)
      navigate('/onboarding')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Signup failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page">
      <h1>Create your account</h1>
      <p className="text-muted">
        Private beta — this only works if your email is on the beta allowlist.{' '}
        <Link to="/">Request access</Link> if you don't have it yet.
      </p>
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
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <span className="text-muted" style={{ fontSize: '0.8rem' }}>
            At least 8 characters, with an uppercase letter, lowercase letter, and a number.
          </span>
        </div>
        <div className="field">
          <label htmlFor="dob">Date of birth</label>
          <input
            id="dob"
            type="date"
            required
            value={dateOfBirth}
            onChange={(e) => setDateOfBirth(e.target.value)}
          />
          <span className="text-muted" style={{ fontSize: '0.8rem' }}>
            You must be 13 or older to sign up.
          </span>
        </div>
        {error && <div className="field-error" style={{ marginBottom: '1rem' }}>{error}</div>}
        <button className="button" type="submit" disabled={submitting}>
          Sign up
        </button>
      </form>
      <p className="text-muted" style={{ marginTop: '2rem' }}>
        Already have an account? <Link to="/login">Log in</Link>
      </p>
    </div>
  )
}
