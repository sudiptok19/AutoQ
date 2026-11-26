import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

/**
 * Two projects:
 *  - live: the real FastAPI backend + PostgreSQL, in a DISPOSABLE database replayed with the demo story.
 *  - mock: the in-browser mock API only, no backend; a smoke pass that runs anywhere.
 *
 * Database safety (the suite wipes its database, so it must never be one anybody keeps):
 *  - The API is always started here, on its own port, and never reused: a dev or verification server on :8010 is
 *    never attached to, even if it is running.
 *  - Its DATABASE_URL is set here, not read from `.env`, and must name a local database ending in `_e2e`.
 *    backend/scripts/e2e_database.py drops, recreates, migrates and seeds exactly that database before the API starts.
 *  - With AUTOQ_E2E=1 the API refuses to boot, and the demo reset refuses to run, against any other database.
 * The live specs share that database and tell one story in order, so everything runs serially.
 */
const E2E_DB = process.env.AUTOQ_E2E_DB ?? 'autoq_e2e'
const PG = process.env.AUTOQ_E2E_PG ?? 'autoq:autoq-dev@localhost:5433'
const API_PORT = process.env.AUTOQ_E2E_API_PORT ?? '8019'
const LIVE_PORT = 5180
const MOCK_PORT = 5181
if (!/^[a-z0-9_]+_e2e$/.test(E2E_DB)) throw new Error(`AUTOQ_E2E_DB=${E2E_DB}: the e2e database name must end in _e2e`)

// `--project=mock` alone needs no API or database, so don't start them.
const MOCK_ONLY = process.argv.some(a => a === '--project=mock') && !process.argv.some(a => /--project=live/.test(a))
const apiEnv = {
  AUTOQ_E2E: '1',
  DATABASE_URL: `postgresql+psycopg://${PG}/${E2E_DB}`,
  AUTOQ_EVIDENCE_DIR: fileURLToPath(new URL('./.tmp/evidence', import.meta.url)),
  // Own throwaway signing key: labels minted by the suite can never verify against the dev or verification key.
  AUTOQ_SIGNING_KEY_PATH: fileURLToPath(new URL('./.tmp/e2e-signing.key', import.meta.url)),
  AUTOQ_SIGNING_KID: 'aq-e2e-1',
  CORS_ORIGINS: `http://localhost:${LIVE_PORT}`,
  // Windows consoles default to a legacy code page; the seed log and API output are UTF-8.
  PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
}

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
    { name: 'live', testMatch: /live\/.*\.spec\.ts/, use: { baseURL: `http://localhost:${LIVE_PORT}` } },
    { name: 'mock', testMatch: /mock\/.*\.spec\.ts/, use: { baseURL: `http://localhost:${MOCK_PORT}` } },
  ],
  webServer: [
    ...(MOCK_ONLY ? [] : [{
      // Fresh disposable database first (≈20 s), then the API on it.
      command: `uv run --directory ../backend python scripts/e2e_database.py && uv run --directory ../backend uvicorn app.main:app --port ${API_PORT}`,
      env: apiEnv,
      url: `http://localhost:${API_PORT}/api/v1/health`,
      reuseExistingServer: false, timeout: 180_000, stdout: 'pipe' as const,
    }, {
      command: `npm --prefix ../frontend run dev -- --port ${LIVE_PORT} --strictPort`,
      env: { VITE_API_MODE: 'live', AUTOQ_API_PORT: API_PORT },
      url: `http://localhost:${LIVE_PORT}`, reuseExistingServer: false, timeout: 60_000,
    }]),
    {
      command: `npm --prefix ../frontend run dev -- --port ${MOCK_PORT} --strictPort`,
      env: { VITE_API_MODE: 'mock' },
      url: `http://localhost:${MOCK_PORT}`, reuseExistingServer: true, timeout: 60_000,
    },
  ],
})
