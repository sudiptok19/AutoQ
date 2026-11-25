import { expect, test, type Page } from '@playwright/test'
import { pickFixture, selectByPrefix, signIn } from '../helpers'

// Each spec inspects its own unpacked serial, so verdicts here never touch the packages the dock story loads.
async function inspect(page: Page, serial: string, fixture: string) {
  await selectByPrefix(page.getByLabel('Lot'), serial.slice(0, 8))
  await selectByPrefix(page.getByLabel('Serial'), serial)
  await pickFixture(page, fixture)
  const go = page.getByRole('button', { name: 'Inspect', exact: true })
  await expect(go).toBeEnabled()
  await go.click()
}

test.beforeEach(async ({ page }) => { await signIn(page, 'op.ravi', '/inspect') })

test('a good part passes and gets a signed passport, labelled synthetic', async ({ page }) => {
  await inspect(page, 'L-2611-A-0031', 'Good part')
  await expect(page.getByText('Passport issued and signed.')).toBeVisible()
  await expect(page.getByText('PASS', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('SYNTHETIC', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('RAW SCORE')).toBeVisible()          // anomaly score, not a probability
  await expect(page.getByText(/Quality passport · ACTIVE/)).toBeVisible()
})

test('a cracked part is rejected with no shipment identity', async ({ page }) => {
  await inspect(page, 'L-2611-A-0032', 'Cracked bore')
  await expect(page.getByText('Rejected. No shipment identity.')).toBeVisible()
  await expect(page.getByText(/Quality passport/)).toHaveCount(0)
})

test('a blurry capture fails the quality gate and becomes HOLD', async ({ page }) => {
  await inspect(page, 'L-2611-A-0033', 'Motion blur')
  await expect(page.getByText('Needs quality-engineer review. No release.')).toBeVisible()
  await expect(page.getByText('IMAGE_QUALITY_BLUR').first()).toBeVisible()
})

test('under active 4M containment a passing part gets no passport, and the screen says why', async ({ page }) => {
  await inspect(page, 'L-2613-D-0002', 'Good part')
  await expect(page.getByText('Passed, but no passport issued yet.')).toBeVisible()
  await expect(page.getByText('Why no passport')).toBeVisible()
  await expect(page.getByText('CONTAINMENT_ACTIVE').first()).toBeVisible()
  await expect(page.getByText('Passport issued and signed.')).toHaveCount(0)
})
