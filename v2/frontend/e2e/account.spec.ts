import { expect, test } from '@playwright/test'

test('exports account data and deletes the account', async ({ page }) => {
  await page.goto('/signup')
  await page.fill('#email', 'e2e-account@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.fill('#dob', '1990-01-01')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/onboarding$/)
  for (let i = 0; i < 3; i++) {
    await page.click('button:has-text("Next")')
  }
  await page.click('button:has-text("Get started")')
  await expect(page).toHaveURL(/\/dashboard$/)

  await page.click('a:has-text("Profile")')
  await expect(page).toHaveURL(/\/profile$/)

  const downloadPromise = page.waitForEvent('download')
  await page.click('button:has-text("Export my data")')
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('fishing-forecast-data.json')

  await page.click('button:has-text("Delete my account")')
  await page.fill('#delete-password', 'GoodPass1')
  await page.fill('#delete-confirm', 'DELETE')
  await page.click('button:has-text("Permanently delete my account")')

  await expect(page).toHaveURL('/')

  // the account is gone — logging back in fails
  await page.goto('/login')
  await page.fill('#email', 'e2e-account@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.click('button[type="submit"]')
  await expect(page.getByText(/incorrect email or password/i)).toBeVisible()
})
