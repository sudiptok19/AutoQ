import { expect, test } from '@playwright/test'
import { signIn } from '../helpers'

test('signs in with the login form and rejects a wrong password', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Password').fill('wrong-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText(/invalid|incorrect|credentials/i)).toBeVisible()

  await page.getByLabel('Password').fill('autoq-dev')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('header').getByText('Ravi Kumar', { exact: true })).toBeVisible()
  await expect(page.getByText('MOCK API')).toHaveCount(0)   // really talking to the backend
})

test('an operator cannot drive the dispatch gate', async ({ page }) => {
  await signIn(page, 'op.ravi', '/dispatch')
  await expect(page.getByText('You are viewing as a non-dispatcher')).toBeVisible()
  await expect(page.getByTestId('crate-PKG-0004').getByRole('button', { name: 'Scan' })).toBeDisabled()
})
