import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import type { Passkey } from '../api/passkey'
import { createPasskey, passkeysSupported } from '../utils/webauthn'

export function PasskeySettings() {
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null)
  const [nickname, setNickname] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const refresh = () => {
    apiRequest<Passkey[]>('/auth/passkey/list', { auth: true })
      .then(setPasskeys)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load passkeys'))
  }

  useEffect(refresh, [])

  if (!passkeysSupported()) return null

  const register = async () => {
    setError(null)
    setBusy(true)
    try {
      const options = await apiRequest('/auth/passkey/register/options', {
        method: 'POST',
        auth: true,
      })
      const credential = await createPasskey(options)
      await apiRequest('/auth/passkey/register/verify', {
        method: 'POST',
        auth: true,
        body: { credential, nickname: nickname || undefined },
      })
      setNickname('')
      refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add passkey')
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: string) => {
    setError(null)
    try {
      await apiRequest(`/auth/passkey/${id}`, { method: 'DELETE', auth: true })
      refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove passkey')
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Passkeys</h3>
      <p className="text-muted">Sign in with Face ID, Touch ID, or a security key.</p>

      {passkeys && passkeys.length > 0 && (
        <ul style={{ paddingLeft: '1.25rem' }}>
          {passkeys.map((pk) => (
            <li key={pk.id} style={{ marginBottom: '0.4rem' }}>
              {pk.device_label || 'Unnamed passkey'}{' '}
              <button
                type="button"
                className="button button--secondary"
                style={{ padding: '0 0.4rem' }}
                onClick={() => remove(pk.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="field" style={{ flexDirection: 'row', gap: '0.5rem' }}>
        <input
          placeholder="Nickname (e.g. MacBook)"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
        />
        <button
          type="button"
          className="button button--secondary"
          onClick={register}
          disabled={busy}
        >
          Add a passkey
        </button>
      </div>
      {error && <div className="field-error">{error}</div>}
    </div>
  )
}
