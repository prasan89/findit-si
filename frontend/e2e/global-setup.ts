import { execFileSync } from 'child_process'
import path from 'path'

/**
 * Global setup: rebuild a deterministic backend DATA_DIR before the suite.
 * Fails loudly if a model API key is set — E2E must never spend live credits.
 */
export default async function globalSetup() {
  if (process.env.MODEL_API_KEY || process.env.META_API_KEY) {
    throw new Error('Refusing E2E with MODEL_API_KEY/META_API_KEY set (no live calls in E2E).')
  }
  const frontendDir = path.join(__dirname, '..')
  const dataDir = path.join(__dirname, '.e2e-data')
  const backendPy = path.join(frontendDir, '..', 'backend', '.venv', 'bin', 'python')
  process.env.E2E_DATA_DIR = dataDir
  execFileSync(backendPy, [path.join(__dirname, 'seed_e2e_db.py')], {
    cwd: path.join(frontendDir, '..', 'backend'),
    env: { ...process.env, E2E_DATA_DIR: dataDir },
    stdio: 'inherit',
  })
}

export const E2E_DATA_DIR = process.env.E2E_DATA_DIR ?? ''
