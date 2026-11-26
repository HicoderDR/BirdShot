// SPDX-License-Identifier: AGPL-3.0-only
// Explicit opt-in; actual packaged runtimes and public birds, never private reruns.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, mkdir, rename, readFile, writeFile, cp } from 'node:fs/promises'
import { createServer } from 'node:net'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'

assert.equal(process.platform, 'win32')
assert.equal(process.env.BIRDSHOT_TEST_PACKAGED, '1', 'Explicitly opt in to starting the packaged app')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const version = JSON.parse(await readFile(path.join(root, 'package.json'))).desktopVersion || '0.2.0'
const built = path.resolve(process.env.BIRDSHOT_DESKTOP_DIRECTORY || path.join(root, 'desktop-artifacts', `BirdShot-${version}-win32-x64`))
const temp = process.env.BIRDSHOT_REUSE_VALIDATION || process.env.BIRDSHOT_VALIDATION_ROOT ||
  await mkdtemp(path.join(os.tmpdir(), 'BirdShot 桌面 验证 '))
console.log(JSON.stringify({ validationRoot: temp, phase: 'packaged-relocation' }))
const relocated = process.env.BIRDSHOT_TEST_INSTALLED === '1' ? built : path.join(temp, '便携 App')
if (process.env.BIRDSHOT_TEST_INSTALLED !== '1') await rename(built, relocated)
const data = path.join(temp, '干净 用户配置'), sources = [path.join(temp, '来源甲', '同名 照片'), path.join(temp, '来源乙', '同名 照片')]
await mkdir(data, { recursive: true })
for (const source of sources) await mkdir(source, { recursive: true })
const python = path.join(relocated, 'resources', 'runtime', 'python', 'python.exe')
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'),
  PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1',
  BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1', BIRDSHOT_NETWORK_AUDIT: path.join(temp, 'network-audit-' + Date.now() + '.jsonl') }
for (const key of Object.keys(env)) if (/^(PYTHONHOME|PYTHONPATH|VIRTUAL_ENV|ELECTRON_RUN_AS_NODE|DONGNIAO_API_KEY|GH_TOKEN|GITHUB_TOKEN|COPILOT_GH_ACCOUNT_)/.test(key)) delete env[key]
const hash = data => createHash('sha256').update(data).digest('hex')
const files = [path.join(sources[0], '连续 鸟 1.jpg'), path.join(sources[0], '连续 鸟 2.jpg'), path.join(sources[1], '公开 双鸟.jpg')]
const proof = { version, packaged: true, installed: process.env.BIRDSHOT_TEST_INSTALLED === '1',
  relocatedUnicodeAndSpaces: true, systemNodePythonPathRemoved: true,
  publicFixturesWithControlledExif: true, reusingCompletedPublicJobs: !!process.env.BIRDSHOT_REUSE_VALIDATION }
