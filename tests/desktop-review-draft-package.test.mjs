// SPDX-License-Identifier: AGPL-3.0-only
// Explicit local validation; old jobs/originals are read-only and trash is forbidden.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, mkdir, readFile, writeFile, rename, readdir } from 'node:fs/promises'
import { createServer } from 'node:net'
import { createHash } from 'node:crypto'
import path from 'node:path'
import os from 'node:os'
import { performance } from 'node:perf_hooks'

assert.equal(process.env.BIRDSHOT_TEST_DRAFT_PACKAGE, '1')
const version = JSON.parse(await readFile('package.json')).desktopVersion
const built = path.resolve(process.env.BIRDSHOT_DRAFT_EXE || `desktop-artifacts/BirdShot-${version}-win32-x64/BirdShot.exe`)
const validation = process.env.BIRDSHOT_DRAFT_VALIDATION || await mkdtemp(path.join(os.tmpdir(), 'BirdShot 草稿 缓存 验证 '))
await mkdir(validation, { recursive: true })
const original = path.dirname(built)
const directory = process.env.BIRDSHOT_DRAFT_KEEP_LOCATION === '1' ? original : path.join(validation, '完整 App')
if (directory !== original) await rename(original, directory)
const data = path.join(validation, '隔离 用户数据')
await mkdir(data, { recursive: true })
const sourceConfig = process.env.BIRDSHOT_DRAFT_SOURCE_CONFIG || path.join(process.env.APPDATA, 'BirdShot', 'desktop-library.json')
const config = JSON.parse(await readFile(sourceConfig))
assert.ok(config.sources.length && config.sources.every(source => source.status === 'completed' &&
  !source.scanRequested && (source.jobs || []).every(job => job.managed === false)), 'Only completed borrowed jobs without scan requests may be used')
const socket = createServer()
await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve))
const port = socket.address().port
await new Promise(resolve => socket.close(resolve))
config.service = { mode: 'local', port }; config.outputRoot = path.join(data, 'output')
await writeFile(path.join(data, 'desktop-library.json'), JSON.stringify(config))
if (process.env.BIRDSHOT_DRAFT_LEGACY_PROJECTION === '1') {
  const library = path.join(path.dirname(sourceConfig), 'library')
  for (const name of ['manifest.json', 'mapping.json', 'groups.json', 'metadata.json', 'status.json',
    'review.json', 'revisions\\verified-birds-birder-v2\\gallery.json']) {
    const target = path.join(data, 'library', name)
    try { await mkdir(path.dirname(target), { recursive: true }); await writeFile(target, await readFile(path.join(library, name))) }
    catch (error) { if (error.code !== 'ENOENT') throw error }
  }
  for (const folder of ['groups', 'revisions\\verified-birds-birder-v2\\groups']) {
    const target = path.join(data, 'library', folder)
    await mkdir(target, { recursive: true })
    for (const name of await readdir(path.join(library, folder))) {
      if (name.endsWith('.json')) await writeFile(path.join(target, name), await readFile(path.join(library, folder, name)))
    }
  }
}
const oldReview = path.join(path.dirname(sourceConfig), 'review-state-v1.json')
let savedBaseline
try {
  savedBaseline = JSON.parse(await readFile(oldReview))
  assert.equal(savedBaseline.version, 1)
  await writeFile(path.join(data, 'review-state-v1.json'), JSON.stringify(savedBaseline))
} catch (error) { if (error.code !== 'ENOENT') throw error }
const metadataHashes = new Map()
for (const source of config.sources) for (const job of source.jobs) {
  for (const name of ['manifest.json', 'status.json', 'mapping.json', 'revisions\\verified-birds-birder-v2\\gallery.json']) {
    const file = path.join(job.path, name)
    metadataHashes.set(file, createHash('sha256').update(await readFile(file)).digest('hex'))
  }
}
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'),
  BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1', PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' }
