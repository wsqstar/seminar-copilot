const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')

const artifacts = path.resolve(__dirname, '../artifacts/e2e')
fs.mkdirSync(artifacts, { recursive: true })

async function assertNoHorizontalOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  if (dimensions.scrollWidth > dimensions.clientWidth + 1) {
    throw new Error(`${label} horizontal overflow: ${JSON.stringify(dimensions)}`)
  }
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
  })
  const consoleErrors = []

  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  desktop.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await desktop.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await desktop.locator('.health-state').getByText(/本地转录|Whisper/).waitFor()
  if ((await desktop.locator('.preview-question').count()) !== 6) {
    throw new Error('Expected six prepared questions')
  }
  await assertNoHorizontalOverflow(desktop, 'desktop setup')
  await desktop.screenshot({ path: path.join(artifacts, 'desktop-setup.png'), fullPage: true })

  await desktop.getByRole('button', { name: '载入演示' }).click()
  await desktop.getByRole('heading', { name: '现场转录' }).waitFor()
  await desktop.getByText('Most of the migrant mortality advantage').first().waitFor()
  if ((await desktop.locator('.question-card').count()) !== 6) {
    throw new Error('Expected six live question cards')
  }
  await assertNoHorizontalOverflow(desktop, 'desktop workbench')
  await desktop.screenshot({ path: path.join(artifacts, 'desktop-workbench.png'), fullPage: true })

  await desktop.getByRole('button', { name: '结束录音' }).click()
  await desktop.getByRole('button', { name: '导出到 Obsidian' }).click()
  await desktop.getByText('已导出：').waitFor()

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 } })
  mobile.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await mobile.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  await mobile.locator('.health-state').getByText(/本地转录|Whisper/).waitFor()
  await assertNoHorizontalOverflow(mobile, 'mobile setup')
  await mobile.screenshot({ path: path.join(artifacts, 'mobile-setup.png'), fullPage: true })
  await mobile.getByRole('button', { name: '载入演示' }).click()
  await mobile.getByRole('heading', { name: '现场转录' }).waitFor()
  await assertNoHorizontalOverflow(mobile, 'mobile workbench')
  await mobile.screenshot({ path: path.join(artifacts, 'mobile-workbench.png'), fullPage: true })

  let recordingFlow = false
  const recordingContext = await browser.newContext({ permissions: ['microphone'] })
  const recording = await recordingContext.newPage()
  recording.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await recording.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' })
  const startButton = recording.getByRole('button', { name: '开始录音' })
  await recording.locator('.consent-row input').check()
  if (await startButton.isEnabled()) {
    await startButton.click()
    await recording.getByRole('heading', { name: '现场转录' }).waitFor()
    await recording.waitForTimeout(1_500)
    await recording.getByRole('button', { name: '结束录音' }).click()
    await recording.getByRole('button', { name: '导出到 Obsidian' }).waitFor()
    recordingFlow = true
  }
  await recordingContext.close()

  await browser.close()
  if (consoleErrors.length) {
    throw new Error(`Browser console errors: ${consoleErrors.join(' | ')}`)
  }
  console.log(JSON.stringify({ ok: true, screenshots: 4, exported: true, recordingFlow }))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
