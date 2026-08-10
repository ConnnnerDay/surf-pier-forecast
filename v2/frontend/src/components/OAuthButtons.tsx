import { useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { oauthState, type OAuthLoginURL, type OAuthProvider } from '../api/oauth'

const LABEL: Record<OAuthProvider, string> = { google: 'Google', apple: 'Apple' }

export function OAuthButtons() {
  const [error, setError] = useState<string | null>(null)

  const start = async (provider: OAuthProvider) => {
    setError(null)
    try {
      const result = await apiRequest<OAuthLoginURL>(`/oauth/${provider}/login`)
      oauthState.set(provider, result.state)
      window.location.href = result.authorize_url
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        setError(`${LABEL[provider]} sign-in isn't set up on this server yet.`)
      } else {
        setError(err instanceof ApiError ? err.message : 'Could not start sign-in')
      }
    }
  }

  return (
    <div style={{ marginTop: '1rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button
          type="button"
          className="button button--secondary"
          style={{ flex: 1 }}
          onClick={() => start('google')}
        >
          Continue with Google
        </button>
        <button
          type="button"
          className="button button--secondary"
          style={{ flex: 1 }}
          onClick={() => start('apple')}
        >
          Continue with Apple
        </button>
      </div>
      {error && (
        <p className="text-muted" style={{ fontSize: '0.85rem', marginTop: '0.5rem' }}>
          {error}
        </p>
      )}
    </div>
  )
}
