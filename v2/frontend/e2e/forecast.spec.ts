import { expect, test } from '@playwright/test'

test('adds a location and sees a real forecast render', async ({ page }) => {
  // domain/forecast.py:generate_forecast() is a live multi-source fetch
  // (NOAA/NWS/NDBC/astro + per-species image lookups); give the whole test
  // real room rather than racing it. Typically completes in ~15-20s.
  test.setTimeout(60_000)

  await page.goto('/signup')
  await page.fill('#email', 'e2e-forecast@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.fill('#dob', '1990-01-01')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/onboarding$/)
  for (let i = 0; i < 3; i++) {
    await page.click('button:has-text("Next")')
  }
  await page.click('button:has-text("Get started")')
  await expect(page).toHaveURL(/\/dashboard$/)

  await page.click('button:has-text("Add your first spot")')
  await page.fill('#label', 'Wrightsville Beach')
  await page.fill('#lat', '34.2104')
  await page.fill('#lng', '-77.7964')
  await page.click('button:has-text("Save location")')

  await expect(page.getByRole('heading', { name: 'Wrightsville Beach' })).toBeVisible()

  await expect(page.getByText('/ 100')).toBeVisible({ timeout: 45_000 })
  await expect(page.getByRole('heading', { name: /best time to fish today/i })).toBeVisible()
})
