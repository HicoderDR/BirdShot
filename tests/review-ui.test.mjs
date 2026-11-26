// SPDX-License-Identifier: AGPL-3.0-only
// Only generated SVGs and in-memory API fixtures; no private photos or screenshots.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { chromium } from 'playwright'
import {
  createNativeBridge, filteredGroups, formatCaptureTime, frameInstances, geometry, groupLabel, mediaURL, nextResolution,
  probabilityLabel, shortcut, technicalSentence, topThree, validateSnapshot, zoomAt
} from '../scripts/review.js'

const candidates = Array.from({ length: 5 }, (_, index) => ({
  commonName: `合成鸟种 ${index + 1}`, scientificName: `Synthetic ${index + 1}`, probability: [.33333, .22222, .11111, .05, .01][index]
}))
const instance = (id, status = 'bird', extra = {}) => ({
  id, crop: `crops/synthetic bird ${id}.jpg`, thumbnail: `thumbs/synthetic ${id}.jpg`,
  cropRect: [100 + id * 120, 160, 220, 190], mask: { path: `M ${100 + id * 120} 160 l 220 0 l 0 190 Z` },
  birdVerification: { status, model: 'Synthetic Birder - NOT REAL CLASSIFICATION' },
  species: { top5: candidates }, decision: id === 0 ? 'keep' : 'review',
  quality: { tierLabel: '标准可用原像素', bodyPixels: 19000, bodyTenengrad: 600, withinGroupRank: 2, edgeRisk: false },
  ...extra
})
const frames = [
  { id: 'p000003', sourceLabel: 'synthetic-003.jpg', width: 6000, height: 4000, selected: true, automatic: true, manual: false, available: true, supported: true },
  { id: 'p000001', sourceLabel: 'synthetic-001-no-bird.jpg', width: 4000, height: 6000, selected: false, automatic: false, manual: false, available: true, supported: true },
  { id: 'p000002', sourceLabel: 'synthetic-002.jpg', width: 6000, height: 4000, selected: true, automatic: true, manual: false, available: true, supported: true }
]
function baseline() {
  return { version: 1, revision: 5, preferences: { exitAction: 'ask' }, groups: [
    { id: 'g00002', revision: 'synthetic-a', name: '原始第一组', frames: structuredClone(frames), manualReviewed: false, newFramesPending: 0,
      selectedCount: 2, automaticCount: 2, cover: { crop: 'crops/synthetic bird 0.jpg', frameId: 'p000003' } },
    { id: 'g00001', revision: 'synthetic-b', name: '无鸟第二组', frames: [
      { ...frames[0], id: 'p000004', sourceLabel: 'synthetic-no-bird.jpg', selected: false, automatic: false }
    ], manualReviewed: true, newFramesPending: 0, selectedCount: 0, automaticCount: 0, cover: { crop: null, frameId: 'p000004' } },
    { id: 'g' + 'a'.repeat(24), revision: 'synthetic-c', name: '2023-08-30T15:06:07+08:00',
      sourceLabel: 'synthetic-folder\\synthetic-stable.jpg', capture: { time: '2023-08-30T15:06:07+08:00' }, frames: [
      { ...frames[0], id: 'p' + 'b'.repeat(24), sourceLabel: 'synthetic-stable.jpg' }
    ], manualReviewed: false, newFramesPending: 1, selectedCount: 1, automaticCount: 1, cover: { crop: null, frameId: null } }
  ], summary: {} }
}
function detail(id) {
  const entry = snapshot.groups.find(group => group.id === id)
  return { id, frames: entry.frames.map(frame => ({
    ...frame,
    instances: frame.id === 'p000003' ? [instance(0), instance(1), instance(2, 'nonBird'), instance(3, 'unknown')] :
      frame.id === 'p000002' ? [instance(0, 'bird', { crop: 'crops/next-frame.jpg' })] : []
  })) }
}
const gallery = {
  items: [instance(0), instance(1), instance(2, 'nonBird'), instance(3, 'unknown')].map(value => ({
    ...value, groupId: 'g00002', frameId: 'p000003', instanceId: value.id,
    rank: value.id === 0 ? 999 : 1, navigationGroups: { date: 'date-one', folder: 'folder-one', place: 'place-one' },
    shootGroup: { id: 'must-not-merge-real-bursts' }
  })),
  navigation: { groups: { date: [{ id: 'date-one', name: '合成日期' }], folder: [{ id: 'folder-one', name: '合成文件夹' }],
    place: [{ id: 'place-one', name: '合成地点' }] } }
}
let snapshot = baseline()
const unchanged = structuredClone(snapshot)
assert.equal(groupLabel(snapshot.groups[2], 3), 'synthetic-stable.jpg')
assert.equal(groupLabel({ name: '2023-08-30T15:06:07+08:00', frames: [] }, 4), '连拍 4')
assert.equal(groupLabel({ name: '真实组名', sourceLabel: 'synthetic.jpg' }, 1), '真实组名')
assert.equal(formatCaptureTime('2023-08-30T23:06:07-04:00'), '2023/08/31 11:06')
assert.equal(formatCaptureTime('2023-08-30T15:06:07+08:00', true), '2023/08/30 15:06:07')
assert.equal(formatCaptureTime('2023-08-30T23:06:07'), '2023/08/30 23:06', 'offset-free camera day must not shift')
assert.equal(formatCaptureTime('2023-08-30T23:06:07', true), '2023/08/30 23:06:07')
assert.equal(formatCaptureTime('时间未知'), '时间未知')
assert.equal(formatCaptureTime(null), '')
assert.deepEqual(snapshot, unchanged, 'display formatting leaves metadata and group/frame ordering untouched')
assert.deepEqual(filteredGroups(snapshot, gallery).map(value => value.group.id), ['g00002', 'g00001', 'g' + 'a'.repeat(24)])
assert.equal(filteredGroups(snapshot, gallery, { mode: 'date', directory: 'date-one' })[0].matchedFrames, 1)
assert.equal(filteredGroups(snapshot, gallery, { mode: 'date', directory: 'date-one' })[0].group.frames.length, 3)
assert.equal(filteredGroups(snapshot, gallery, { search: '合成鸟种 2' }).length, 1)
assert.equal(filteredGroups(snapshot, gallery, { search: 'no-bird' }).length, 2)
assert.throws(() => validateSnapshot({ ...snapshot, revision: '5' }), /格式/)
assert.equal(frameInstances('g00002', detail('g00002').frames[0], gallery.items).filter(value => value.birdVerification.status === 'bird').length, 2)
assert.equal(frameInstances('g00002', { id: 'p000001', instances: [] }, gallery.items).length, 0)
assert.equal(topThree({ top5: candidates }).length, 3)
assert.equal(probabilityLabel(.33333), '33.3%')
assert.equal(probabilityLabel('0.3'), '概率未提供')
assert.equal(mediaURL('crops/synthetic bird #1.jpg'), '/media/crops/synthetic%20bird%20%231.jpg')
assert.equal(mediaURL('../original.jpg'), null)
assert.match(technicalSentence(instance(0)), /主体纹理.*不是眼睛合焦/)
assert.equal(nextResolution(480, frames[0], 1600), 960)
assert.equal(nextResolution(960, frames[0], 1600), 1920)
assert.equal(nextResolution(3840, frames[0], 4500), 'original')
assert.equal(nextResolution(3840, frames[0], 2000), null)
const source = frames[0], initial = { scale: 1, cx: .5, cy: .5 }
const before = geometry(source, 1000, 700, initial)
const zoom = zoomAt(initial, source, 1000, 700, 250, 180, 2)
const after = geometry(source, 1000, 700, zoom)
assert.ok(Math.abs((250 - before.x) / before.factor - (250 - after.x) / after.factor) < 1e-8)
assert.ok(Math.abs((180 - before.y) / before.factor - (180 - after.y) / after.factor) < 1e-8)
for (const ignored of [{ repeat: true }, { isComposing: true }, { ctrlKey: true }, { target: { closest: () => ({}) } }]) {
  assert.equal(shortcut({ key: 'd', ...ignored }), null)
}
assert.equal(shortcut({ key: 'A' }), 'previousFrame')

