import { expect, test, type Page } from '@playwright/test'
import { pickFixture, signIn } from '../helpers'

const crate = (page: Page, label: string) => page.getByTestId(`crate-${label}`)
const verdict = (page: Page) => page.getByText(/^LOAD (ACCEPTED|BLOCKED)$/)

test.describe.configure({ mode: 'serial' })

test.beforeEach(async ({ page }) => {
  await signIn(page, 'disp.kiran', '/dispatch')
  await expect(page.getByRole('combobox').first().locator('option:checked')).toHaveText(/^SHP-0004/)   // default: the first OPEN shipment
})

test('a package with only its enrollment photo is refused until the seal is rechecked', async ({ page }) => {
  await crate(page, 'PKG-0005').getByRole('button', { name: 'Scan' }).click()
  await expect(verdict(page)).toHaveText('LOAD BLOCKED')
  await expect(page.getByText('SEAL_RECHECK_REQUIRED').first()).toBeVisible()
})

test('a torn seal is refused', async ({ page }) => {
  await crate(page, 'PKG-0006').getByRole('button', { name: 'Scan' }).click()
  await expect(verdict(page)).toHaveText('LOAD BLOCKED')
})

test('a forged label is refused by the signature check', async ({ page }) => {
  await page.getByRole('button', { name: /Forged QR/ }).click()
  await expect(verdict(page)).toHaveText('LOAD BLOCKED')
})

test('a ready package loads; a network retry replays the same decision; a re-scan is a duplicate', async ({ page }) => {
  await crate(page, 'PKG-0004').getByRole('button', { name: 'Scan' }).click()
  await expect(verdict(page)).toHaveText('LOAD ACCEPTED')
  await expect(page.getByText(/units · 1 pkgs/)).toBeVisible()

  await page.getByRole('button', { name: /Network retry/ }).click()
  await expect(page.getByText('REPLAY · original decision returned')).toBeVisible()
  await expect(verdict(page)).toHaveText('LOAD ACCEPTED')

  await page.getByRole('button', { name: /Re-scan loaded package/ }).click()
  await expect(verdict(page)).toHaveText('LOAD BLOCKED')
  await expect(page.getByText(/ALREADY_LOADED|DUPLICATE_LOAD/).first()).toBeVisible()
})

test('after an intact recheck the held-back package loads', async ({ page }) => {
  await crate(page, 'PKG-0005').getByRole('button', { name: /Seal/ }).click()
  const modal = page.getByRole('dialog')
  const scope = (await modal.count()) ? modal : page
  await pickFixture(scope, 'Clean recapture')
  await page.getByRole('button', { name: /Compare with enrolled reference/ }).click()
  await expect(page.getByText('Valid for loading for 60 minutes.')).toBeVisible()
  await page.getByRole('button', { name: 'Done' }).click()

  await crate(page, 'PKG-0005').getByRole('button', { name: 'Scan' }).click()
  await expect(verdict(page)).toHaveText('LOAD ACCEPTED')
  // Every attempt, accepted or blocked, is in the persisted log.
  await expect(page.getByText(/Scan log · SHP-0004/)).toBeVisible()
  await expect(page.getByRole('button', { name: /SEAL_RECHECK_REQUIRED/ }).first()).toBeVisible()
})
