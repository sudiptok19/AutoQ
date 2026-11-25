import { expect, type Locator, type Page } from '@playwright/test'

export type DevUser = 'op.ravi' | 'qe.ananya' | 'qm.farhan' | 'disp.kiran' | 'admin'
const NAMES: Record<DevUser, string> = { 'op.ravi': 'Ravi Kumar', 'qe.ananya': 'Ananya Sen', 'qm.farhan': 'Farhan Ali', 'disp.kiran': 'Kiran Das', admin: 'Admin' }

/** Sign in through the real login screen's quick-role button, then open `path`. */
export async function signIn(page: Page, user: DevUser, path = '/') {
  await page.goto('/')
  await page.getByRole('button', { name: new RegExp(user.replace('.', '\\.')) }).click()
  await expect(page.locator('header').getByText(NAMES[user], { exact: true })).toBeVisible()
  if (path !== '/') await page.goto(path)
}

/** Pick an <option> by the text it starts with (labels carry extra context after the code). */
export async function selectByPrefix(select: Locator, prefix: string) {
  const value = await select.locator('option', { hasText: prefix }).first().getAttribute('value')
  expect(value, `option starting with ${prefix}`).toBeTruthy()
  await select.selectOption(value!)
}

/** Choose a synthetic fixture in the capture widget and wait until the image is ready. */
export async function pickFixture(scope: Page | Locator, label: string) {
  await scope.getByRole('button', { name: new RegExp(`^${label}`) }).click()
  await expect(scope.getByRole('img', { name: 'capture' })).toBeVisible()
}

/** A tiny but genuine PDF, accepted by the server's media sniffing. */
export const REVIEW_PDF = {
  name: 'review-minutes.pdf',
  mimeType: 'application/pdf',
  buffer: Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n'),
}