let app, blocker
try {
  const fixture = process.env.BIRDSHOT_REUSE_VALIDATION ? { status: 0 } : spawnSync(python, ['-s', '-c', `
from pathlib import Path
from PIL import Image
import sys
root=Path(sys.argv[1]); targets=sys.argv[2:]
inputs=[root/'public/culling/samples/kingfisher/frame-00120.jpg',root/'public/culling/samples/kingfisher/frame-00121.jpg',root/'public/culling/samples/mallards/pair.jpg']
for i,(source,target) in enumerate(zip(inputs,targets)):
 exif=Image.Exif();exif[0x8769]={36867:'2001:02:03 09:00:0'+str(i%2),36881:'+08:00'}
 with Image.open(source) as image:image.convert('RGB').save(target,'JPEG',quality=96,exif=exif)
print('Public licensed birds with explicitly controlled EXIF dates; not private camera data')
`, root, ...files], { env, encoding: 'utf8', shell: false })
  assert.equal(fixture.status, 0, fixture.stderr)
  const before = await Promise.all(files.map(async file => hash(await readFile(file))))
  async function launch() {
    app = await _electron.launch({ executablePath: path.join(relocated, 'BirdShot.exe'), cwd: temp, env, timeout: 90000 })
    const page = await app.firstWindow()
    await page.waitForFunction(() => !!window.birdshotDesktop, undefined, { timeout: 30000 })
    const state = await page.evaluate(() => window.birdshotDesktop.getState())
    assert.equal(state.runtime.ready, true, JSON.stringify(state.runtime))
    assert.equal(state.runtime.mode, 'packaged-verified')
    assert.ok(state.runtime.models.every(model => model.verified && model.path.startsWith(relocated)))
    return page
  }
  async function waitState(page, predicate, timeout = 480000) {
    const until = Date.now() + timeout
    while (Date.now() < until) {
      const state = await page.evaluate(() => window.birdshotDesktop.getState())
      if (state.sources.some(source => ['failed', 'error'].includes(source.status))) throw new Error(JSON.stringify(state.sources.map(source => ({ status: source.status, error: source.error }))))
      if (predicate(state)) return state
      await new Promise(resolve => setTimeout(resolve, 750))
    }
    throw new Error('Packaged processing timed out')
  }
  async function selectFolder(page, folder, method = 'chooseSource') {
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] }) }, folder)
    return page.evaluate(name => window.birdshotDesktop[name](), method)
  }
  async function closeApp() {
    if (!app) return
    await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
    await app.close(); app = null
  }
  if (!process.env.BIRDSHOT_REUSE_VALIDATION) {
    blocker = createServer(socket => socket.end('occupied-by-test'))
    await new Promise((resolve, reject) => { blocker.once('error', reject); blocker.listen(5274, '127.0.0.1', resolve) })
  }
  let page = await launch()
  if (blocker) {
    const conflict = await page.evaluate(() => window.birdshotDesktop.getState())
    assert.equal(conflict.service.status, 'error')
    assert.ok(conflict.service.suggestedPort > 5274)
    await page.evaluate(settings => window.birdshotDesktop.configureService(settings), { mode: 'local', port: conflict.service.suggestedPort })
    await new Promise(resolve => blocker.close(resolve)); blocker = null
    proof.occupiedPortReportedAndExplicitAlternativePersisted = true
  }
  console.log(JSON.stringify({ phase: 'runtime-ready-public-folders' }))
  await selectFolder(page, sources[0])
  await selectFolder(page, sources[1])
  let state = await waitState(page, state => state.sources.length === 2 && state.sources.every(source => source.status === 'completed'))
  proof.newSourcesCompleted = 2
  proof.runtimeModelsVerified = state.runtime.models.length
  proof.runtimeMode = state.runtime.mode
  const snapshot = await (await fetch(state.service.localUrl + 'gallery.json')).json()
  assert.equal(snapshot.navigation.groups.date.length, 1)
  assert.equal(snapshot.navigation.groups.folder.length, 2)
  assert.equal(snapshot.navigation.groups.place.length, 1)
  assert.equal(snapshot.navigation.groups.date[0].photoCount, 3)
  assert.equal(snapshot.navigation.groups.place[0].name, '未知地点')
  assert.ok(snapshot.items.length >= 2 && snapshot.items.some(item => item.species.top3.length > 0), 'Actual segmentation and full-class species inference must produce browsable crops')
  assert.equal(snapshot.progress.processed, snapshot.progress.total)
  proof.photoCount = 3; proof.cropCount = snapshot.items.length; proof.preferred = snapshot.summary.preferred
  proof.groups = { date: 1, folder: 2, place: 1 }
  let audit = ''
  try { audit = await readFile(env.BIRDSHOT_NETWORK_AUDIT, 'utf8') } catch (error) { if (error.code !== 'ENOENT') throw error }
  assert.equal(audit, '', 'Model preparation/inference must not attempt external connections')
  proof.externalModelConnections = 0
  if (process.env.BIRDSHOT_REFERENCE_FIXTURE) {
    const name = path.basename(process.env.BIRDSHOT_REFERENCE_FIXTURE)
    assert.match(name, /^[a-f0-9]{64}\.jpg$/)
    const cache = path.join(data, 'library', 'revisions', 'species-reference-cache-v1')
    await mkdir(cache, { recursive: true })
    await cp(process.env.BIRDSHOT_REFERENCE_FIXTURE, path.join(cache, name))
    await cp(process.env.BIRDSHOT_REFERENCE_FIXTURE.slice(0, -4) + '.image.json', path.join(cache, name.slice(0, -4) + '.image.json'))
  }
  let gallery = page.frames().find(frame => frame.url().startsWith(state.service.localUrl))
  if (!gallery) gallery = await page.waitForEvent('framenavigated', { predicate: frame => frame.url().startsWith(state.service.localUrl), timeout: 30000 })
  // Retain regression coverage for the compatible crop gallery; review has its own tests.
  await gallery.goto(new URL('/gallery-crops/', state.service.localUrl).href)
  await gallery.locator('#gallery[aria-busy="false"]').waitFor()
  await gallery.waitForFunction(count => state.index?.items?.length === count, snapshot.items.length)
  assert.equal(await gallery.evaluate(() => typeof window.birdshotDesktop), 'undefined', 'Shared gallery never receives native IPC')
  await gallery.locator('#review-tab').click()
  if (!await gallery.locator('.tile').count()) await gallery.locator('#preferred-tab').click()
  await gallery.locator('.tile-open').first().click()
  await gallery.locator('.preview-area[aria-busy="false"]').waitFor()
  await gallery.locator('.original-toggle').click()
  await gallery.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.match(await gallery.locator('.crop-wrap img:visible').getAttribute('src'), /^\/preview\//)
  await gallery.locator('#close').click()
  await selectFolder(page, sources[0])
  state = await page.evaluate(() => window.birdshotDesktop.getState())
  assert.equal(state.sources.length, 2, 'Adding the same canonical folder must not duplicate it')
  for (const mode of ['folder', 'date', 'place']) {
    await gallery.locator('#navigation-mode').selectOption(mode)
    assert.equal(await gallery.locator('#shoot-group option').count(), snapshot.navigation.groups[mode].length + 1)
  }
  assert.deepEqual(await Promise.all(files.map(async file => hash(await readFile(file)))), before)
  proof.originalHashesUnchanged = true
  const localUrl = state.service.localUrl
  console.log(JSON.stringify({ phase: 'public-pipeline-complete', photos: 3, crops: snapshot.items.length }))
  await closeApp()
  page = await launch()
  state = await waitState(page, state => state.sources.length === 2 && state.sources.every(source => source.status === 'completed'))
  assert.equal(state.service.localUrl, localUrl)
  proof.restartRetainedSourcesAndJobs = true
  const lan = process.env.BIRDSHOT_TEST_LAN_IP
  if (lan) {
    state = await page.evaluate(settings => window.birdshotDesktop.configureService(settings), { mode: 'lan', port: state.service.port, lanIp: lan })
    assert.equal(state.service.status, 'ready', JSON.stringify(state.service))
    const lanUrl = 'http://' + lan + ':' + state.service.port + '/'
    const response = await fetch(lanUrl)
    assert.equal(response.status, 200)
    const index = await (await fetch(lanUrl + 'gallery.json')).json()
    const item = index.items[0]
    for (const route of ['media/' + item.thumbnail, 'preview/' + item.frameId + '/960.jpg', 'original/' + item.frameId]) {
      const media = await fetch(new URL(route, lanUrl))
      assert.equal(media.status, 200)
      await media.arrayBuffer()
    }
    if (process.env.BIRDSHOT_REFERENCE_FIXTURE) {
      assert.equal((await fetch(new URL('species-media/' + path.basename(process.env.BIRDSHOT_REFERENCE_FIXTURE), lanUrl))).status, 200)
      proof.offlineRegisteredReferenceMediaServed = true
    }
    assert.equal((await fetch(new URL('cancel', lanUrl), { method: 'POST' })).status, 403)
    state = await page.evaluate(settings => window.birdshotDesktop.configureService(settings), { mode: 'all', port: state.service.port, lanIp: lan })
    assert.equal(state.service.status, 'ready')
    assert.equal((await fetch(lanUrl)).headers.get('X-BirdShot-Access-Mode'), 'all-sources-read-only')
    state = await page.evaluate(settings => window.birdshotDesktop.configureService(settings), { mode: 'local', port: state.service.port })
    assert.equal(state.service.status, 'ready')
    await assert.rejects(fetch(lanUrl))
    assert.equal((await fetch(state.service.localUrl)).status, 200)
    proof.lanSharingReadOnlyAndDisabledAfterwards = true
  }
  if (process.env.BIRDSHOT_EXISTING_JOB) {
    await closeApp()
    env.BIRDSHOT_USER_DATA = path.join(temp, '仅旧批次 配置')
    await mkdir(env.BIRDSHOT_USER_DATA, { recursive: true })
    page = await launch()
    state = await selectFolder(page, process.env.BIRDSHOT_EXISTING_JOB, 'attachExisting')
    const combined = await (await fetch(state.service.localUrl + 'gallery.json')).json()
    assert.equal(combined.progress.processed, 5504)
    assert.equal(combined.summary.preferred, 544)
    assert.equal(combined.navigation.groups.date.length, 13)
    assert.equal(combined.navigation.groups.folder.length, 1)
    assert.equal(combined.navigation.groups.place.length, 1)
    proof.existingBatchLoadedWithoutInference = true
  }
  await writeFile(path.join(temp, 'verification.json'), JSON.stringify(proof, null, 2))
  console.log(JSON.stringify({ ...proof, verification: path.join(temp, 'verification.json') }))
} finally {
  if (app) {
    await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 }).catch(() => {})
    await app.close()
  }
  if (blocker) await new Promise(resolve => blocker.close(resolve))
  if (process.env.BIRDSHOT_TEST_INSTALLED !== '1') await rename(relocated, built)
}
