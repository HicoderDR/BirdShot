// SPDX-License-Identifier: AGPL-3.0-only
// Real public birds; actual recycling is restricted to this test's temporary originals.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, mkdir, readFile, writeFile, access, rename } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import path from 'node:path'
import os from 'node:os'
import { createHash } from 'node:crypto'
import { performance } from 'node:perf_hooks'

assert.equal(process.platform, 'win32')
assert.equal(process.env.BIRDSHOT_TEST_REVIEW_PACKAGE, '1')
const root = path.resolve('.')
const version = JSON.parse(await readFile('package.json')).desktopVersion
const built = path.resolve(process.env.BIRDSHOT_DESKTOP_DIRECTORY ||
  path.join('desktop-artifacts', `BirdShot-${version}-win32-x64`))
const temporary = process.env.BIRDSHOT_REVIEW_VALIDATION_ROOT ||
  await mkdtemp(path.join(os.tmpdir(), 'BirdShot 审阅 验证 '))
console.log(JSON.stringify({ validationRoot: temporary, version, phase: 'relocating-complete-app' }))
const directory = process.env.BIRDSHOT_TEST_REVIEW_INSTALLED === '1' ? built : path.join(temporary, '完整 App')
if (process.env.BIRDSHOT_TEST_REVIEW_INSTALLED !== '1') await rename(built, directory)
const data = path.join(temporary, '独立 用户数据')
const sources = [path.join(temporary, '公开 连拍一'), path.join(temporary, '公开 多鸟二')]
await mkdir(data, { recursive: true })
for (const folder of sources) await mkdir(folder, { recursive: true })
const python = path.join(directory, 'resources', 'runtime', 'python', 'python.exe')
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'),
  BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1', PYTHONUTF8: '1',
  PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1' }
delete env.ELECTRON_RUN_AS_NODE
const hash = value => createHash('sha256').update(value).digest('hex')
async function execute(executable, args, extra = {}) {
  return new Promise((resolve, reject) => {
    const process = spawn(executable, args, { cwd: root, env, shell: false, windowsHide: true, ...extra })
    let stdout = '', stderr = ''
    process.stdout.on('data', chunk => { stdout += chunk })
    process.stderr.on('data', chunk => { stderr += chunk })
    process.on('error', reject)
    process.on('exit', code => resolve({ code, stdout, stderr }))
  })
}
const files = [path.join(sources[0], 'public-001.jpg'), path.join(sources[0], 'public-002.jpg'),
  path.join(sources[1], 'public-101.jpg'), path.join(sources[1], 'public-102.jpg')]
