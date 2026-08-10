/** Pre-creates the account e2e/auth.spec.ts's login tests sign into, so
 * those tests don't depend on running after the signup test (Playwright
 * doesn't guarantee cross-file order) or duplicate signup logic per test.
 * The email is one of the fixed rows backend/scripts/seed_e2e.py puts on
 * the beta allowlist before this runs (webServer readiness is awaited
 * before globalSetup, so the backend is already up). */
async function globalSetup() {
  const res = await fetch('http://localhost:8000/auth/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: 'e2e-existing@example.com',
      password: 'GoodPass1',
      date_of_birth: '2000-01-01',
    }),
  })
  if (!res.ok && res.status !== 409) {
    throw new Error(`global-setup: failed to pre-create e2e-existing user (${res.status})`)
  }
}

export default globalSetup
