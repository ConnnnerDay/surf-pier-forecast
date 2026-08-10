import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { apiRequest, ApiError } from '../api/client'
import { oauthState, type OAuthLoginResult, type OAuthProvider } from '../api/oauth'
import { useAuth } from '../context/AuthContext'

function parseFragment(): Record<string, string> {
  const hash = window.location.hash.startsWith('#')
    ? window.location.hash.slice(1)
    : window.location.hash
  return Object.fromEntries(new URLSearchParams(hash))
}

export function OAuthCallback() {
  const { provider } = useParams<{ provider: OAuthProvider }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { loginWithTokens } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const ran = useRef(false)

  useEffect(() => {
    if (ran.current || !provider) return
    ran.current = true

    const run = async () => {
      const fragment = parseFragment()
      const returnedState = searchParams.get('state') || fragment.state
      const expectedState = oauthState.consume(provider)
      if (!returnedState || returnedState !== expectedState) {
        setError('Sign-in link expired or was tampered with — please try again.')
        return
      }

      try {
        const body =
          provider === 'apple'
            ? { id_token: fragment.id_token }
            : { code: searchParams.get('code') }
        const result = await apiRequest<OAuthLoginResult>(`/oauth/${provider}/callback`, {
          method: 'POST',
          body,
        })

        if (result.status === 'logged_in' && result.tokens) {
          await loginWithTokens(result.tokens.access_token, result.tokens.refresh_token)
          navigate('/dashboard', { replace: true })
        } else if (result.pending_token) {
          navigate('/oauth/complete-signup', {
            replace: true,
            state: { pendingToken: result.pending_token },
          })
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Sign-in failed')
      }
    }

    run()
  }, [provider, searchParams, navigate, loginWithTokens])

  if (error) {
    return (
      <div className="page">
        <p className="field-error">{error}</p>
      </div>
    )
  }

  return <div className="page">Signing you in…</div>
}
