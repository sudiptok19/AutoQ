import { expect, test } from '@playwright/test'
import { pickFixture, signIn } from '../helpers'

// Runs without any backend: the in-browser mock API that mirrors docs/CONTRACT.md.
test('mock mode is labelled and an inspection passes', async ({ page }) => {
  await signIn(page, 'op.ravi', '/inspect')
  await expect(page.getByText('MOCK API').first()).toBeVisible()
  await expect(page.getByLabel('Serial')).toHaveValue(/.+/)
  await pickFixture(page, 'Good part')
  await page.getByRole('button', { name: 'Inspect', exact: true }).click()
  await expect(page.getByText('Passport issued and signed.')).toBeVisible()
})

test('mock mode previews a recall and labels the query as in-browser', async ({ page }) => {
  await signIn(page, 'qm.farhan', '/trace?q=H-7781')
  await page.getByRole('button', { name: 'Preview impact' }).click()
  await expect(page.getByText('(in-browser mock)')).toBeVisible()
  await expect(page.getByText('Blocked at plant', { exact: true })).toBeVisible()
})
