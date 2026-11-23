// SPDX-License-Identifier: AGPL-3.0-only
// Reuse only an explicitly selected isolated schema1 test library, not a user's live data.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { readFile, writeFile, mkdtemp } from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'

assert.equal(process.env.BIRDSHOT_TEST_INSTALLER, '1')
assert.ok(process.env.BIRDSHOT_LEGACY_VALIDATION_DATA)
const oldFile = path.join(process.env.BIRDSHOT_LEGACY_VALIDATION_DATA, 'desktop-library.json')
const before = await readFile(oldFile)
const old = JSON.parse(before)
assert.equal(old.version, 1)
assert.ok(old.sources.length > 0 && old.sources.every(source => source.status === 'completed'))
const data = await mkdtemp(path.join(os.tmpdir(), 'BirdShot-schema1-upgrade-'))
await writeFile(path.join(data, 'desktop-library.json'), before)
const executable = path.resolve(process.env.BIRDSHOT_INSTALLED_EXE ||
  'desktop-artifacts/BirdShot-0.3.0-win32-x64/BirdShot.exe')
const app = await _electron.launch({ executablePath: executable, env: { ...process.env,
  PATH: path.join(process.env.SystemRoot, 'System32'), BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1' }, timeout: 90000 })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  const state = await page.evaluate(() => window.birdshotDesktop.getState())
  assert.deepEqual(state.sources.map(source => [source.id, source.path, source.job]),
    old.sources.map(source => [source.id, source.path, source.job]))
  assert.equal(state.outputRoot, old.outputRoot)
  assert.equal(state.service.mode, old.service.mode)
  assert.equal(state.service.port, old.service.port)
  assert.ok(state.sources.every(source => source.status === 'completed'))
  assert.equal(state.runtime.ready, true)
  await page.waitForTimeout(1500)
  const next = await page.evaluate(() => window.birdshotDesktop.getState())
  assert.ok(next.sources.every(source => source.status === 'completed'), 'Upgrade must not automatically rerun completed jobs')
  assert.deepEqual(await readFile(oldFile), before, 'The original fixture configuration is read-only')
  console.log(JSON.stringify({ schema1LegacyUpgrade: true, sources: state.sources.length, noAutomaticReanalysis: true,
    sourceIdsPathsJobsOutputAndNetworkPreserved: true, previousConfigUnchanged: true }))
} finally {
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
  await app.close()
}
