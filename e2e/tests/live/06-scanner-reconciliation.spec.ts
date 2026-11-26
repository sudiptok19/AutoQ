import { expect, test, type Page } from '@playwright/test'
import { selectByPrefix, signIn } from '../helpers'

// The phone and the control room, mounted side by side. The e2e seed stages SHP-0005 (SO-1005) with PKG-0007 and PKG-0008
// for this spec only, so nothing here disturbs the dock story on SHP-0004.
const SHIP = 'SHP-0005', MINE = 'PKG-0007', CONTESTED = 'PKG-0008'

async function dock(page: Page) {
  await selectByPrefix(page.getByRole('combobox').first(), SHIP)
  await expect(page.getByTestId(`crate-${MINE}`).or(page.getByText(/units · \d+ pkgs/)).first()).toBeVisible()
}

test('offline loads reconcile on a mounted scanner: acceptance, conflict, verification, settlement, close', async ({ browser }) => {
  const phone = await (await browser.newContext({ viewport: { width: 412, height: 915 } })).newPage()
  await signIn(phone, 'disp.kiran', '/scanner')
  await phone.getByRole('button', { name: 'Setup' }).click()
  await phone.getByRole('button', { name: new RegExp(SHIP) }).click()
  await phone.getByRole('button', { name: /Download signed snapshot/ }).click()
  await expect(phone.getByText(/Snapshot verified \(kid aq-e2e-1\)/)).toBeVisible()      // the suite's own signing key
  await phone.getByRole('checkbox').check()                                              // the dock loses signal

  await phone.getByRole('button', { name: 'Scan', exact: true }).click()
  for (const label of [MINE, CONTESTED]) {
    await phone.getByRole('button', { name: label, exact: true }).click()
    await expect(phone.getByText('PROVISIONAL · LOAD')).toBeVisible()
    if (label === MINE) await phone.getByText('tap to scan next').click()
  }
  await expect(phone.getByText('unsynced events')).toBeVisible()

  // Meanwhile the control room loads PKG-0008 online: the phone's provisional load of it can no longer stand.
  const room = await (await browser.newContext()).newPage()
  await signIn(room, 'disp.kiran', '/dispatch')
  await dock(room)
  await room.getByTestId(`crate-${CONTESTED}`).getByRole('button', { name: 'Scan' }).click()
  await expect(room.getByText('LOAD ACCEPTED')).toBeVisible()

  // Signal returns; one sync.
  await phone.getByRole('button', { name: 'Setup' }).click()
  await phone.getByRole('checkbox').uncheck()
  await phone.getByRole('button', { name: 'Queue' }).click()
  await phone.getByRole('button', { name: /Sync 2 event/ }).click()
  await expect(phone.getByText('Synced 2 event(s) · 1 conflict(s) need physical action')).toBeVisible()
  await expect(phone.getByText(/Physical action:.*ALREADY_LOADED.*PKG-0008/)).toBeVisible()
  await phone.getByRole('button', { name: 'Scan', exact: true }).click()
  await expect(phone.getByText('PROVISIONAL · LOAD')).toHaveCount(0)                    // the device's guess is gone
  await expect(phone.getByText(/4\/4/)).toBeVisible()                                     // server-confirmed: PKG-0007 + PKG-0008

  // The control room sees what still blocks the truck: this phone's authorization and the contested crate.
  await room.reload(); await dock(room)
  const panel = room.getByText('Open offline authorizations and physical tasks').locator('xpath=ancestor::*[contains(@class,"rounded")][1]')
  await expect(panel).toContainText('disp.kiran')
  await expect(panel).toContainText(CONTESTED)
  await room.getByRole('button', { name: 'Close manifest' }).click()
  await expect(room.getByText('MANIFEST_NOT_RECONCILED')).toBeVisible()

  // A dispatcher may not "unload" a crate another accepted scan put on the truck; a QE verifies instead.
  await room.getByRole('button', { name: 'Record disposition…' }).click()
  await room.getByLabel('What was physically done (audit)').fill('unloaded')
  await room.getByRole('button', { name: 'Record disposition', exact: true }).click()
  await expect(room.getByText('LOCATION_CONFLICT_REVIEW')).toBeVisible()
  const qe = await (await browser.newContext()).newPage()
  await signIn(qe, 'qe.ananya', '/dispatch')
  await dock(qe)
  await qe.getByRole('button', { name: 'Record disposition…' }).click()
  await qe.getByLabel('What was physically done (audit)').fill('Verified at Dock 5: one crate, loaded by the control-room scan')
  await qe.getByRole('button', { name: 'Record disposition', exact: true }).click()
  await expect(qe.getByText('Record disposition…')).toHaveCount(0)

  // The phone hands back its authorization; only then can the manifest close.
  await phone.getByRole('button', { name: 'Setup' }).click()
  await phone.getByRole('button', { name: 'End offline session' }).click()
  await expect(phone.getByText('Offline session ended.')).toBeVisible()
  await room.reload(); await dock(room)
  await expect(room.getByText('Open offline authorizations and physical tasks')).toHaveCount(0)
  await room.getByRole('button', { name: 'Close manifest' }).click()
  await expect(room.getByRole('button', { name: 'Record departure' })).toBeVisible()
})