// A file/null-origin parent is a prerequisite; self, siblings and web parents cannot grant write access.
function fakeWindow(embedded = true) {
  const listeners = new Map(), messages = []
  const win = { document: { readyState: 'complete' }, setTimeout, clearTimeout,
    addEventListener: (type, callback) => listeners.set(type, callback), removeEventListener: type => listeners.delete(type) }
  win.parent = embedded ? { postMessage: (message, target) => messages.push({ message, target }) } : win
  win.emit = (data, origin = 'null', source = win.parent) => listeners.get('message')?.({ data, origin, source })
  return { win, messages }
}
const fake = fakeWindow(), trusted = createNativeBridge(fake.win)
assert.deepEqual(fake.messages[0], { message: { type: 'birdshot-review-ready' }, target: '*' })
fake.win.emit({ type: 'birdshot-review-capabilities', writable: true }, 'http://127.0.0.1')
assert.equal(trusted.writable, false)
fake.win.emit({ type: 'birdshot-review-capabilities', writable: true }, 'null', {})
assert.equal(trusted.writable, false)
fake.win.emit({ type: 'birdshot-review-capabilities', writable: true }, 'file://')
assert.equal(trusted.writable, true)
const bridgeRequest = trusted.request('state')
const sent = fake.messages.at(-1).message
fake.win.emit({ type: 'birdshot-review-response', id: sent.id, ok: true, result: baseline() }, 'http://evil.invalid')
fake.win.emit({ type: 'birdshot-review-response', id: sent.id, ok: true, result: baseline() })
assert.equal((await bridgeRequest).revision, 5)
trusted.dispose()
const topWindow = fakeWindow(false), readonly = createNativeBridge(topWindow.win)
topWindow.win.emit({ type: 'birdshot-review-capabilities', writable: true })
assert.equal(readonly.writable, false); readonly.dispose()

