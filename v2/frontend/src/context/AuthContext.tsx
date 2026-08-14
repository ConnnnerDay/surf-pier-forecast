import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { apiRequest, tokenStorage } from '../api/client'
import { deviceLabel } from '../utils/device'

interface User {
  id: string
  email: string
  totp_enabled: boolean
  has_password: boolean
}

interface TokenPair {
  access_token: string
  refresh_token: string
}

interface AuthContextValue {
  user: User | null
  isLoading: boolean
  signup: (email: string, password: string, dateOfBirth: string) => Promise<void>
  login: (email: string, password: string, totpCode?: string) => Promise<void>
  loginWithTokens: (accessToken: string, refreshToken: string) => Promise<void>
  logout: () => void
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const loadMe = useCallback(async () => {
    if (!tokenStorage.access) {
      setIsLoading(false)
      return
    }
    try {
      const me = await apiRequest<User>('/auth/me', { auth: true })
      setUser(me)
    } catch {
      tokenStorage.clear()
      setUser(null)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadMe()
  }, [loadMe])

  const signup = useCallback(
    async (email: string, password: string, dateOfBirth: string) => {
      const tokens = await apiRequest<TokenPair>('/auth/signup', {
        method: 'POST',
        body: { email, password, date_of_birth: dateOfBirth },
      })
      tokenStorage.set(tokens.access_token, tokens.refresh_token)
      await loadMe()
    },
    [loadMe],
  )

  const login = useCallback(
    async (email: string, password: string, totpCode?: string) => {
      const tokens = await apiRequest<TokenPair>('/auth/login', {
        method: 'POST',
        body: { email, password, totp_code: totpCode, device_label: deviceLabel() },
      })
      tokenStorage.set(tokens.access_token, tokens.refresh_token)
      await loadMe()
    },
    [loadMe],
  )

  const loginWithTokens = useCallback(
    async (accessToken: string, refreshToken: string) => {
      tokenStorage.set(accessToken, refreshToken)
      await loadMe()
    },
    [loadMe],
  )

  const logout = useCallback(() => {
    tokenStorage.clear()
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, isLoading, signup, login, loginWithTokens, logout, refreshUser: loadMe }),
    [user, isLoading, signup, login, loginWithTokens, logout, loadMe],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
