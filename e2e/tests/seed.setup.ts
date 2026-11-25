import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { test as setup } from '@playwright/test'

const backend = fileURLToPath(new URL('../../backend', import.meta.url))

// The live story depends on fresh seal rechecks (60 min validity) and known codes (SHP-0004, PKG-0004…),
// so every live run starts from a clean `make seed-demo`. This wipes the local dev database only;
// seed_demo.py refuses non-local hosts.
setup('reset and seed the demo story', () => {
  setup.setTimeout(180_000)
  try {
    execFileSync('uv', ['run', 'python', 'scripts/seed_demo.py', '--reset'], { cwd: backend, stdio: 'pipe' })
  } catch (e: any) {
    throw new Error(`Demo seed failed. Is PostgreSQL up (make db) and migrated (make migrate)?\n${e.stderr?.toString() ?? e}`)
  }
})
