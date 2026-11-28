
const { createRequire } = require('node:module')
const { join } = require('node:path')

const env = process.env
const checkout = env.AUTOQ_ACCEPTANCE_CHECKOUT
if (!checkout || !env.AUTOQ_ACCEPTANCE_RESULTS ||
    !/^http:\/\/127\.0\.0\.1:\d+$/.test(env.AUTOQ_ACCEPTANCE_URL ?? '') ||
    !/^[a-f0-9]{32}$/.test(env.AUTOQ_ACCEPTANCE_NONCE ?? '')) {
  throw new Error('Start the isolated services using run.py --group browser')
}
const { defineConfig, devices } = createRequire(join(checkout, 'e2e', 'package.json'))('@playwright/test')
module.exports = defineConfig({
  testDir: __dirname,
  testMatch: 'interrupted-sync.spec.cjs',
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  reporter: 'list',
  outputDir: env.AUTOQ_ACCEPTANCE_RESULTS,
  use: { ...devices['Desktop Chrome'], baseURL: env.AUTOQ_ACCEPTANCE_URL, serviceWorkers: 'block' },
})
