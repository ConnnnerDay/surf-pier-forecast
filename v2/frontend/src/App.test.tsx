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

  it('redirects unauthenticated users away from /profile', () => {
    renderAt('/profile')
    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })

  it('redirects unauthenticated users away from /regulations', () => {
    renderAt('/regulations')
    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })

  it('renders a 404 for unknown routes', () => {
    renderAt('/nope')
    expect(screen.getByText(/page not found/i)).toBeInTheDocument()
  })

  it('shows a login link on the signup page', () => {
    renderAt('/signup')
    expect(screen.getByRole('heading', { name: /create your account/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /continue with google/i })).toBeInTheDocument()
  })

  it('tells the user to restart when hitting complete-signup with no pending token', () => {
    renderAt('/oauth/complete-signup')
    expect(screen.getByText(/start sign-in again/i)).toBeInTheDocument()
  })
})
