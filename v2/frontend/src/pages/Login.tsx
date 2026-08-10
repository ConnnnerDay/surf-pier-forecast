import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiRequest, ApiError } from '../api/client'
import { OAuthButtons } from '../components/OAuthButtons'
import { deviceLabel } from '../utils/device'
import { getPasskey, passkeysSupported } from '../utils/webauthn'

export function Login() {
  const { login, loginWithTokens } = useAuth()
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

  const loginWithPasskey = async () => {
    setError(null)
    setSubmitting(true)
    try {
      const options = await apiRequest('/auth/passkey/login/options', { method: 'POST' })
      const credential = await getPasskey(options)
      const tokens = await apiRequest<{ access_token: string; refresh_token: string }>(
        '/auth/passkey/login/verify',
        { method: 'POST', body: { credential, device_label: deviceLabel() } },
      )
      await loginWithTokens(tokens.access_token, tokens.refresh_token)
      navigate('/dashboard')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Passkey sign-in failed')
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
      {passkeysSupported() && (
        <button
          type="button"
          className="button button--secondary"
          style={{ marginTop: '1rem', width: '100%' }}
          onClick={loginWithPasskey}
          disabled={submitting}
        >
          Sign in with a passkey
        </button>
      )}
      <OAuthButtons />
      <p className="text-muted" style={{ marginTop: '2rem' }}>
        Don't have an account? <Link to="/signup">Sign up</Link>
      </p>
    </div>
  )
}
