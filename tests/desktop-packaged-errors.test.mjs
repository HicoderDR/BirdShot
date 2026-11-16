// SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, rename } from 'node:fs/promises'
import { createServer } from 'node:net'
import os from 'node:os'
import path from 'node:path'

assert.equal(process.env.BIRDSHOT_TEST_PACKAGED, '1')
const directory = path.resolve('desktop-artifacts', 'BirdShot-win32-x64')
const executablePath = path.join(directory, 'BirdShot.exe')
const root = await mkdtemp(path.join(os.tmpdir(), 'BirdShot-errors-'))
const common = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'), BIRDSHOT_OFFLINE: '1' }
delete common.ELECTRON_RUN_AS_NODE
async function stateOf(app) {
  const page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  return { page, state: await page.evaluate(() => window.birdshotDesktop.getState()) }
}
let app
const blocker = createServer(socket => socket.end('owned-conflict-fixture'))
await new Promise((resolve, reject) => { blocker.once('error', reject); blocker.listen(5274, '127.0.0.1', resolve) })
try {
  app = await _electron.launch({ executablePath, env: { ...common, BIRDSHOT_USER_DATA: path.join(root, 'port-conflict') }, timeout: 90000 })
  const { page, state } = await stateOf(app)
  assert.equal(state.service.status, 'error')
  assert.ok(state.service.suggestedPort > 5274)
  const next = await page.evaluate(port => window.birdshotDesktop.configureService({ mode: 'local', port }), state.service.suggestedPort)
  assert.equal(next.service.status, 'ready')
  assert.equal((await fetch(next.service.localUrl + 'health.json')).status, 200)
  await app.close(); app = null
} finally {
  if (app) await app.close()
  await new Promise(resolve => blocker.close(resolve))
}
const model = path.join(directory, 'resources', 'backend', 'public', 'culling', 'models', 'eye.onnx')
const disabled = model + '.test-disabled'
await rename(model, disabled)
try {
  app = await _electron.launch({ executablePath, env: { ...common, BIRDSHOT_USER_DATA: path.join(root, 'missing-model') }, timeout: 90000 })
  const { state } = await stateOf(app)
  assert.equal(state.runtime.ready, false, 'Missing package model must not trigger a hidden download or green state')
  assert.ok(state.runtime.models.some(model => !model.ready || model.error))
} finally {
  if (app) await app.close()
  await rename(disabled, model)
}
console.log(JSON.stringify({ packagedErrorsVerified: true, occupiedPortNotAdopted: true,
  explicitAlternativeWorks: true, missingModelVisible: true, originalPackageModelRestored: true }))
