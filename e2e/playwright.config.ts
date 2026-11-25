import { defineConfig, devices } from '@playwright/test'

/**
 * Two projects:
 *  - live: the real FastAPI backend + PostgreSQL, reseeded with the demo story first (needs `make db`).
 *  - mock: the in-browser mock API only, no backend; a smoke pass that runs anywhere.
 * The live specs share one database and tell one story in order, so everything runs serially.
 */
const API_PORT = process.env.AUTOQ_API_PORT ?? '8010'
const LIVE_PORT = 5180
const MOCK_PORT = 5181
// `--project=mock` alone needs no API or database, so don't start them.
const MOCK_ONLY = process.argv.some(a => a === '--project=mock') && !process.argv.some(a => /--project=(live|seed)/.test(a))

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'seed', testMatch: /seed\.setup\.ts/ },
    { name: 'live', testMatch: /live\/.*\.spec\.ts/, dependencies: ['seed'], use: { baseURL: `http://localhost:${LIVE_PORT}` } },
    { name: 'mock', testMatch: /mock\/.*\.spec\.ts/, use: { baseURL: `http://localhost:${MOCK_PORT}` } },
  ],
  webServer: [
    ...(MOCK_ONLY ? [] : [{
      command: `uv run --directory ../backend uvicorn app.main:app --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/api/v1/health`,
      reuseExistingServer: true, timeout: 60_000,
    }, {
      command: `npm --prefix ../frontend run dev -- --port ${LIVE_PORT} --strictPort`,
      env: { VITE_API_MODE: 'live', AUTOQ_API_PORT: API_PORT },
      url: `http://localhost:${LIVE_PORT}`, reuseExistingServer: true, timeout: 60_000,
    }]),
    {
      command: `npm --prefix ../frontend run dev -- --port ${MOCK_PORT} --strictPort`,
      env: { VITE_API_MODE: 'mock' },
      url: `http://localhost:${MOCK_PORT}`, reuseExistingServer: true, timeout: 60_000,
    },
  ],
})
