// SPDX-License-Identifier: AGPL-3.0-only
// Explicit package test: copy only configuration, retain old jobs read-only.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, readFile, writeFile, rename } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { performance } from 'node:perf_hooks'

assert.equal(process.env.BIRDSHOT_TEST_STARTUP_PACKAGE, '1')
const version = JSON.parse(await readFile('package.json')).desktopVersion
const builtExecutable = path.resolve(process.env.BIRDSHOT_STARTUP_EXE ||
  `desktop-artifacts/BirdShot-${version}-win32-x64/BirdShot.exe`)
const data = await mkdtemp(path.join(os.tmpdir(), 'BirdShot 启动 旧库 验证 '))
const originalDirectory = path.dirname(builtExecutable)
const directory = process.env.BIRDSHOT_STARTUP_KEEP_LOCATION === '1' ? originalDirectory : path.join(data, '完整 App')
if (directory !== originalDirectory) await rename(originalDirectory, directory)
const executablePath = path.join(directory, 'BirdShot.exe')
const sourceConfig = process.env.BIRDSHOT_STARTUP_SOURCE_CONFIG
async function restorePackage() {
  for (let attempt = 0; ; attempt++) {
    try { await rename(directory, originalDirectory); return }
    catch (error) {
      if (error.code !== 'EPERM' || attempt >= 30) throw error
      await new Promise(resolve => setTimeout(resolve, 100))
    }
  }
}
let original, config
if (sourceConfig) {
  original = await readFile(sourceConfig)
  config = JSON.parse(original)
  assert.equal(config.version, 1)
  assert.ok(config.sources.every(source => source.status === 'completed' &&
    (source.jobs || []).every(job => job.managed === false)), 'Use only completed read-only old-job references')
  config.outputRoot = path.join(data, 'output')
  config.service = { mode: 'local', port: Number(process.env.BIRDSHOT_STARTUP_TEST_PORT || 5377) }
  await writeFile(path.join(data, 'desktop-library.json'), JSON.stringify(config))
} else {
  await writeFile(path.join(data, 'desktop-library.json'), JSON.stringify({ version: 1, sources: [],
    service: { mode: 'local', port: Number(process.env.BIRDSHOT_STARTUP_TEST_PORT || 5377) } }))
}
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'),
  BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1', PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' }
delete env.ELECTRON_RUN_AS_NODE
const start = performance.now()
const app = await _electron.launch({ executablePath, env, timeout: 90000 })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  const queryStart = performance.now()
  const first = await page.evaluate(() => window.birdshotDesktop.getState())
  const firstQuerySeconds = (performance.now() - queryStart) / 1000
  assert.ok(firstQuerySeconds < 2, 'Initial state must be queryable without waiting for old-library initialization')
  assert.equal(first.version, 1)
  const stages = new Set()
  let state = first
  const deadline = Date.now() + 240000
  while (Date.now() < deadline) {
    state = await page.evaluate(() => window.birdshotDesktop.getState())
    if (state.startup?.phase) stages.add(state.startup.phase)
    assert.notEqual(state.startup?.status, 'error', JSON.stringify(state.startup))
    if (state.startup?.status === 'ready' && state.service.status === 'ready') break
    await page.waitForTimeout(500)
  }
  assert.equal(state.startup?.status, 'ready', JSON.stringify(state.startup))
  assert.equal(state.service.status, 'ready')
  const health = await (await fetch(state.service.localUrl + 'health.json')).json()
  assert.equal(health.instanceId, state.instanceId)
  assert.equal(health.status || 'ready', 'ready')
  const gallery = await (await fetch(state.service.localUrl + 'gallery.json')).json()
  if (sourceConfig) {
    assert.equal(state.sources.length, config.sources.length)
    assert.ok(state.sources.every(source => source.status === 'completed'))
    assert.deepEqual(await readFile(sourceConfig), original)
    if (process.env.BIRDSHOT_STARTUP_EXPECT_CROPS) assert.equal(gallery.items.length, Number(process.env.BIRDSHOT_STARTUP_EXPECT_CROPS))
    if (process.env.BIRDSHOT_STARTUP_EXPECT_PREFERRED) assert.equal(gallery.summary.preferred, Number(process.env.BIRDSHOT_STARTUP_EXPECT_PREFERRED))
  }
  const again = await page.evaluate(() => window.birdshotDesktop.getState())
  assert.equal(again.service.status, 'ready', 'Late readiness must not leave a permanently rejected state API')
  const proof = { version, passed: true, firstStateQuerySeconds: Number(firstQuerySeconds.toFixed(3)),
    totalReadySeconds: Number(((performance.now() - start) / 1000).toFixed(3)), phases: [...stages],
    sources: state.sources.length, crops: gallery.items.length, preferred: gallery.summary.preferred,
    originalConfigUnchanged: !!sourceConfig, noInferenceOrCleanup: true }
  await writeFile(path.join(data, 'verification.json'), JSON.stringify(proof, null, 2))
  console.log(JSON.stringify(proof))
} finally {
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
  await app.close()
  if (directory !== originalDirectory) await restorePackage()
}