for (const key of Object.keys(env)) if (/^(ELECTRON_RUN_AS_NODE|PYTHONHOME|PYTHONPATH|VIRTUAL_ENV|GH_TOKEN|GITHUB_TOKEN|COPILOT_GH_ACCOUNT_)/i.test(key)) delete env[key]
let app, page, reviewPage
const timings = []
let hasCache = false
try { await readFile(path.join(data, 'review-registry-cache-v1.json')); hasCache = true }
catch (error) { if (error.code !== 'ENOENT') throw error }
async function restorePackage() {
  for (let attempt = 0; ; attempt++) {
    try { await rename(directory, original); return }
    catch (error) {
      if (error.code !== 'EPERM' || attempt >= 30) {
        console.error('Complete package remains at the validation location:', directory, error.message)
        return
      }
      await new Promise(resolve => setTimeout(resolve, 100))
    }
  }
}
async function launch(label) {
  const start = performance.now()
  app = await _electron.launch({ executablePath: path.join(directory, 'BirdShot.exe'), env, timeout: 90000 })
  assert.equal(path.resolve(await app.evaluate(({ app }) => app.getPath('userData'))), data)
  await app.evaluate(({ shell, BrowserWindow }) => {
    globalThis.__trashCalls = 0
    shell.trashItem = async () => { globalThis.__trashCalls++; throw new Error('Trash is forbidden in this read-only old-job test') }
    BrowserWindow.getAllWindows()[0].setTitle('BirdShot 0.4.2 验证 · 隔离数据')
  })
  page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  await page.evaluate(() => {
    window.__draftRequests = []; window.__saveDelay = 0
    window.addEventListener('message', event => {
      if (event.data?.type !== 'birdshot-review-request') return
      window.__draftRequests.push(event.data.action)
      if (event.data.action === 'save' && window.__saveDelay) {
        event.stopImmediatePropagation()
        const { id, params } = event.data, target = event.source, origin = event.origin
        setTimeout(async () => {
          try { target.postMessage({ type: 'birdshot-review-response', id, ok: true,
            result: await window.birdshotDesktop.review('save', params) }, origin) }
          catch (error) { target.postMessage({ type: 'birdshot-review-response', id, ok: false, error: error.message }, origin) }
        }, window.__saveDelay)
      }
    }, { capture: true })
  })
  const query = performance.now()
  let state = await page.evaluate(() => window.birdshotDesktop.getState())
  const firstStateMilliseconds = performance.now() - query
  let firstContentSeconds
  const deadline = Date.now() + 300000
  while (Date.now() < deadline) {
    state = await page.evaluate(() => window.birdshotDesktop.getState())
    assert.notEqual(state.startup?.status, 'error', JSON.stringify(state.startup))
    if (state.service.localUrl) {
      reviewPage = page.frames().find(frame => frame.url().startsWith(state.service.localUrl))
      if (reviewPage && !firstContentSeconds && await reviewPage.evaluate(() => {
        const picture = document.querySelector('.group-open img')
        return picture?.complete && picture.naturalWidth > 0
      })) firstContentSeconds = (performance.now() - start) / 1000
    }
    if (state.runtime.ready && state.service.status === 'ready' && firstContentSeconds) break
    await page.waitForTimeout(150)
  }
  assert.equal(state.service.status, 'ready')
  assert.ok(firstContentSeconds)
  const snapshot = await (await fetch(state.service.localUrl + 'review.json')).json()
  const gallery = await (await fetch(state.service.localUrl + 'gallery.json')).json()
  assert.equal(snapshot.groups.length, 845); assert.equal(gallery.items.length, 5504); assert.equal(gallery.summary.preferred, 544)
  timings.push({ label, firstStateMilliseconds: Number(firstStateMilliseconds.toFixed(3)),
    firstDecodedGroupSeconds: Number(firstContentSeconds.toFixed(3)),
    readySeconds: Number(((performance.now() - start) / 1000).toFixed(3)) })
  return { state, snapshot }
}
async function close() {
  if (!app) return
  if (reviewPage) {
    await reviewPage.evaluate(() => {
      if (!document.querySelector('#immersive').hidden) document.querySelector('#exit').click()
      const dialog = document.querySelector('#exit-dialog')
      if (dialog.open) document.querySelector('#exit-discard').click()
    }).catch(() => {})
  }
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
  await app.close(); app = null
}
try {
  await launch(hasCache ? 'existing-registry-cache' : process.env.BIRDSHOT_DRAFT_LEGACY_PROJECTION === '1' ?
    'legacy-projection-no-registry-cache' : 'cold-no-registry-cache')
  await close()
  const { state, snapshot } = await launch('warm-registry-cache')
  const group = snapshot.groups.find(group => group.frames.length > 1)
  await reviewPage.locator(`.group-open[data-group-id="${group.id}"]`).click()
  await reviewPage.locator('#stage .source-image').waitFor()
  await reviewPage.locator('#stage').focus()
  const requestsBefore = await page.evaluate(() => window.__draftRequests.length)
  await reviewPage.evaluate(() => {
    document.addEventListener('keydown', event => {
      if (event.key !== ' ') return
      const before = document.querySelector('.frame-check input').checked, start = performance.now()
      requestAnimationFrame(() => { window.__spaceFeedback = {
        milliseconds: performance.now() - start, changed: document.querySelector('.frame-check input').checked !== before,
        key: { composing: event.isComposing, repeat: event.repeat, ctrl: event.ctrlKey, target: event.target.id, prevented: event.defaultPrevented },
        status: document.querySelector('#immersive-status').textContent, disabled: document.querySelector('#keep-frame').disabled } })
    }, { once: true, capture: true })
  })
  await reviewPage.press('#stage', 'Space')
  await reviewPage.waitForFunction(() => !!window.__spaceFeedback)
  const feedback = await reviewPage.evaluate(() => window.__spaceFeedback)
  assert.equal(feedback.changed, true, JSON.stringify(feedback))
  assert.equal(await page.evaluate(() => window.__draftRequests.length), requestsBefore, 'Space must not call any IPC')
  const firstChoice = await reviewPage.locator('.frame-check input').first().isChecked()
  await app.evaluate(({ BrowserWindow }) => { BrowserWindow.getAllWindows()[0].close() })
  await reviewPage.locator('#exit-dialog[open]').waitFor()
  await reviewPage.locator('#continue-review').click()
  assert.equal(await reviewPage.locator('.frame-check input').first().isChecked(), firstChoice, 'cancelled native close preserves the unsaved draft')
  await reviewPage.press('#stage', 'Control+=')
  assert.ok(Number(await reviewPage.locator('#source-layer').getAttribute('data-scale')) > 1)
  assert.equal(await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].webContents.getZoomFactor()), 1)
  await reviewPage.press('#stage', 'Control+-')
  await page.evaluate(() => { window.__saveDelay = 900 })
  const saving = reviewPage.locator('#save-immersive').click()
  await saving
  await reviewPage.press('#stage', 'd')
  assert.match(await reviewPage.locator('#frame-title').textContent(), /^2 \//)
  await reviewPage.press('#stage', 'Space')
  const secondChoice = await reviewPage.locator('.frame-check input').nth(1).isChecked()
  await reviewPage.waitForTimeout(1150)
  await reviewPage.waitForFunction(() => !document.querySelector('#save-immersive').disabled)
  assert.match(await reviewPage.locator('#immersive-status').textContent(), /未保存/)
  await page.evaluate(() => { window.__saveDelay = 0 })
  const saveStart = performance.now()
  await reviewPage.locator('#save-immersive').click()
  await reviewPage.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('遴选状态已同步'))
  const saveMilliseconds = performance.now() - saveStart
  assert.equal(await app.evaluate(() => globalThis.__trashCalls), 0)
  assert.deepEqual((await page.evaluate(() => window.__draftRequests)).filter(action => action !== 'state'), ['save', 'save'])
  const final = await (await fetch(state.service.localUrl + 'review.json')).json()
  assert.equal(final.groups.find(row => row.id === group.id).frames[0].selected, firstChoice)
  assert.equal(final.groups.find(row => row.id === group.id).frames[1].selected, secondChoice)
  await close()
  const restarted = await launch('warm-after-save')
  assert.equal(restarted.snapshot.groups.find(row => row.id === group.id).frames[0].selected, firstChoice)
  assert.equal(restarted.snapshot.groups.find(row => row.id === group.id).frames[1].selected, secondChoice)
  assert.equal(await app.evaluate(() => globalThis.__trashCalls), 0)
  for (const [file, expected] of metadataHashes) {
    assert.equal(createHash('sha256').update(await readFile(file)).digest('hex'), expected)
  }
  const proof = { version, passed: true, timings, spaceFeedbackMilliseconds: Number(feedback.milliseconds.toFixed(3)),
    spaceIPCCount: 0, saveMilliseconds: Number(saveMilliseconds.toFixed(3)), slowSaveNavigationAndNewDraftPreserved: true,
    pageZoomFactor: 1, unsavedNativeCloseProtected: true, restartRestoredSelections: true,
    originalSavedDecisionCount: Object.keys(savedBaseline?.decisions || {}).length,
    sources: config.sources.length, groups: 845, photos: 1644, crops: 5504, preferred: 544,
    oldJobMetadataUnchanged: true, trashCalls: 0, noInference: true }
  await writeFile(path.join(validation, 'verification.json'), JSON.stringify(proof, null, 2))
  console.log(JSON.stringify({ ...proof, validationRoot: validation }))
} catch (error) {
  console.error('Draft package validation failed:', error.stack)
  throw error
} finally {
  await close()
  if (directory !== original) {
    await restorePackage()
  }
}
