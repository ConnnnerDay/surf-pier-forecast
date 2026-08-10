import { Link } from 'react-router-dom'
import { useTheme } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'

const THEME_LABEL: Record<string, string> = { system: '🌗', light: '☀️', dark: '🌙' }
const THEME_ORDER = ['system', 'light', 'dark'] as const

export function Header() {
  const { theme, setTheme } = useTheme()
  const { user, logout } = useAuth()

  const cycleTheme = () => {
    const next = THEME_ORDER[(THEME_ORDER.indexOf(theme as never) + 1) % THEME_ORDER.length]
    setTheme(next)
  }

  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0.75rem 1.25rem',
        borderBottom: '1px solid var(--color-border)',
      }}
    >
      <Link to="/" style={{ fontWeight: 700, textDecoration: 'none', color: 'var(--color-text)' }}>
        🎣 Fishing Forecast
      </Link>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <button
          className="button button--secondary"
          onClick={cycleTheme}
          aria-label={`Theme: ${theme}`}
          title={`Theme: ${theme}`}
        >
          {THEME_LABEL[theme]}
        </button>
        {user ? (
          <button className="button button--secondary" onClick={logout}>
            Log out
          </button>
        ) : (
          <Link to="/login" className="button button--secondary">
            Log in
          </Link>
        )}
      </div>
    </header>
  )
}
