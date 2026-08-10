import { expect, test } from '@playwright/test'

test('looks up a regulation and checks a specific catch against it', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#email', 'e2e-existing@example.com')
  await page.fill('#password', 'GoodPass1')
  await page.click('button[type="submit"]')
  await expect(page).toHaveURL(/\/dashboard$/)

  await page.click('a:has-text("Regs")')
  await expect(page).toHaveURL(/\/regulations$/)

  await page.fill('#species', 'Red drum')
  await page.selectOption('#state', 'NC')
  await page.click('button:has-text("Look up regulation")')

  await expect(page.getByRole('heading', { name: /red drum — nc/i })).toBeVisible()

  await page.fill('input[placeholder="Length (in)"]', '22')
  await page.click('button:has-text("Check")')

  await expect(page.locator('.card')).toContainText(/legal to keep|too small|too large|unknown/i)
})
