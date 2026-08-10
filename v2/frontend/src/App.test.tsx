import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'
import { AuthProvider } from './context/AuthContext'
import { ThemeProvider } from './context/ThemeContext'
import { UnitsProvider } from './context/UnitsContext'

function renderAt(path: string) {
  return render(
    <ThemeProvider>
      <UnitsProvider>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider>
            <App />
          </AuthProvider>
        </MemoryRouter>
      </UnitsProvider>
    </ThemeProvider>,
  )
}

describe('App routing', () => {
  it('renders the landing page at /', () => {
    renderAt('/')
    expect(screen.getByText(/know before you go/i)).toBeInTheDocument()
  })

  it('renders the login page at /login', () => {
    renderAt('/login')
    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })

  it('redirects unauthenticated users away from /dashboard', () => {
    renderAt('/dashboard')
    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })

  it('renders a 404 for unknown routes', () => {
    renderAt('/nope')
    expect(screen.getByText(/page not found/i)).toBeInTheDocument()
  })
})
