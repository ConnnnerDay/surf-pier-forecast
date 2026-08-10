export interface OAuthLoginURL {
  authorize_url: string
  state: string
}

export interface OAuthLoginResult {
  status: 'logged_in' | 'needs_signup_info'
  tokens: { access_token: string; refresh_token: string; token_type: string } | null
  pending_token: string | null
}

export type OAuthProvider = 'google' | 'apple'

const STATE_KEY = (provider: OAuthProvider) => `ff.oauth_state.${provider}`

export const oauthState = {
  set(provider: OAuthProvider, state: string) {
    sessionStorage.setItem(STATE_KEY(provider), state)
  },
  consume(provider: OAuthProvider): string | null {
    const value = sessionStorage.getItem(STATE_KEY(provider))
    sessionStorage.removeItem(STATE_KEY(provider))
    return value
  },
}
