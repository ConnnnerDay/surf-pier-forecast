import { expect, test } from '@playwright/test'

test('adds multiple locations and switches between them', async ({ page }) => {
  await page.goto('/signup')
  await page.fill('#email', 'e2e-locations@example.com')
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
  await page.fill('#label', 'North Spot')
  await page.fill('#lat', '34.2104')
  await page.fill('#lng', '-77.7964')
  await page.click('button:has-text("Save location")')
  await expect(page.getByRole('heading', { name: 'North Spot' })).toBeVisible()

  await page.click('button:has-text("Add a location")')
  await page.fill('#label', 'South Spot')
  await page.fill('#lat', '33.65')
  await page.fill('#lng', '-78.94')
  await page.click('button:has-text("Save location")')

  const tablist = page.getByRole('tablist', { name: 'Saved locations' })
  await expect(tablist.getByRole('tab', { name: 'North Spot' })).toBeVisible()
  await expect(tablist.getByRole('tab', { name: 'South Spot' })).toBeVisible()

  // Switch to South Spot.
  await tablist.getByRole('tab', { name: 'South Spot' }).click()
  await expect(page.getByRole('heading', { name: 'South Spot' })).toBeVisible()

  // Set North Spot as the default.
  await page.click('button[aria-label="Set North Spot as default"]')
  await expect(page.locator('button[aria-label="Default location"]')).toBeVisible()

  // Remove South Spot.
  await page.click('button[aria-label="Remove South Spot"]')
  await expect(tablist.getByRole('tab', { name: 'South Spot' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'North Spot' })).toBeVisible()
})
