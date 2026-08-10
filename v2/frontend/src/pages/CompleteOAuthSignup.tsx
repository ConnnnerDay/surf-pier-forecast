import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiRequest, ApiError } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface LocationState {
  pendingToken?: string
}

export function CompleteOAuthSignup() {
  const location = useLocation()
  const navigate = useNavigate()
  const { loginWithTokens } = useAuth()
  const pendingToken = (location.state as LocationState | null)?.pendingToken

  const [dateOfBirth, setDateOfBirth] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!pendingToken) {
    return (
      <div className="page">
        <p className="field-error">
          Nothing to finish here — start sign-in again from the login page.
        </p>
      </div>
    )
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const tokens = await apiRequest<{ access_token: string; refresh_token: string }>(
        '/oauth/complete-signup',
        { method: 'POST', body: { pending_token: pendingToken, date_of_birth: dateOfBirth } },
      )
      await loginWithTokens(tokens.access_token, tokens.refresh_token)
      navigate('/onboarding', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not finish signing up')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page">
      <h1>One more thing</h1>
      <p className="text-muted">
        We need your date of birth to finish creating your account (you must be 13 or older).
      </p>
      <form onSubmit={submit}>
        <div className="field">
          <label htmlFor="dob">Date of birth</label>
          <input
            id="dob"
            type="date"
            required
            value={dateOfBirth}
            onChange={(e) => setDateOfBirth(e.target.value)}
          />
        </div>
        {error && <div className="field-error">{error}</div>}
        <button className="button" type="submit" disabled={submitting}>
          Finish signing up
        </button>
      </form>
    </div>
  )
}
