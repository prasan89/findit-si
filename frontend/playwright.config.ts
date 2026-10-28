import { defineConfig } from '@playwright/test'
import path from 'path'

const E2E_DATA_DIR = path.join(__dirname, 'e2e', '.e2e-data')
const BACKEND_PY = path.join(__dirname, '..', 'backend', '.venv', 'bin', 'python')

/**
 * Hackathon E2E: full-stack journeys against local servers with a seeded,
 * deterministic backend (mock model fallback — no API key, no live calls).
 *
 * Uses `next dev` (not `next start`) because the production bundle resolves
 * API rewrites at build time, while E2E must point at its own seeded backend
 * on port 8123. The production bundle itself is validated separately with
 * `npm run build`. Run: `npm run test:e2e`. Uses installed Google Chrome.
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: '*.spec.ts',
  timeout: 120000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  globalSetup: './e2e/global-setup.ts',
  use: {
    baseURL: 'http://localhost:3100',
    // E2E runs on non-standard ports (3100/8123) to avoid colliding with dev
    // servers; the backend's CORS allowlist covers only :3000, and the
    // Next.js trailing-slash redirect + FastAPI slash redirect chain ends in
    // an absolute-URL hop that the test browser would otherwise block. The
    // flag below relaxes same-origin policy in the TEST browser only so the
    // full production rewrite/redirect chain still executes end to end.
    // The backend's own CORS allowlist is pinned by backend CORS tests.
    launchOptions: { channel: 'chrome', args: ['--disable-web-security'] },
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: `${BACKEND_PY} -m uvicorn app.main:app --port 8123`,
      cwd: '../backend',
      env: { DATA_DIR: E2E_DATA_DIR },
      url: 'http://localhost:8123/api/recordings/',
      reuseExistingServer: !process.env.CI,
      timeout: 60000,
    },
    {
      command: 'npm run dev -- -p 3100',
      env: { NEXT_PUBLIC_API_URL: 'http://localhost:8123' },
      url: 'http://localhost:3100',
      reuseExistingServer: !process.env.CI,
      timeout: 180000,
    },
  ],
})
