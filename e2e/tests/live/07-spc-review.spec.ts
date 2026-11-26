import { expect, test } from '@playwright/test'
import { signIn } from '../helpers'

// A3: a drift alert opens a containment scope review. The demo's inspections are SYNTHETIC, so the honest answer is
// "insufficient evidence": nothing can be approved, and a manager records why no containment follows.
test('a synthetic drift alert is investigated, found unsupported, and rejected with a reason', async ({ browser }) => {
  const qe = await (await browser.newContext()).newPage()
  await signIn(qe, 'qe.ananya', '/spc')
  await qe.getByRole('button', { name: 'Investigate scope' }).click()
  const panel = qe.getByText(/SPC containment review/).locator('xpath=ancestor::*[contains(@class,"rounded")][1]')
  await expect(panel).toContainText('insufficient evidence')
  await expect(panel).toContainText(/non production source · \d+/)
  await expect(panel).toContainText('SYNTHETIC DATA NEVER SUPPORTS CONTAINMENT')
  await expect(panel).toContainText('0 independent exposure links')
  await expect(qe.getByRole('button', { name: 'Investigate scope' })).toHaveCount(0)   // one review per alert

  const qm = await (await browser.newContext()).newPage()
  await signIn(qm, 'qm.farhan', '/spc')
  await expect(qm.getByRole('button', { name: /Approve 0 lots/ })).toBeDisabled()
  await qm.getByLabel('Decision reason (audit)').fill('Synthetic demo data only; no production evidence of drift')
  await qm.getByRole('button', { name: 'Reject', exact: true }).click()
  await expect(qm.getByText(/Rejected by qm\.farhan/)).toBeVisible()
})
