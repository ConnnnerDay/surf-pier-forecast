import { useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { useAuth } from '../context/AuthContext'

interface EnrollResponse {
  secret: string
  provisioning_uri: string
}

export function TwoFactorSettings() {
  const { user, refreshUser } = useAuth()
  const [pending, setPending] = useState<EnrollResponse | null>(null)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!user) return null

  const startEnroll = async () => {
    setError(null)
    setBusy(true)
    try {
      const result = await apiRequest<EnrollResponse>('/auth/2fa/enroll', {
        method: 'POST',
        auth: true,
      })
      setPending(result)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start 2FA setup')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await apiRequest('/auth/2fa/confirm', { method: 'POST', auth: true, body: { code } })
      setPending(null)
      setCode('')
      await refreshUser()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Invalid code')
    } finally {
      setBusy(false)
    }
  }

  const disable = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await apiRequest('/auth/2fa/disable', { method: 'POST', auth: true, body: { password } })
      setPassword('')
      await refreshUser()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not disable 2FA')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Two-factor authentication</h3>

      {user.totp_enabled ? (
        <form onSubmit={disable}>
          <p className="text-muted">Enabled. Enter your password to turn it off.</p>
          <div className="field">
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && <div className="field-error">{error}</div>}
          <button className="button button--secondary" type="submit" disabled={busy}>
            Disable 2FA
          </button>
        </form>
      ) : pending ? (
        <form onSubmit={confirm}>
          <p className="text-muted">
            Add this secret to an authenticator app (Google Authenticator, 1Password, etc.), then
            enter the 6-digit code it shows.
          </p>
          <p className="card" style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
            {pending.secret}
          </p>
          <div className="field">
            <input
              inputMode="numeric"
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
            />
          </div>
          {error && <div className="field-error">{error}</div>}
          <button className="button" type="submit" disabled={busy}>
            Confirm and enable
          </button>
        </form>
      ) : (
        <>
          <p className="text-muted">Not enabled.</p>
          {error && <div className="field-error">{error}</div>}
          <button className="button button--secondary" onClick={startEnroll} disabled={busy}>
            Set up 2FA
          </button>
        </>
      )}
    </div>
  )
}