const assets = new Map(await Promise.all(['review-report.html', 'review.js', 'review.css'].map(async name =>
  [name, await readFile(new URL('../scripts/' + name, import.meta.url))])))
const requests = [], operations = [], modes = []
let failSave = false, decodeDelay = 0, originalWrong = false, cleanupOptions
function svg(width, height) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#123854"/><text x="15" y="35" fill="#b7d7ff" font-size="22">SYNTHETIC ONLY</text></svg>`
}
const server = createServer(async (request, response) => {
  const url = new URL(request.url, 'http://127.0.0.1'), path = url.pathname
  requests.push(path); response.setHeader('Cache-Control', 'no-store')
  if (path === '/' || path === '/review-report.html') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); response.end(assets.get('review-report.html'))
  } else if (assets.has(path.slice(1))) {
    response.writeHead(200, { 'Content-Type': path.endsWith('.js') ? 'text/javascript; charset=utf-8' : 'text/css; charset=utf-8' })
    response.end(assets.get(path.slice(1)))
  } else if (path === '/review.json' || path === '/gallery.json') {
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(path === '/review.json' ? snapshot : gallery))
  } else if (/^\/gallery\/group\/g(?:\d{5}|[a-f0-9]{24})\.json$/.test(path)) {
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(detail(path.split('/').at(-1).slice(0, -5))))
  } else if (path.startsWith('/preview/') || path.startsWith('/original/')) {
    if (decodeDelay && !path.endsWith('/480.jpg')) await new Promise(resolve => setTimeout(resolve, decodeDelay))
    const id = path.split('/')[2], frame = snapshot.groups.flatMap(group => group.frames).find(frame => frame.id === id)
    const original = path.startsWith('/original/'), level = original ? Math.max(frame.width, frame.height) : Number(path.split('/')[3].slice(0, -4))
    const factor = Math.min(1, level / Math.max(frame.width, frame.height))
    response.writeHead(200, { 'Content-Type': 'image/svg+xml' })
    response.end(svg(original && originalWrong ? frame.width / 2 : Math.round(frame.width * factor), original && originalWrong ? frame.height / 2 : Math.round(frame.height * factor)))
  } else if (path.startsWith('/media/')) {
    response.writeHead(200, { 'Content-Type': 'image/svg+xml' }); response.end(svg(240, 180))
  } else if (path === '/species-reference') {
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({
      article: { title: '合成百科', url: 'https://example.invalid/synthetic' },
      image: { url: '/species-media/' + 'a'.repeat(64) + '.jpg', sourceUrl: 'https://example.invalid/source',
        author: '<synthetic author>', license: 'CC BY-SA', licenseUrl: 'https://example.invalid/license' }
    }))
  } else if (path.startsWith('/species-media/')) {
    response.writeHead(200, { 'Content-Type': 'image/svg+xml' }); response.end(svg(100, 80))
  } else { response.writeHead(404); response.end('synthetic missing route') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = 'http://127.0.0.1:' + server.address().port
let browser
try {
  browser = await chromium.launch({ channel: process.env.BIRDSHOT_BROWSER_CHANNEL || 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = [], external = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (new URL(request.url()).origin !== origin) external.push(request.url()) })
  const ready = async () => {
    await page.waitForFunction(() => document.querySelector('#group-grid').getAttribute('aria-busy') === 'false')
    await page.locator('.group-open').first().waitFor()
  }
  const refreshPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  refreshPage.on('pageerror', error => errors.push(error.message))
  await refreshPage.addInitScript(() => {
    Object.defineProperty(window, 'parent', { value: { postMessage() {} } })
  })
  const notifyRefresh = async (origin = 'null', trustedSource = true) => refreshPage.evaluate(({ origin, trustedSource }) => {
    const event = new Event('message')
    Object.defineProperties(event, {
      origin: { value: origin }, source: { value: trustedSource ? window.parent : {} },
      data: { value: { type: 'birdshot-review-refresh' } }
    })
    window.dispatchEvent(event)
  }, { origin, trustedSource })
  snapshot = { ...baseline(), revision: 0, groups: [] }
  await refreshPage.goto(origin)
  await refreshPage.waitForFunction(() => document.querySelector('#group-grid').getAttribute('aria-busy') === 'false')
  assert.equal(await refreshPage.locator('.group-open').count(), 0, 'new empty library initially has no groups')
  assert.match(await refreshPage.locator('#summary').textContent(), /^0 组/)
  const emptyReads = requests.filter(path => path === '/review.json').length
  snapshot = { ...baseline(), revision: 1, groups: baseline().groups.slice(0, 2) }
  await notifyRefresh('https://untrusted.invalid')
  await notifyRefresh('null', false)
  await refreshPage.waitForTimeout(350)
  assert.equal(requests.filter(path => path === '/review.json').length, emptyReads, 'untrusted refresh origin/source is ignored')
  await notifyRefresh()
  await refreshPage.waitForFunction(() => document.querySelectorAll('.group-open').length === 2 &&
    document.querySelector('#group-grid').getAttribute('aria-busy') === 'false')
  assert.deepEqual(await refreshPage.locator('.group-open').evaluateAll(nodes => nodes.map(node => node.dataset.groupId)),
    ['g00002', 'g00001'], 'parent completion refresh reveals two real-schema fixture groups without manual refresh')
  assert.equal(await refreshPage.locator('#cleanup').isDisabled(), true)
  await refreshPage.locator('.group-open').first().click()
  await refreshPage.locator('#stage .source-image').waitFor()
  await refreshPage.locator('#next-frame').click()
  await refreshPage.locator('#stage .source-image').waitFor()
  const refreshBounds = await refreshPage.locator('#stage').boundingBox()
  await refreshPage.mouse.move(refreshBounds.x + refreshBounds.width * .3, refreshBounds.y + refreshBounds.height * .45)
  await refreshPage.keyboard.down('Control')
  await refreshPage.mouse.wheel(0, -230)
  await refreshPage.keyboard.up('Control')
  await refreshPage.waitForFunction(() => Number(document.querySelector('#source-layer').dataset.scale) > 1.8)
  const currentView = await refreshPage.locator('#source-layer').evaluate(node => ({
    scale: node.dataset.scale, cx: node.dataset.centerX, cy: node.dataset.centerY, transform: node.style.transform
  }))
  await refreshPage.locator('#exit').click()
  assert.equal(await refreshPage.locator('#exit-dialog').evaluate(node => node.open), true)
  snapshot = { ...baseline(), revision: 2 }
  await notifyRefresh('file://')
  await refreshPage.waitForFunction(() => document.querySelectorAll('.group-open').length === 3 &&
    document.querySelector('#group-grid').getAttribute('aria-busy') === 'false')
  assert.equal(await refreshPage.locator('#immersive').isVisible(), true, 'background parent refresh does not exit immersive review')
  assert.equal(await refreshPage.locator('#exit-dialog').evaluate(node => node.open), true, 'background refresh leaves the confirmation modal open')
  assert.match(await refreshPage.locator('#frame-title').textContent(), /^2 \/ 3.*synthetic-001/)
  assert.deepEqual(await refreshPage.locator('#source-layer').evaluate(node => ({
    scale: node.dataset.scale, cx: node.dataset.centerX, cy: node.dataset.centerY, transform: node.style.transform
  })), currentView, 'parent refresh preserves the current source-frame scale, normalized center and transform')
  await refreshPage.locator('#continue-review').click()
  await refreshPage.close()
  snapshot = baseline()
  requests.length = 0
  await page.goto(origin); await ready()
  assert.deepEqual(await page.locator('.group-open').evaluateAll(nodes => nodes.map(node => node.dataset.groupId)), snapshot.groups.map(group => group.id))
  assert.equal(await page.locator('.group-copy strong').nth(2).textContent(), 'synthetic-stable.jpg')
  assert.match(await page.locator('.group-copy').nth(2).textContent(), /2023\/08\/30 15:06/)
  assert.ok(!(await page.locator('.group-copy').nth(2).textContent()).includes('2023-08-30T'))
  assert.equal(await page.locator('#cleanup').isDisabled(), true)
  assert.equal(requests.some(path => path.startsWith('/original/')), false)
  assert.equal(requests.some(path => path.startsWith('/gallery/group/')), false)
  await page.locator('#navigation-mode').selectOption('date')
  await page.locator('#navigation-group').selectOption('date-one')
  assert.equal(await page.locator('.group-open').count(), 1)
  assert.match(await page.locator('.group-copy').textContent(), /命中 1 帧（完整组）/)
  await page.locator('#navigation-mode').selectOption('all')
  await page.locator('.group-open').first().click()
  await page.locator('#stage .source-image').waitFor()
  await page.waitForFunction(() => document.querySelectorAll('#crop-grid .crop-button').length === 2)
  assert.deepEqual(await page.locator('.frame-tile').evaluateAll(nodes => nodes.map(node => node.dataset.frameId)), frames.map(frame => frame.id))
  assert.equal(await page.locator('#source-overlay').getAttribute('viewBox'), '0 0 6000 4000')
  assert.equal(await page.locator('#source-overlay path').count(), 2, 'nonBird / unknown cannot be an overlay bird')
  assert.equal(await page.locator('#unknown-section').getAttribute('open'), null)
  assert.equal(await page.locator('#unknown-grid .crop-button').count(), 1)
  assert.equal(await page.locator('#top-three .species-candidate').count(), 3)
  assert.equal(await page.locator('#top-three .classification-score').first().textContent(), '33.3%')
  assert.equal(await page.locator('#top-three .classification-score').first().evaluate(node => getComputedStyle(node).color), 'rgb(118, 184, 255)')
  assert.ok(!(await page.locator('#top-three').textContent()).includes('合成鸟种 4'))
  await page.waitForFunction(() => document.querySelectorAll('#leaders line').length === 2)
  assert.deepEqual(await page.locator('#leaders line').evaluateAll(nodes => nodes.map(node => [node.dataset.frameId, node.dataset.instanceId, node.dataset.crop])),
    [['p000003', '0', 'crops/synthetic bird 0.jpg'], ['p000003', '1', 'crops/synthetic bird 1.jpg']])
  assert.equal(requests.find(path => path.startsWith('/preview/p000003/')), '/preview/p000003/480.jpg')
  assert.equal(requests.some(path => path.startsWith('/original/')), false)
  await page.locator('#crop-grid .crop-button').nth(1).click()
  assert.equal(await page.locator('#source-overlay path.focused').getAttribute('data-instance-id'), '1')
  await page.keyboard.press('d')
  await page.waitForFunction(() => document.querySelector('#frame-title').textContent.includes('p000001') || document.querySelector('#frame-title').textContent.includes('synthetic-001'))
  assert.equal(await page.locator('#source-overlay path').count(), 0)
  assert.equal(await page.locator('#crop-grid .crop-button').count(), 0)
  assert.equal(await page.locator('.frame-tile').count(), 3)
  await page.locator('#stage .source-image').waitFor()
  assert.match(await page.locator('#stage .source-image').getAttribute('src'), /p000001\/480/)
  await page.keyboard.press('a')
  await page.waitForFunction(() => document.querySelector('#frame-title').textContent.includes('synthetic-003'))
  await page.locator('#stage .source-image').waitFor()

  const normalized = async () => page.locator('#source-layer').evaluate(node => ({
    scale: Number(node.dataset.scale), cx: Number(node.dataset.centerX), cy: Number(node.dataset.centerY)
  }))
  const bounds = await page.locator('#stage').boundingBox(), px = bounds.x + bounds.width * .32, py = bounds.y + bounds.height * .45
  await page.mouse.move(px, py); await page.keyboard.down('Control'); await page.mouse.wheel(0, -230); await page.keyboard.up('Control')
  const zoomed = await normalized()
  assert.ok(zoomed.scale > 1.8)
  await page.keyboard.press('d')
  await page.waitForFunction(() => document.querySelector('#frame-title').textContent.includes('synthetic-001'))
  assert.deepEqual(await normalized(), zoomed, 'frame switch preserves scale and normalized source center')
  await page.keyboard.press('e')
  await page.waitForFunction(() => document.querySelector('#group-title').textContent === '无鸟第二组')
  assert.deepEqual(await normalized(), initial, 'group switch resets view')
  await page.keyboard.press('q')
  await page.waitForFunction(() => document.querySelector('#group-title').textContent === '原始第一组')
  await page.keyboard.press('a')
  assert.match(await page.locator('#frame-title').textContent(), /^1 \/ 3/)
  await page.keyboard.press('q')
  assert.equal(await page.locator('#group-title').textContent(), '原始第一组', 'boundary navigation does not wrap')
  await page.locator('#search').evaluate(node => { node.focus(); node.dispatchEvent(new KeyboardEvent('keydown', { key: 'd', bubbles: true })) })
  assert.match(await page.locator('#frame-title').textContent(), /^1 \/ 3/)
  await page.locator('#stage').evaluate(node => {
    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'd', repeat: true, bubbles: true }))
    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'd', isComposing: true, bubbles: true }))
  })
  assert.match(await page.locator('#frame-title').textContent(), /^1 \/ 3/)
  await page.locator('#exit').click()
  assert.equal(await page.locator('#exit-dialog').evaluate(node => node.open), true)
  await page.keyboard.press('d')
  assert.match(await page.locator('#frame-title').textContent(), /^1 \/ 3/)
  assert.equal(await page.locator('#remember-exit').isChecked(), false)
  await page.locator('#continue-review').click()
  await page.locator('#exit').click(); await page.locator('#exit-only').click()
  assert.equal(await page.locator('#immersive').isHidden(), true)

  // Test-only embedded parent simulation. Production HTML has no test hook or HTTP write route.
  await page.exposeFunction('__nativeRequest', message => {
    if (message.type === 'birdshot-review-mode') { modes.push(message.immersive); return null }
    if (message.type !== 'birdshot-review-request') return null
    operations.push(structuredClone(message))
    const params = message.params
    if (message.action === 'state') return { ok: true, result: snapshot }
    if (message.action === 'decide') {
      if (failSave) { failSave = false; return { ok: false, error: 'revision conflict synthetic' } }
      assert.equal(params.expectedRevision, snapshot.revision)
      assert.ok(!JSON.stringify(params).includes('crops/'))
      const group = snapshot.groups.find(group => group.id === params.groupId)
      const changes = params.action === 'restoreAutomatic' ? group.frames.map(frame => ({ frameId: frame.id, selected: frame.automatic })) : params.changes
      for (const change of changes) {
        const frame = group.frames.find(frame => frame.id === change.frameId); frame.selected = change.selected; frame.manual = true
      }
      group.manualReviewed = true; snapshot.revision++; return { ok: true, result: snapshot }
    }
    if (message.action === 'preferences') {
      assert.equal(params.expectedRevision, snapshot.revision); snapshot.preferences.exitAction = params.exitAction; snapshot.revision++
      return { ok: true, result: snapshot }
    }
    if (message.action === 'cleanupPreview') {
      cleanupOptions = params
      return { ok: true, result: { token: 'a'.repeat(32), revision: snapshot.revision, expiresAt: Date.now() + 60000,
        keepCount: 2, photoCount: 1, rawCount: params.includeRaw ? 1 : 0, zeroSkipped: params.includeZero ? 0 : 1,
        newFramesSkipped: 1, unstableSkipped: 0, groups: ['g00002'],
        files: [{ id: 'synthetic-file', frameId: 'p000001', groupId: 'g00002', name: 'synthetic-unselected.jpg', kind: 'photo' }],
        skipped: [{ groupId: 'g00001', reason: 'zeroSkipped' }] } }
    }
    if (message.action === 'cleanupCancel') return { ok: true, result: { cancelled: true } }
    if (message.action === 'cleanupExecute') return { ok: true, result: { cancelled: true, results: [], snapshot } }
    return { ok: false, error: 'unrecognized test action' }
  })
  await page.addInitScript(() => {
    const deliver = data => {
      const event = new Event('message')
      Object.defineProperties(event, { origin: { value: 'null' }, source: { value: window.parent }, data: { value: data } })
      window.dispatchEvent(event)
    }
    const parent = { postMessage: async message => {
      if (message.type === 'birdshot-review-ready') {
        deliver({ type: 'birdshot-review-capabilities', writable: true })
        return
      }
      const response = await window.__nativeRequest(message)
      if (response) deliver({ type: 'birdshot-review-response', id: message.id, ...response })
    } }
    Object.defineProperty(window, 'parent', { value: parent })
  })
  await page.reload(); await ready()
  await page.waitForFunction(() => !document.querySelector('#cleanup').disabled)
  await page.locator('.group-open').first().click(); await page.locator('#stage .source-image').waitFor()
  let decisions = () => operations.filter(operation => operation.action === 'decide')
  await page.locator('#stage').click({ position: { x: 200, y: 250 } })
  await page.keyboard.press('Space')
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().at(-1).params.changes[0].selected, false)
  assert.equal(snapshot.groups[0].frames[0].selected, false)
  assert.equal(await page.locator('.frame-check input').first().isChecked(), false)
  await page.keyboard.press('w'); await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().at(-1).params.changes[0].selected, true)
  await page.keyboard.press('s'); await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().at(-1).params.changes[0].selected, false)
  const count = decisions().length
  await page.locator('#stage').evaluate(node => node.dispatchEvent(new KeyboardEvent('keydown', { key: 'w', repeat: true, bubbles: true })))
  assert.equal(decisions().length, count)
  failSave = true
  await page.keyboard.press('w')
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.includes('版本冲突'))
  assert.equal(await page.locator('.frame-check input').first().isChecked(), false, 'failed save never paints a false selected state')
  await page.locator('#multiselect').click()
  const beforeCheck = decisions().length
  await page.locator('.frame-check input').first().check()
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().length, beforeCheck + 1, 'checkbox toggles exactly once')
  assert.equal(decisions().at(-1).params.changes[0].selected, true)
  const beforeTile = decisions().length
  await page.locator('.frame-open').nth(1).click()
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().length, beforeTile + 1)
  assert.equal(decisions().at(-1).params.changes[0].frameId, 'p000001')
  assert.equal(decisions().at(-1).params.changes[0].selected, true)
  await page.locator('#multiselect').click()
  const longCount = decisions().length
  const tileBounds = await page.locator('.frame-open').nth(1).boundingBox()
  await page.mouse.move(tileBounds.x + 30, tileBounds.y + 30); await page.mouse.down()
  await page.waitForFunction(() => document.querySelector('#multiselect').getAttribute('aria-pressed') === 'true')
  await page.mouse.up()
  assert.equal(decisions().length, longCount, 'longpress enters multiselect without accidental synthesized toggle')
  await page.locator('#restore').click()
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().at(-1).params.action, 'restoreAutomatic')
  await page.locator('#undo').click()
  await page.waitForFunction(() => document.querySelector('#immersive-status').textContent.startsWith('已保存'))
  assert.equal(decisions().at(-1).params.action, 'undo')

  // Initial low preview stays installed while a delayed higher-quality image decodes.
  await page.locator('#previous-frame').click(); await page.locator('#stage .source-image').waitFor()
  decodeDelay = 650
  await page.mouse.move(bounds.x + 200, bounds.y + 150); await page.keyboard.down('Control'); await page.mouse.wheel(0, -650); await page.keyboard.up('Control')
  const upgradedView = await normalized()
  await page.waitForFunction(() => /\/(960|1920|3840)\.jpg$/.test(document.querySelector('#stage .source-image')?.src || ''))
  assert.deepEqual(await normalized(), upgradedView, 'decode replacement keeps transform')
  decodeDelay = 0
  originalWrong = true
  await page.keyboard.down('Control'); await page.mouse.wheel(0, -2000); await page.keyboard.up('Control')
  await page.waitForFunction(() => document.querySelector('#resolution-status').textContent.includes('原文件尺寸不符'), { timeout: 20000 })
  assert.ok(!(await page.locator('#resolution-status').textContent()).startsWith('原文件像素已核对'))
  originalWrong = false

  await page.locator('#exit').click()
  assert.equal(await page.locator('#exit-dialog').evaluate(node => node.open), true, JSON.stringify({
    preferences: snapshot.preferences, status: await page.locator('#immersive-status').textContent(), errors,
    dialogs: await page.locator('dialog').evaluateAll(nodes => nodes.map(node => [node.id, node.open, node.getAttribute('open')]))
  }))
  assert.equal(await page.locator('#remember-exit').isChecked(), false)
  await page.locator('#exit-cleanup').click()
  await page.waitForFunction(() => !document.querySelector('#cleanup-execute').disabled)
  assert.equal(cleanupOptions.includeRaw, false)
  assert.equal(cleanupOptions.includeZero, false)
  assert.match(await page.locator('#cleanup-summary').textContent(), /跳过零保留 1/)
  assert.match(await page.locator('#cleanup-files').textContent(), /synthetic-unselected.jpg/)
  assert.equal(operations.some(operation => operation.action === 'cleanupExecute'), false)
  await page.locator('#include-zero').click()
  assert.equal(await page.locator('#zero-dialog').evaluate(node => node.open), true)
  assert.equal(await page.locator('#include-zero').isChecked(), false)
  await page.locator('#zero-confirm').click()
  await page.waitForFunction(() => !document.querySelector('#cleanup-execute').disabled)
  assert.equal(cleanupOptions.includeZero, true)
  await page.locator('#cleanup-cancel').click()
  snapshot.preferences.exitAction = 'cleanup'
  await page.locator('#refresh').evaluate(button => button.click())
  await page.waitForFunction(() => !document.querySelector('#refresh').disabled)
  await page.locator('#exit').click(); await page.waitForFunction(() => !document.querySelector('#cleanup-execute').disabled)
  assert.equal(await page.locator('#cleanup-dialog').evaluate(node => node.open), true, 'cleanup preference still requires this preview')
  assert.equal(cleanupOptions.includeZero, false)
  await page.locator('#cleanup-execute').click()
  await page.waitForFunction(() => document.querySelector('#trash-progress').textContent.includes('系统确认已取消'))
  assert.equal(await page.locator('#immersive').isVisible(), true)
  await page.locator('#cleanup-cancel').click()
  snapshot.preferences.exitAction = 'ask'
  await page.locator('#refresh').evaluate(button => button.click())
  await page.waitForFunction(() => !document.querySelector('#refresh').disabled)
  await page.locator('#exit').click(); await page.locator('#remember-exit').check(); await page.locator('#exit-only').click()
  assert.equal(snapshot.preferences.exitAction, 'exit')
  assert.equal(await page.locator('#immersive').isHidden(), true)
  await page.locator('#reset-exit').click()
  await page.waitForFunction(() => document.querySelector('#save-status').textContent.includes('已重置'))
  assert.equal(snapshot.preferences.exitAction, 'ask')
  await page.reload(); await ready()
  assert.equal(snapshot.groups[0].manualReviewed, true, 'state persists through reload via the bridge snapshot')

  for (const width of [360, 390, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${width} homepage has no horizontal overflow`)
    await page.locator('.group-open').first().click()
    await page.locator('#stage .source-image').waitFor()
    assert.ok(await page.evaluate(() => document.querySelector('#immersive').scrollWidth <= innerWidth), `${width} immersive has no horizontal overflow`)
    if (width < 700) {
      await page.locator('#drawer-toggle').click()
      assert.ok(await page.locator('#review-drawer').evaluate(node => node.scrollWidth <= node.clientWidth))
      await page.locator('#drawer-close').click()
      await page.locator('#help-button').click()
      assert.equal(await page.locator('#help').isVisible(), true)
      assert.equal(await page.locator('#help').evaluate(node => getComputedStyle(node).pointerEvents), 'none')
      await page.locator('#help-button').click()
      const sourceBounds = await page.locator('#stage').boundingBox()
      const touch = await page.context().newCDPSession(page)
      const firstTouch = { id: 11, x: sourceBounds.x + 80, y: sourceBounds.y + 150 }
      const secondTouch = { id: 12, x: sourceBounds.x + 180, y: sourceBounds.y + 150 }
      await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [firstTouch] })
      await touch.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [firstTouch, secondTouch] })
      await touch.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [firstTouch, { ...secondTouch, x: sourceBounds.x + 240 }] })
      await touch.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
      await touch.detach()
      assert.ok((await normalized()).scale > 1)
      await page.locator('#next-frame').click()
      assert.match(await page.locator('#frame-title').textContent(), /^2 \/ 3/)
      await page.locator('#next-group').click()
      assert.equal(await page.locator('#group-title').textContent(), '无鸟第二组')
    }
    await page.locator('#exit').click(); await page.locator('#exit-only').click()
  }
  assert.ok(modes.includes(true) && modes.includes(false))
  assert.deepEqual(external, [], 'references are locally served and no private photo is loaded')
  assert.deepEqual(errors, [])
  console.log('review UI: group order, source overlays, progressive decode, desktop persistence, cleanup safety, keyboard and 360/390/desktop layouts passed (synthetic only)')
} finally {
  if (browser) await browser.close()
  await new Promise(resolve => server.close(resolve))
}
