import { expect, test } from '@playwright/test'

test('rejects signup for an email that is not on the beta allowlist', async ({ page }) => {
  await page.goto('/signup')
  await page.fill('#email', 'not-invited@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.fill('#dob', '1990-01-01')
  await page.click('button[type="submit"]')

  await expect(page.locator('.field-error')).toHaveText(/allowlist/i)
  await expect(page).toHaveURL(/\/signup$/)
})

test('signs up, completes onboarding, and lands on the dashboard', async ({ page }) => {
  await page.goto('/signup')
  await page.fill('#email', 'e2e-newuser@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.fill('#dob', '1995-06-15')
  await page.click('button[type="submit"]')

  await expect(page).toHaveURL(/\/onboarding$/)
  await expect(page.getByRole('heading', { name: /go\/no-go score/i })).toBeVisible()

  for (let i = 0; i < 3; i++) {
    await page.click('button:has-text("Next")')
  }
  await page.click('button:has-text("Get started")')

  await expect(page).toHaveURL(/\/dashboard$/)
  await expect(page.getByRole('heading', { name: /hey, e2e-newuser/i })).toBeVisible()
})

test('logs in with an existing account', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#email', 'e2e-existing@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.click('button[type="submit"]')

  await expect(page).toHaveURL(/\/dashboard$/)
})

test('shows an error for the wrong password', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#email', 'e2e-existing@example.com')
  await page.fill('#password', 'TotallyWrongPass1')
  await page.click('button[type="submit"]')

  await expect(page.getByText(/incorrect email or password/i)).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
})

test('logging out returns to a logged-out state', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#email', 'e2e-existing@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/dashboard$/)

  await page.click('button:has-text("Log out")')
  await expect(page.getByRole('link', { name: /log in/i })).toBeVisible()

  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/login$/)
})