if (process.env.BIRDSHOT_REUSE_REVIEW_FIXTURES !== '1') {
  const fixture = await execute(python, ['-s', '-c', `
from pathlib import Path
from PIL import Image
import sys
root=Path(sys.argv[1]);files=sys.argv[2:]
inputs=[root/'public/culling/samples/kingfisher/frame-00120.jpg',root/'public/culling/samples/kingfisher/frame-00121.jpg',root/'public/culling/samples/mallards/pair.jpg',root/'public/culling/samples/mallards/pair.jpg']
for index,(source,target) in enumerate(zip(inputs,files)):
 exif=Image.Exif();exif[0x8769]={36867:'2001:02:03 09:00:0'+str(index%2),36881:'+08:00'}
 if index==3:exif[274]=8
 with Image.open(source) as image:image.convert('RGB').save(target,'JPEG',quality=96,exif=exif)
print('Controlled EXIF on explicitly licensed public samples, not private camera data')
`, root, ...files])
  assert.equal(fixture.code, 0, fixture.stderr)
  await writeFile(files[1].slice(0, -4) + '.ARW', 'Controlled non-pixel RAW companion fixture')
}
const initialHashes = await Promise.all(files.map(async file => hash(await readFile(file))))
const raw = files[1].slice(0, -4) + '.ARW'
const rawBefore = await readFile(raw)
const started = performance.now()
let app
async function launch() {
  app = await _electron.launch({ executablePath: path.join(directory, 'BirdShot.exe'), env, timeout: 90000 })
  const page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  const deadline = Date.now() + 180000
  let state
  do {
    state = await page.evaluate(() => window.birdshotDesktop.getState())
    if (state.startup?.status === 'error') throw new Error(JSON.stringify(state.startup))
    if (state.runtime.ready && state.service.status === 'ready') break
    await page.waitForTimeout(500)
  } while (Date.now() < deadline)
  assert.equal(state.service.status, 'ready')
  return page
}
async function closeApp() {
  if (!app) return
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
  await app.close(); app = null
}
try {
  let page = await launch()
  for (const folder of sources) {
    await app.evaluate(({ dialog }, selected) => { dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [selected] }) }, folder)
    await page.evaluate(() => window.birdshotDesktop.chooseSource())
  }
  const deadline = Date.now() + 600000
  let state
  while (Date.now() < deadline) {
    state = await page.evaluate(() => window.birdshotDesktop.getState())
    const failed = state.sources.find(source => source.status === 'error')
    assert.ok(!failed, failed?.error)
    if (state.sources.length === 2 && state.sources.every(source => source.status === 'completed')) break
    await new Promise(resolve => setTimeout(resolve, 1000))
  }
  assert.ok(state.sources.every(source => source.status === 'completed'))
  const url = state.service.localUrl
  let snapshot = await page.evaluate(() => window.birdshotDesktop.review('state', {}))
  assert.equal(snapshot.groups.length, 2)
  assert.ok(snapshot.groups.every(group => group.frames.length === 2))
  const first = snapshot.groups[0]
  let reviewPage = page.frames().find(frame => frame.url().startsWith(url))
  if (!reviewPage) reviewPage = await page.waitForEvent('framenavigated', { predicate: frame => frame.url().startsWith(url) })
  await reviewPage.locator('#group-grid .group-open').first().waitFor()
  await reviewPage.locator(`#group-grid [data-group-id="${first.id}"]`).click()
  await reviewPage.locator('#stage .source-image').waitFor()
  await reviewPage.waitForFunction(() => !document.getElementById('keep-frame').disabled)
  await page.keyboard.press('s')
  await reviewPage.waitForFunction(() => !document.getElementById('keep-frame').disabled)
  await reviewPage.waitForFunction(() => /未保留/.test(document.getElementById('frame-title').textContent))
  await page.keyboard.press('Space')
  await reviewPage.waitForFunction(() => /已保留/.test(document.getElementById('frame-title').textContent))
  const canvas = await reviewPage.locator('#stage').boundingBox()
  await page.mouse.move(canvas.x + canvas.width * .35, canvas.y + canvas.height * .45)
  await page.keyboard.down('Control')
  await page.mouse.wheel(0, -500)
  await page.keyboard.up('Control')
  await reviewPage.waitForFunction(() => Number(document.getElementById('source-layer').dataset.scale) > 1)
  const zoom = await reviewPage.locator('#source-layer').getAttribute('data-scale')
  await page.keyboard.press('d')
  await reviewPage.waitForFunction(() => document.querySelector('#filmstrip .current')?.dataset.frameId !== document.querySelector('#filmstrip .frame-tile')?.dataset.frameId)
  assert.equal(await reviewPage.locator('#source-layer').getAttribute('data-scale'), zoom, 'Group-local zoom survives changing frames')
  await page.keyboard.press('e')
  await reviewPage.waitForFunction(() => Number(document.getElementById('source-layer').dataset.scale) === 1)
  await reviewPage.locator('#exit').click()
  await reviewPage.locator('#exit-dialog[open]').waitFor()
  await reviewPage.locator('#exit-only').click()
  snapshot = await page.evaluate(() => window.birdshotDesktop.review('state', {}))
  snapshot = await page.evaluate(params => window.birdshotDesktop.review('decide', params), {
    expectedRevision: snapshot.revision, groupId: first.id,
    changes: first.frames.map(frame => ({ frameId: frame.id, selected: !frame.selected })),
  })
  const target = snapshot.groups.find(group => group.id === first.id)
  snapshot = await page.evaluate(params => window.birdshotDesktop.review('decide', params), {
    expectedRevision: snapshot.revision, groupId: first.id,
    changes: target.frames.map((frame, index) => ({ frameId: frame.id, selected: index === 0 })),
  })
  assert.equal(snapshot.groups.find(group => group.id === first.id).manualReviewed, true)
  assert.equal(snapshot.groups.find(group => group.id === first.id).selectedCount, 1)
  assert.ok((await readFile(path.join(data, 'review-state-v1.json'))).length > 0)
  await closeApp()
  page = await launch()
  snapshot = await page.evaluate(() => window.birdshotDesktop.review('state', {}))
  assert.equal(snapshot.groups.find(group => group.id === first.id).selectedCount, 1)
  assert.equal(snapshot.groups.find(group => group.id === first.id).manualReviewed, true)
  await page.locator('#gallery-frame').waitFor()
  let gallery = page.frames().find(frame => frame.url().startsWith(url))
  if (!gallery) gallery = await page.waitForEvent('framenavigated', { predicate: frame => frame.url().startsWith(url) })
  assert.equal(await gallery.evaluate(() => typeof window.birdshotDesktop), 'undefined')
  const httpSnapshot = await (await fetch(url + 'review.json')).json()
  assert.equal(httpSnapshot.revision, snapshot.revision)
  assert.equal((await fetch(url + 'review/decide', { method: 'POST' })).status, 403)
  const plan = await page.evaluate(params => window.birdshotDesktop.review('cleanupPreview', params), { expectedRevision: snapshot.revision })
  assert.equal(plan.photoCount, 1)
  assert.equal(plan.rawCount, 0)
  assert.equal(plan.files.length, 1)
  await app.evaluate(({ dialog }) => { dialog.showMessageBox = async () => ({ response: 1, checkboxChecked: false }) })
  const outcome = await page.evaluate(token => window.birdshotDesktop.review('cleanupExecute', { token }), plan.token)
  assert.equal(outcome.results.length, 1)
  assert.equal(outcome.results[0].success, true, outcome.results[0].error)
  await assert.rejects(access(files[1]))
  assert.equal(hash(await readFile(files[0])), initialHashes[0])
  assert.deepEqual(await readFile(raw), rawBefore, 'Default recycle leaves paired RAW untouched')
  assert.deepEqual(await Promise.all(files.slice(2).map(async file => hash(await readFile(file)))), initialHashes.slice(2))
  const name = path.basename(files[1]), folder = path.dirname(files[1])
  const ps = `
$bin=(New-Object -ComObject Shell.Application).NameSpace(10)
$own=@($bin.Items()|Where-Object {($_.Name -eq '${name}' -or $_.Name -eq '${name.slice(0, -4)}') -and $_.ExtendedProperty('System.Recycle.DeletedFrom') -eq '${folder.replaceAll("'", "''")}'})
if($own.Count -ne 1){throw 'The one owned public fixture is not uniquely recoverable'}
$own[0].InvokeVerb('undelete')
$until=(Get-Date).AddSeconds(15)
while(!(Test-Path -LiteralPath '${files[1].replaceAll("'", "''")}') -and (Get-Date) -lt $until){Start-Sleep -Milliseconds 200}
if(!(Test-Path -LiteralPath '${files[1].replaceAll("'", "''")}')){throw 'Owned fixture restore not completed'}
`
  const restored = await execute(path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
    ['-NoProfile', '-Command', ps])
  assert.equal(restored.code, 0, restored.stderr)
  assert.equal(hash(await readFile(files[1])), initialHashes[1])
  const proof = { version, packagedReviewVerified: true, publicPhotos: 4, groups: 2,
    realModelPipeline: true, sourceIdentitySelectionsPersisted: true, rawDefaultPreserved: true,
    actualFrameBridgeKeyboardAndCtrlZoomVerified: true,
    actualNativeRecycleAndRestore: true, otherPublicGroupsUnmodified: true, remoteWriteRejected: true,
    privatePhotosReadOrRecycled: false, seconds: Number(((performance.now() - started) / 1000).toFixed(3)),
    temporaryValidation: temporary }
  await writeFile(path.join(temporary, 'verification.json'), JSON.stringify(proof, null, 2))
  console.log(JSON.stringify(proof))
} finally {
  await closeApp()
  if (process.env.BIRDSHOT_TEST_REVIEW_INSTALLED !== '1') await rename(directory, built)
}
