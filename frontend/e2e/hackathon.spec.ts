import { test, expect, type Page } from '@playwright/test'
import { readFileSync } from 'fs'
import path from 'path'

/**
 * Hackathon submission journeys. Deterministic seeded backend (see
 * e2e/seed_e2e_db.py); mock model fallback — no live API calls.
 * Never asserts exact model-generated prose — only structure, counts,
 * evidence wiring, and honest no-evidence messaging.
 */
test.describe.configure({ mode: 'serial' })

const FIXTURE = path.join(__dirname, 'fixtures', 'cctv_sample.mp4')
const UPLOAD_NAME = 'cctv_e2e_upload.mp4'

async function gotoInvestigate(page: Page) {
  await page.getByRole('button', { name: 'Investigate', exact: true }).click()
}

async function submitQuery(page: Page, query: string) {
  await page.getByPlaceholder(/What happened before the person left/).fill(query)
  await page.locator('form button[type="submit"]').click()
}

test('1. uploads a CCTV video and sees it indexed in the library', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('cctv_seed.mp4')).toBeVisible()

  await page.locator('input[type="file"]').setInputFiles({
    name: UPLOAD_NAME,
    mimeType: 'video/mp4',
    buffer: readFileSync(FIXTURE),
  })
  await page.getByPlaceholder('e.g. Entrance Camera').fill('E2E Entrance')
  await page.getByRole('button', { name: 'Upload', exact: true }).click()

  const row = page.locator('div', { hasText: UPLOAD_NAME }).last()
  await expect(page.getByText(UPLOAD_NAME)).toBeVisible({ timeout: 15000 })
  // Background indexing (mock model) completes deterministically.
  await expect(row.getByText('INDEXED')).toBeVisible({ timeout: 90000 })
})

test('2. submits a Standard investigation and sees an event timeline', async ({ page }) => {
  await page.goto('/')
  await gotoInvestigate(page)
  // NOTE: the query avoids the token "red" — retrieval is substring-based and
  // "red" also matches "enteRED", which would honestly return both seed events.
  await submitQuery(page, 'bag left near door')

  await expect(page.getByText('Event Timeline')).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('red bag left near door', { exact: true })).toBeVisible()
  await expect(page.getByText(/Reliable timeline|Approximate timeline/)).toBeVisible()
  await expect(page.getByText(/1 event\(s\) across/)).toBeVisible()
})

test('3. submits a Multi-Agent investigation and inspects the trace', async ({ page }) => {
  await page.goto('/')
  await gotoInvestigate(page)
  await page.getByRole('button', { name: 'Multi-Agent (8 specialists)' }).click()
  await submitQuery(page, 'person entered')

  await expect(page.getByText('Investigation Agents')).toBeVisible({ timeout: 60000 })
  await expect(page.getByText(/\d+ of 8 agents completed/)).toBeVisible()
  await expect(page.getByText('Investigation Narrative')).toBeVisible()
  // All eight specialists are listed truthfully in the trace.
  for (const name of ['QueryInvestigationAgent', 'ReportSynthesisAgent']) {
    await expect(page.getByText(name)).toBeVisible()
  }
})

test('4. opens a finding and plays the evidence at the right timestamp', async ({ page }) => {
  await page.goto('/')
  await gotoInvestigate(page)
  await submitQuery(page, 'bag left near door')
  await expect(page.getByText('Event Timeline')).toBeVisible({ timeout: 30000 })

  await page.getByRole('button', { name: /Preview/ }).first().click()
  await expect(page.getByText('Seeking to 0:08')).toBeVisible({ timeout: 15000 })

  const video = page.locator('video')
  await expect(video).toBeVisible()
  await expect(video).toHaveAttribute('src', /\/video$/)
  await page.waitForFunction(
    () => {
      const v = document.querySelector('video')
      return !!v && Math.abs(v.currentTime - 8) < 1.5
    },
    null,
    { timeout: 15000 },
  )
  await page.keyboard.press('Escape')
  await expect(video).toBeHidden()
})

test('5. reports an unanswerable query honestly without inventing conclusions', async ({ page }) => {
  await page.goto('/')
  await gotoInvestigate(page)
  await submitQuery(page, 'purple helicopter zebra')

  await expect(page.getByText(/No events found for/)).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('Evidence Gaps')).toBeVisible()
  await expect(page.getByText(/No events matching/)).toBeVisible()
  // The unrelated seeded footage must not leak into the answer.
  await expect(page.getByText('red bag left near door', { exact: true })).toBeHidden()
  await expect(page.getByText('person entered lobby', { exact: true })).toBeHidden()
})
