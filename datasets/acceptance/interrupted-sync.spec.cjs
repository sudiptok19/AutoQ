
const { createRequire } = require('node:module')
const { join } = require('node:path')
const { expect, test } = createRequire(join(process.env.AUTOQ_ACCEPTANCE_CHECKOUT, 'e2e', 'package.json'))('@playwright/test')
const ship = 'SHP-0005'
const contested = 'PKG-0008'

async function signIn(page, role, displayName, path) {
  await page.goto('/')
  await page.getByRole('button', { name: role }).click()
  await expect(page.locator('header').getByText(displayName, { exact: true })).toBeAttached()
  if (path !== '/') await page.goto(path)
}

async function api(page, path, method = 'GET', body) {
  return page.evaluate(async ({ path, method, body }) => {
    const r = await fetch('/api/v1' + path, {
      method,
      headers: { Authorization: 'Bearer ' + localStorage.getItem('autoq-token'), 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    })
    return { status: r.status, body: await r.json() }
  }, { path, method, body })
}

async function queue(page) {
  return page.evaluate(async () => {
    const scanner = await import('/src/scanner/store.ts')
    return scanner.listQueue()
  })
}

test('retains queue and durable conflict across a pre-send interruption and lost second-batch reply', async ({ browser }) => {
  const admin = await (await browser.newContext()).newPage()
  await signIn(admin, 'admin', 'Admin', '/')
  const active = await api(admin, '/policies')
  expect(active.status).toBe(200)
  const published = await api(admin, '/policies', 'PUT', {
    ...active.body.settings, version: 'acceptance-browser-v2', offline: { chain_writer_version: 2, chain_min_version: 1 },
  })
  expect(published.status).toBe(200)

  const phone = await (await browser.newContext({ viewport: { width: 412, height: 915 }, serviceWorkers: 'block' })).newPage()
  await signIn(phone, 'disp.kiran', 'Kiran Das', '/scanner')
  await phone.getByRole('navigation').getByRole('button', { name: 'Setup', exact: true }).click()
  await phone.getByRole('button', { name: new RegExp(ship) }).click()
  await phone.getByRole('button', { name: /Download signed snapshot/ }).click()
  await expect(phone.getByText(/Snapshot verified \(kid aq-e2e-1\)/)).toBeVisible()
  await expect(phone.getByText('v2', { exact: true })).toBeVisible()
  const current = (await api(phone, '/shipments')).body.items.find(x => x.code === ship)
  expect(current).toBeTruthy()
  await phone.evaluate(async shipment => {
    const scanner = await import('/src/scanner/store.ts')
    // Simulate known no-load ONLINE captures with the real IndexedDB writer.
    for (let i = 1; i < 200; i++) {
      await scanner.appendScan({
        token: 'synthetic-uncaptured-online-' + i, shipment_id: shipment, scanned_at: new Date().toISOString(),
        local_decision: 'ONLINE', local_reasons: [], local_checks: [], package_id: null, package_label: null,
        units: 0, status: 'PENDING',
      })
    }
  }, current.id)
  await phone.getByRole('checkbox').check()
  await phone.getByRole('button', { name: 'Scan', exact: true }).click()
  for (const label of ['PKG-0007', contested]) {
    await phone.getByRole('button', { name: label, exact: true }).click()
    await expect(phone.getByText('PROVISIONAL · LOAD')).toBeVisible()
    if (label !== contested) await phone.getByText('tap to scan next').click()
  }
  expect((await queue(phone)).length).toBe(201)
  const finalCapture = (await queue(phone))[0]

  const room = await (await browser.newContext()).newPage()
  await signIn(room, 'disp.kiran', 'Kiran Das', '/dispatch')
  const picker = room.getByRole('combobox').first()
  await picker.selectOption((await picker.locator('option', { hasText: ship }).getAttribute('value')))
  await room.getByTestId('crate-' + contested).getByRole('button', { name: 'Scan' }).click()
  await expect(room.getByText('LOAD ACCEPTED')).toBeVisible()

  const attempts = []
  const replies = []
  await phone.route('**/api/v1/sync/scans', async route => {
    const next = route.request().postDataJSON().events
    attempts.push(next.map(x => x.seq))
    if (attempts.length === 2) return route.abort('failed') // request never reaches the server
    const response = await route.fetch()
    replies.push(await response.json())
    if (attempts.length === 3) return route.abort('failed') // server committed before the connection failed
    await route.fulfill({ response })
  })
  await phone.getByRole('navigation').getByRole('button', { name: 'Setup', exact: true }).click()
  await phone.getByRole('checkbox').uncheck()
  await phone.getByRole('navigation').getByRole('button', { name: /^Queue/ }).click()
  await phone.getByRole('button', { name: /Sync 201 event/ }).click()
  await expect(phone.getByText(/Synced 200 event.s., then:/)).toBeVisible()
  await expect.poll(async () => (await queue(phone)).filter(x => !x.synced_at).map(x => x.seq)).toEqual([201])
  expect(replies[0].chain.server_seq).toBe(200)
  await expect.poll(async () => (await api(room, '/segregation-tasks')).body.items.filter(t => t.shipment_id === current.id).length).toBe(0)

  await phone.getByRole('button', { name: /Sync 1 event/ }).click()
  await expect.poll(() => replies.length).toBe(2)
  expect(replies[1].results[0].outcome).toBe('CONFLICT')
  const taskId = replies[1].results[0].segregation_task_id
  expect(taskId).toBeTruthy()
  expect(replies[1].chain.server_head).toBe(finalCapture.hash)
  await expect(phone.getByRole('button', { name: /Sync 1 event/ })).toBeEnabled()
  expect((await queue(phone)).filter(x => !x.synced_at).map(x => x.seq)).toEqual([201])

  await phone.getByRole('button', { name: /Sync 1 event/ }).click()
  await expect(phone.getByText(/Physical action:.*ALREADY_LOADED.*PKG-0008/)).toBeVisible()
  await expect.poll(async () => (await queue(phone)).filter(x => !x.synced_at).length).toBe(0)
  expect(attempts).toEqual([Array.from({length: 200}, (_, i) => i + 1), [201], [201], [201]])
  expect(replies[2].chain.server_seq).toBe(201)
  expect(replies[2].chain.server_head).toBe(finalCapture.hash)
  expect(replies[2].results[0]).toMatchObject({ outcome: 'DUPLICATE', original_outcome: 'CONFLICT', segregation_task_id: taskId })
  const tasks = (await api(room, '/segregation-tasks')).body.items.filter(t => t.shipment_id === current.id)
  expect(tasks.map(t => t.id)).toEqual([taskId])
  const shipment = (await api(room, '/shipments/' + current.id)).body
  expect(shipment.loaded_units).toBe(4)
  expect(new Set(shipment.loaded_package_ids).size).toBe(2)
})
