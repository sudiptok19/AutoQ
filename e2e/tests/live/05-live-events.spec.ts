import { expect, test } from '@playwright/test'
import { selectByPrefix, signIn } from '../helpers'

// Two screens, two people: the control-room laptop must learn about a gate event without reloading.
test('a counterfeit scan at the dock reaches the control room in real time', async ({ browser }) => {
  const room = await (await browser.newContext()).newPage()
  const dock = await (await browser.newContext()).newPage()
  await signIn(room, 'qm.farhan', '/')
  await expect(room.getByText('LIVE', { exact: true })).toBeVisible()          // socket authenticated with a ticket

  await signIn(dock, 'disp.kiran', '/dispatch')
  await selectByPrefix(dock.getByRole('combobox').first(), 'SHP-0004')   // waits for the shipment list before scanning
  const paste = dock.getByPlaceholder('Paste any AQ1.… token')
  await paste.fill('AQ1.not-a-real-label')
  await paste.press('Enter')
  await expect(dock.getByText('LOAD BLOCKED')).toBeVisible()

  const toast = room.getByRole('alert').filter({ hasText: 'Counterfeit label suspected' })
  await expect(toast).toBeVisible({ timeout: 5_000 })                          // relay polls every 0.5 s
  await expect(toast).toContainText('TOKEN_MALFORMED')
  await toast.getByRole('link', { name: 'Open →' }).click()
  await expect(room).toHaveURL(/\/dispatch$/)
  await expect(toast).toHaveCount(0)
})

test('the siren can be muted, and the choice survives a reload', async ({ page }) => {
  await signIn(page, 'qm.farhan', '/')
  await page.getByRole('button', { name: 'Mute alert siren' }).click()
  await page.reload()
  await expect(page.getByRole('button', { name: 'Unmute alert siren' })).toBeVisible()
})
