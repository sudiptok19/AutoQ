import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { REVIEW_PDF, signIn } from '../helpers'

test.describe.configure({ mode: 'serial' })

let recallPath = ''
let reportPath = ''

test('previewing a heat recall writes nothing; executing it quarantines atomically', async ({ page }) => {
  await signIn(page, 'qm.farhan', '/trace?q=H-7781')
  await page.getByRole('button', { name: 'Preview impact' }).click()
  await expect(page.getByText('(PostgreSQL)')).toBeVisible()
  for (const note of [/packages · gate will refuse/, 'notify & intercept — cannot block', 'customer notification only']) await expect(page.getByText(note)).toBeVisible()

  const go = page.getByRole('button', { name: /Quarantine \d+ units atomically/ })
  await expect(go).toBeDisabled()                                        // a reason is mandatory
  await page.getByLabel('Quarantine reason (audit)').fill('e2e: supplier reported out-of-spec nodularity on H-7781')
  await go.click()
  await expect(page.getByText(/Quarantine executed by qm\.farhan/)).toBeVisible()

  await page.getByRole('link', { name: /Physical tasks & release/ }).click()
  await expect(page).toHaveURL(/\/recalls\/.+/)
  recallPath = new URL(page.url()).pathname
  await expect(page.getByText('QUARANTINED').first()).toBeVisible()
})

test('physical tasks are recorded with a reason, then a manager releases the quarantine', async ({ page }) => {
  test.skip(!recallPath, 'needs the executed recall')
  await signIn(page, 'qm.farhan', recallPath)
  const header = page.getByText(/Segregation tasks · \d+ open/)
  await expect(header).toBeVisible()
  const before = Number((await header.textContent())!.match(/(\d+) open/)![1])
  expect(before).toBeGreaterThan(0)                                      // H-7781 stock is in transit and delivered

  await page.getByRole('button', { name: 'Mark done…' }).first().click()
  await page.getByPlaceholder(/e\.g\./).first().fill('e2e: called the consignee, quarantine confirmed at receiving')
  await page.getByRole('button', { name: 'Record as done' }).click()
  await expect(page.getByText(`Segregation tasks · ${before - 1} open`)).toBeVisible()
  await expect(page.getByText(/Done by qm\.farhan/).first()).toBeVisible()

  await page.getByLabel('Release reason (audit)').fill('e2e: supplier lab retest of H-7781 passed')
  await page.getByRole('button', { name: /^Release H-7781/ }).click()
  await expect(page.getByText('RELEASED').first()).toBeVisible()
  await expect(page.getByText(/were held · hold lifted/)).toBeVisible()
})

test('an 8D drafted from the recall labels every statement and cites its evidence', async ({ page }) => {
  test.skip(!recallPath, 'needs the executed recall')
  await signIn(page, 'qm.farhan', recallPath)
  await page.getByRole('button', { name: 'Draft 8D from this recall' }).click()
  await expect(page).toHaveURL(/\/reports\/.+/)
  reportPath = new URL(page.url()).pathname
  for (const kind of ['Fact', 'Needs a person']) await expect(page.getByText(kind, { exact: true }).first()).toBeVisible()
  await expect(page.getByText('DRAFT').first()).toBeVisible()
})

test('approval needs an uploaded review record; the PDF downloads', async ({ page }) => {
  test.skip(!reportPath, 'needs the drafted 8D')
  await signIn(page, 'qm.farhan', reportPath)
  const approve = page.getByRole('button', { name: 'Approve 8D' })
  await expect(approve).toBeDisabled()
  await page.locator('input[type=file]').setInputFiles(REVIEW_PDF)
  await page.getByLabel('Approval reason (audit)').fill('e2e: cross-functional review, root cause confirmed by lab retest')
  await approve.click()
  await expect(page.getByText(/By qm\.farhan · /)).toBeVisible()
  await expect(page.getByText('APPROVED').first()).toBeVisible()

  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: /PDF of this revision/ }).click()])
  expect(dl.suggestedFilename()).toMatch(/^8D-.+\.pdf$/)
  expect(readFileSync((await dl.path())!).subarray(0, 5).toString()).toBe('%PDF-')
})

test('the audit chain still verifies after the whole story', async ({ page }) => {
  await signIn(page, 'admin', '/audit')
  await page.getByRole('button', { name: /Verify chain/ }).click()
  await expect(page.getByText(/Chain intact · \d+ events/)).toBeVisible()
})
