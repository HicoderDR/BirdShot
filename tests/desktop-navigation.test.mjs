// SPDX-License-Identifier: AGPL-3.0-only
// Synthetic fixtures only: no user library, photo, output directory or existing service is read.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { chromium } from 'playwright'

const gallery = await readFile(new URL('../scripts/gallery-report.html', import.meta.url))
const bridge = (await readFile(new URL('../scripts/batch_culling.py', import.meta.url), 'utf8'))
  .match(/data = b"""(<!doctype html>[\s\S]*?)"""/)?.[1]
assert.ok(bridge, 'Use the actual unprivileged HTTP bridge, not a second implementation')
const desktopFiles = new Map(await Promise.all(['index.html', 'app.js', 'style.css'].map(async name => [
  '/desktop/' + name, await readFile(new URL('../desktop/renderer/' + name, import.meta.url)),
])))
const syntheticImage = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><text x="0" y="50">SYNTHETIC</text></svg>'
function fixture() {
  const items = Array.from({ length: 56 }, (_, index) => {
    const rank = index + 1
    return {
      id: 'synthetic-' + rank, rank, groupId: 'g' + String(Math.ceil(rank / 2)).padStart(5, '0'),
      frameId: 'p' + String(rank === 2 ? 1 : rank).padStart(6, '0'), instanceId: rank === 2 ? 1 : 0,
      crop: 'crops/synthetic-' + rank + '.jpg', thumbnail: 'crops/synthetic-' + rank + '.jpg',
      sourceLabel: 'synthetic-' + String(rank).padStart(3, '0') + '.jpg',
      reason: 'Synthetic fixture only', decision: [2, 56].includes(rank) ? 'review' : 'keep',
      birdVerification: { status: [2, 56].includes(rank) ? 'unknown' : 'bird' },
      quality: { tier: rank % 2 ? 'good' : 'excellent' },
      shootGroup: { id: 'old', name: '旧拍摄组' },
      navigationGroups: { date: rank <= 52 ? 'day-1' : 'day-2', folder: rank === 2 || rank % 2 ? 'source-a/photos' : 'source-b/photos', place: rank % 3 ? 'unknown' : 'trusted' },
    }
  })
  const definitions = {
    date: [['day-1', '2001-02-03'], ['day-2', '2001-02-04']],
    folder: [['source-a/photos', '合成来源甲 / photos'], ['source-b/photos', '合成来源乙 / photos']],
    place: [['unknown', '未知地点'], ['trusted', '合成可信地点']],
  }
  const groups = Object.fromEntries(Object.entries(definitions).map(([mode, entries]) => [mode, entries.map(([id, name]) => {
    const groupItems = items.filter(item => item.navigationGroups[mode] === id)
    return { id, name, photoCount: new Set(groupItems.map(item => item.frameId)).size + (mode === 'folder' && id === 'source-a/photos' ? 2 : 0), cropCount: groupItems.length, preferredCount: groupItems.filter(item => item.decision === 'keep').length }
  })]))
  return {
    version: 1, status: 'completed', updatedAt: '2026-11-16T12:00:00Z',
    progress: { total: 56, processed: 56 },
    summary: { preferred: 54, bird: 54, nonBird: 0, unknown: 2, oldSelected: 0, removedOld: 0, addedNew: 54 },
    navigation: { modes: ['date', 'folder', 'place'], groups },
    shootGroups: [{ id: 'old', name: '旧拍摄组', photoCount: 55, cropCount: 56, preferredCount: 54 }],
    items: items.reverse(),
  }
}
let payload = fixture()
const requests = []
const server = createServer((request, response) => {
  const path = new URL(request.url, 'http://127.0.0.1').pathname
  requests.push(path)
  response.setHeader('Cache-Control', 'no-store')
  if (desktopFiles.has(path)) {
    response.setHeader('Content-Type', path.endsWith('.html') ? 'text/html; charset=utf-8' : path.endsWith('.js') ? 'text/javascript' : 'text/css')
    response.end(desktopFiles.get(path))
  } else if (path === '/gallery-refresh-bridge') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(bridge)
  } else if (['/', '/gallery/'].includes(path)) {
    response.setHeader('Content-Type', 'text/html; charset=utf-8'); response.end(gallery)
  } else if (path === '/gallery.json') {
    response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify(payload))
  } else if (/^\/gallery\/group\/g(?:\d{5}|[a-f0-9]{24})\.json$/.test(path)) {
    const groupId = path.split('/').at(-1).slice(0, -5)
    const items = payload.items.filter(item => item.groupId === groupId)
    response.setHeader('Content-Type', 'application/json')
    response.end(JSON.stringify({ id: groupId, frames: items.map(item => ({
      id: item.frameId, width: 4000, height: 6000, source: { primary: { relative: item.sourceLabel } },
      instances: [{ ...item, id: item.instanceId, cropRect: [0, 0, 100, 100] }],
    })) }))
  } else if (/^\/preview\/p[a-f0-9]{24}\/(960|1920|3840)\.jpg$/.test(path) || /^\/original\/p[a-f0-9]{24}$/.test(path)) {
    response.setHeader('Content-Type', 'image/svg+xml')
    response.end(syntheticImage.replace('width="100" height="100"', 'width="4000" height="6000"'))
  } else if (path.startsWith('/media/')) {
    response.setHeader('Content-Type', 'image/svg+xml'); response.end(syntheticImage)
  } else { response.writeHead(404); response.end('Synthetic route not found') }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = 'http://127.0.0.1:' + server.address().port
let browser
try {
  browser = await chromium.launch({ channel: process.env.BIRDSHOT_BROWSER_CHANNEL ?? 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const ready = target => target.waitForFunction(() => document.getElementById('gallery').getAttribute('aria-busy') === 'false' && !document.getElementById('refresh').disabled)
  const ids = () => page.locator('.tile-open').evaluateAll(nodes => nodes.map(node => node.dataset.itemId))
  const refresh = async () => { await page.locator('#refresh').click(); await ready(page) }
  await page.goto(origin)
  await ready(page)
  assert.equal(await page.locator('#navigation-mode').inputValue(), 'date')
  assert.equal(await page.locator('#navigation-mode').isEnabled(), true)
  assert.equal(await page.locator('#shoot-group option').count(), 3)
  const originalScoring = await page.evaluate(() => JSON.stringify(state.index.items.map(({ id, rank, groupId, quality }) => ({ id, rank, groupId, quality }))))
  await page.locator('#shoot-group').selectOption('day-1')
  assert.match(await page.locator('#shoot-group-status').textContent(), /原图 51 · 裁剪 52 · 优选 51/)
  assert.equal(await page.locator('.tile').count(), 48)
  await page.locator('#next').click()
  assert.deepEqual(await ids(), ['synthetic-50', 'synthetic-51', 'synthetic-52'])
  await page.locator('#search').fill('synthetic-003')
  await page.locator('#tier').selectOption('good')
  assert.deepEqual(await ids(), ['synthetic-3'])
  assert.match(await page.locator('#page-label').textContent(), /^1 \/ 1/)
  await page.locator('#search').fill('')
  await page.locator('#tier').selectOption('')
  await page.locator('#review-tab').click()
  await page.locator('#verification').selectOption('unknown')
  assert.deepEqual(await ids(), ['synthetic-2'])
  await page.locator('#tier').selectOption('good')
  assert.equal(await page.locator('.tile').count(), 0)
  await page.locator('#tier').selectOption('')
  await page.locator('#verification').selectOption('')
  await page.locator('#preferred-tab').click()
  await page.locator('#navigation-mode').selectOption('folder')
  await page.locator('#shoot-group').selectOption('source-b/photos')
  const folderIds = await ids()
  assert.equal(folderIds.length, 26)
  assert.equal(folderIds.every(id => Number(id.split('-')[1]) % 2 === 0), true)
  await page.reload()
  await ready(page)
  assert.equal(await page.locator('#navigation-mode').inputValue(), 'folder')
  assert.equal(await page.locator('#shoot-group').inputValue(), 'source-b/photos')
  assert.deepEqual(await ids(), folderIds)
  await page.locator('#shoot-group').selectOption('source-a/photos')
  assert.match(await page.locator('#shoot-group-status').textContent(), /原图 30 · 裁剪 29 · 优选 28/, '原图包含零裁剪照片，不能由卡片数替代')
  await page.locator('#navigation-mode').selectOption('date')
  assert.equal(await page.locator('#shoot-group').inputValue(), 'day-1', '同日跨文件夹合并且按方式记忆选择')
  await page.locator('#navigation-mode').selectOption('place')
  await page.locator('#shoot-group').selectOption('unknown')
  assert.equal((await ids()).every(id => Number(id.split('-')[1]) % 3 !== 0), true, '未知地点统一按后端 id 筛选')
  await page.locator('#collapse').check()
  const collapsed = await page.locator('.tile-open').evaluateAll(nodes => nodes.map(node => node.dataset.itemId))
  assert.equal(new Set(collapsed.map(id => Math.ceil(Number(id.split('-')[1]) / 2))).size, collapsed.length)
  await page.locator('#collapse').uncheck()
  assert.equal(await page.evaluate(() => JSON.stringify(state.index.items.map(({ id, rank, groupId, quality }) => ({ id, rank, groupId, quality })))), originalScoring)
  assert.equal(requests.some(path => path.includes('inference') || path.startsWith('/gallery/group/')), false)
  payload.navigation.groups.place = payload.navigation.groups.place.filter(group => group.id !== 'unknown')
  payload.items.forEach(item => { if (item.navigationGroups.place === 'unknown') item.navigationGroups.place = 'trusted' })
  await refresh()
  assert.equal(await page.locator('#shoot-group').inputValue(), '')
  assert.equal(await page.locator('.tile').count(), 48, '已删除选择回到全部')
  payload = fixture()
  payload.navigation.groups.date[0].photoCount = '52'
  await refresh()
  assert.match(await page.locator('#shoot-group-status[role=alert]').textContent(), /分类目录格式无效/)
  assert.equal(await page.locator('.tile').count(), 48)
  payload = fixture()
  delete payload.items[0].navigationGroups.folder
  await refresh()
  assert.match(await page.locator('#shoot-group-status[role=alert]').textContent(), /navigationGroups/)
  assert.equal(await page.locator('.tile').count(), 48)
  payload = fixture()
  delete payload.navigation
  await refresh()
  assert.equal(await page.locator('#navigation-mode').isDisabled(), true)
  await page.locator('#shoot-group').selectOption('old')
  assert.match(await page.locator('#shoot-group-status').textContent(), /原图 55 · 裁剪 56 · 优选 54/)
  payload = fixture()
  const stableFrameId = 'p' + 'ab'.repeat(12)
  const stableGroupId = 'g' + 'cd'.repeat(12)
  Object.assign(payload.items.find(item => item.rank === 1), { frameId: stableFrameId, groupId: stableGroupId })
  await refresh()
  await page.locator('.tile-open[data-item-id="synthetic-1"]').click()
  await page.locator('#focus-panel').waitFor()
  assert.equal(await page.locator('.original-toggle').isEnabled(), true, '稳定的聚合照片ID支持完整画面')
  await page.locator('.original-toggle').click()
  await page.waitForFunction(() => state.preview?.mode === 'original' && !state.preview.loading)
  await page.locator('#focus-panel .crop-wrap img:visible').click()
  await page.locator('#photo-viewer[open]').waitFor()
  assert.equal(await page.locator('#photo-download').isVisible(), true)
  assert.equal(await page.locator('#photo-download').getAttribute('href'), '/original/' + stableFrameId + '?download=1')
  assert.equal(await page.evaluate(() => validFrameId('p' + 'ab'.repeat(12) + '/..')), false)
  await page.locator('#photo-exit').click()
  await page.locator('#close').click()
  payload = fixture()
  await page.close()

  const management = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  management.on('pageerror', error => errors.push(error.message))
  await management.addInitScript(({ origin }) => {
    if (!location.pathname.startsWith('/desktop/')) return
    let state = {
      version: 1, instanceId: 'synthetic-desktop',
      runtime: { ready: false, models: [{ id: 'birder', path: 'SYNTHETIC-MODEL', ready: false, error: '合成模型尚未准备' }] },
      sources: [], outputRoot: '',
      service: { status: 'stopped', mode: 'local', port: 5274, localUrl: null, lanUrls: [] },
    }
    let listener
    const calls = []
    window.testDesktop = {
      calls, state: () => structuredClone(state),
      emit: next => { state = structuredClone(next); listener(structuredClone(state)) },
    }
    const call = (method, implementation) => async (...args) => {
      calls.push({ method, args })
      if (implementation) implementation(...args)
      return structuredClone(state)
    }
    window.birdshotDesktop = {
      getState: call('getState'),
      chooseSource: call('chooseSource', () => state.sources.push({ id: 'synthetic-source', name: '合成来源 <img src=x onerror=alert(1)>', path: 'SYNTHETIC:\\photos', status: 'queued' })),
      attachExisting: call('attachExisting', () => state.sources.push({ id: 'synthetic-old', name: '合成旧批次', path: 'SYNTHETIC:\\legacy', job: 'SYNTHETIC:\\job', status: 'completed' })),
      chooseOutput: call('chooseOutput', () => { state.outputRoot = 'SYNTHETIC:\\output' }),
      removeSource: call('removeSource', id => { state.sources = state.sources.filter(source => source.id !== id) }),
      rescan: call('rescan', id => { state.sources.find(source => source.id === id).status = 'processing' }),
      configureService: call('configureService', config => {
        state.service = config.port === 5274
          ? { ...state.service, ...config, status: 'error', error: '合成端口已被占用；未接管已有服务', suggestedPort: 5275 }
          : { ...config, status: 'ready', localUrl: origin + '/gallery/', lanUrls: config.mode === 'local' ? [] : ['http://192.0.2.1:5275/'] }
      }),
      onState: callback => { listener = callback; return () => { listener = null } },
    }
  }, { origin })
  await management.goto(origin + '/desktop/index.html')
  await management.locator('#runtime-status').filter({ hasText: '尚未就绪' }).waitFor()
  assert.match(await management.locator('#gallery-message').textContent(), /添加来源目录或接入旧批次/)
  assert.equal(await management.locator('#gallery-frame').getAttribute('src'), null)
  assert.equal(await management.locator('#service-mode').inputValue(), 'local')
  assert.equal(await management.locator('#service-port').inputValue(), '5274')
  assert.deepEqual(await management.evaluate(() => testDesktop.calls), [{ method: 'getState', args: [] }], '初始化不抢占服务、不重推理')
  await management.locator('#choose-source').click()
  await management.locator('[data-source-id="synthetic-source"]').waitFor()
  assert.equal(await management.locator('#source-list img').count(), 0)
  await management.locator('#attach-existing').click()
  await management.locator('[data-source-id="synthetic-old"]').waitFor()
  await management.locator('#choose-output').click()
  await management.locator('#output-root').filter({ hasText: 'SYNTHETIC:\\output' }).waitFor()
  assert.deepEqual(await management.evaluate(() => testDesktop.calls.filter(call => ['chooseSource', 'attachExisting', 'chooseOutput'].includes(call.method))), [
    { method: 'chooseSource', args: [] }, { method: 'attachExisting', args: [] }, { method: 'chooseOutput', args: [] },
  ], 'renderer 仅发起原生选择器，不传入路径')
  await management.evaluate(() => {
    const next = testDesktop.state()
    Object.assign(next.sources[0], { status: 'error', error: '合成扫描失败', progress: { phase: '扫描', totalPhotos: 10, processedPhotos: 3, totalGroups: 2, completedGroups: 1, cropCount: 4 } })
    testDesktop.emit(next)
  })
  assert.match(await management.locator('[data-source-id="synthetic-source"]').textContent(), /原图 3 \/ 10 · 组 1 \/ 2 · 裁剪 4/)
  assert.equal(await management.locator('[data-source-id="synthetic-source"] progress').getAttribute('value'), '3')
  await management.locator('[data-source-id="synthetic-source"]').getByRole('button', { name: '恢复 / 检查新增照片' }).click()
  await management.locator('[data-source-id="synthetic-old"]').getByRole('button', { name: '移除（仅解绑）' }).click()
  assert.deepEqual(await management.evaluate(() => testDesktop.calls.filter(call => ['rescan', 'removeSource'].includes(call.method))), [
    { method: 'rescan', args: ['synthetic-source'] }, { method: 'removeSource', args: ['synthetic-old'] },
  ])
  await management.locator('#network-settings').evaluate(element => { element.open = true })
  await management.locator('#apply-service').click()
  await management.locator('#service-status[role=alert]').waitFor()
  assert.match(await management.locator('#gallery-message').textContent(), /端口已被占用/)
  assert.equal(await management.locator('#gallery-frame').getAttribute('src'), null)
  await management.locator('#apply-suggested-port').click()
  await management.locator('#gallery-frame').waitFor({ state: 'visible' })
  const frame = management.frameLocator('#gallery-frame')
  await frame.locator('.tile').first().waitFor()
  assert.equal(await management.locator('#gallery-frame').getAttribute('src'), origin + '/gallery/')
  assert.equal(await frame.locator('body').evaluate(() => typeof window.birdshotDesktop), 'undefined', '普通图库 iframe 不持有管理 API')
  assert.equal(await management.locator('#service-port').inputValue(), '5275')
  await management.locator('#service-mode').selectOption('lan')
  assert.equal(await management.locator('#share-risk').isVisible(), true)
  assert.match(await management.locator('#share-risk').textContent(), /没有身份认证/)
  const callsBefore = await management.evaluate(() => testDesktop.calls.length)
  await management.locator('#apply-service').click()
  assert.equal(await management.evaluate(() => testDesktop.calls.length), callsBefore)
  assert.match(await management.locator('#action-error').textContent(), /勾选确认/)
  await management.locator('#share-confirm').check()
  await management.locator('#apply-service').click()
  await management.locator('#service-addresses').filter({ hasText: '192.0.2.1' }).waitFor()
  assert.deepEqual((await management.evaluate(() => testDesktop.calls.filter(call => call.method === 'configureService'))).map(call => call.args[0]), [
    { mode: 'local', port: 5274 }, { mode: 'local', port: 5275 }, { mode: 'lan', port: 5275 },
  ])
  payload = fixture()
  payload.items = []
  payload.summary = Object.fromEntries(Object.keys(payload.summary).map(key => [key, 0]))
  payload.progress = { processed: 0, total: 0 }
  payload.shootGroups = []
  payload.navigation.groups = { date: [], folder: [], place: [] }
  await management.evaluate(() => {
    const next = testDesktop.state()
    next.sources = []
    testDesktop.emit(next)
  })
  await frame.locator('#empty-title').filter({ hasText: '尚无通过验鸟并保留的鸟体' }).waitFor()
  await frame.locator('#navigation-mode').selectOption('folder')
  const galleryLoads = () => requests.filter(path => path === '/gallery/').length
  const emptyLoads = galleryLoads()
  payload = fixture()
  payload.status = 'processing'
  await management.evaluate(() => {
    const next = testDesktop.state()
    next.sources = [{ id: 'synthetic-new', name: '合成新增来源', path: 'SYNTHETIC:\\new', job: 'SYNTHETIC:\\new-job', status: 'processing', progress: { processed: 1, total: 56 } }]
    testDesktop.emit(next)
  })
  await frame.locator('.tile').first().waitFor()
  assert.equal(galleryLoads(), emptyLoads, '来源集合改变只刷新索引，不重新导航iframe')
  await frame.locator('#shoot-group').selectOption('source-b/photos')
  await frame.locator('.tile-open').first().click()
  await frame.locator('#focus-panel .crop-wrap img:visible').click()
  await frame.locator('#photo-viewer[open]').waitFor()
  const processingLoads = galleryLoads()
  const indexReads = () => requests.filter(path => path === '/gallery.json').length
  const beforeInvalidMessages = indexReads()
  await frame.locator('body').evaluate(() => {
    for (const [origin, source, data] of [
      ['https://invalid.example', parent, { type: 'birdshot:refresh-index' }],
      [location.origin, window, { type: 'birdshot:refresh-index' }],
      [location.origin, parent, { type: 'birdshot:refresh-index', action: 'inference' }],
    ]) window.dispatchEvent(new MessageEvent('message', { origin, source, data }))
  })
  await management.waitForTimeout(100)
  assert.equal(indexReads(), beforeInvalidMessages, '刷新通知严格限定同源父frame与单一无IPC消息')
  await management.evaluate(() => {
    for (let processed = 2; processed <= 4; processed++) {
      const next = testDesktop.state()
      next.sources[0].progress.processed = processed
      testDesktop.emit(next)
    }
  })
  await management.waitForTimeout(150)
  assert.equal(galleryLoads(), processingLoads, '普通进度事件不刷新iframe')
  assert.equal(await frame.locator('#photo-viewer').evaluate(element => element.open), true, '进度事件不重置viewer')
  payload.status = 'completed'
  await management.evaluate(() => {
    const next = testDesktop.state()
    next.sources[0].status = 'completed'
    testDesktop.emit(next)
  })
  await frame.locator('#status').filter({ hasText: '索引已完成' }).waitFor()
  assert.equal(galleryLoads(), processingLoads, 'processing→completed只刷新最终索引')
  assert.equal(await frame.locator('#photo-viewer').evaluate(element => element.open), true, '最终发布仍保留已打开viewer')
  assert.equal(await frame.locator('#shoot-group').inputValue(), 'source-b/photos', '最终加载保留仍合法的localStorage筛选')
  assert.equal(await frame.locator('.tile').count(), 26)
  assert.equal(await management.locator('#gallery-frame').getAttribute('src'), origin + '/gallery/')
  await management.evaluate(() => {
    testDesktop.emit(testDesktop.state())
    testDesktop.emit(testDesktop.state())
  })
  await management.waitForTimeout(150)
  assert.equal(galleryLoads(), processingLoads, '重复completed状态事件去重')
  for (const localUrl of ['https://example.invalid/', 'http://localhost:5275/', 'http://127.0.0.1:5275@evil.invalid/', 'file:///SYNTHETIC:/photos']) {
    await management.evaluate(localUrl => {
      const next = testDesktop.state()
      next.service.localUrl = localUrl
      testDesktop.emit(next)
    }, localUrl)
    assert.equal(await management.locator('#gallery-frame').getAttribute('src'), null)
    assert.match(await management.locator('#gallery-message[role=alert]').textContent(), /图库地址无效/)
  }
  assert.match(await management.locator('meta[http-equiv="Content-Security-Policy"]').getAttribute('content'), /script-src 'self'; style-src 'self'; frame-src http:\/\/127\.0\.0\.1:\*; connect-src 'self'/)
  assert.equal(await management.locator('script:not([src]),[onclick],[onload],iframe[sandbox]').count(), 0, '外置脚本且不拦截图库原生手势')
  await management.close()
  const disconnected = await browser.newPage()
  await disconnected.goto(origin + '/desktop/index.html')
  await disconnected.locator('#gallery-message[role=alert]').waitFor()
  assert.match(await disconnected.locator('#action-error').textContent(), /共享图库不提供管理能力/)
  assert.equal(await disconnected.locator('#choose-source').isDisabled(), true)
  await disconnected.close()
  const rejected = await browser.newPage()
  await rejected.addInitScript(() => {
    window.birdshotDesktop = { getState: async () => { throw new Error('合成状态读取失败') }, onState: () => () => {} }
  })
  await rejected.goto(origin + '/desktop/index.html')
  await rejected.locator('#gallery-message[role=alert]').waitFor()
  assert.match(await rejected.locator('#gallery-message').textContent(), /无法读取本机照片库/)
  assert.equal(await rejected.locator('#refresh-state').isEnabled(), true)
  await rejected.close()
  payload = fixture()
  const fileManager = await browser.newPage()
  fileManager.on('pageerror', error => errors.push(error.message))
  await fileManager.addInitScript(({ origin }) => {
    if (location.protocol !== 'file:') return
    let listener
    const current = {
      version: 1, runtime: { ready: false, models: [] }, outputRoot: 'SYNTHETIC', sources: [],
      service: { status: 'ready', mode: 'local', localUrl: origin + '/gallery/', lanUrls: [] },
    }
    window.emitFileState = revision => {
      current.sources = [{ id: 'public-fixture', name: '公开测试', path: 'SYNTHETIC', status: 'processing',
        progress: { publishedRevision: revision, processedPhotos: 2, totalPhotos: 3, completedGroups: 1, totalGroups: 2 } }]
      listener(structuredClone(current))
    }
    window.birdshotDesktop = { getState: async () => structuredClone(current), onState: callback => { listener = callback; return () => {} } }
  }, { origin })
  await fileManager.goto(new URL('../desktop/renderer/index.html', import.meta.url).href)
  const fileFrame = fileManager.frameLocator('#gallery-frame')
  await fileFrame.locator('.tile-open').first().waitFor()
  await fileManager.waitForFunction(() => document.getElementById('gallery-refresh-bridge')?.contentWindow)
  await fileFrame.locator('.tile-open[data-item-id="synthetic-3"]').click()
  await fileFrame.locator('#focus-panel .crop-wrap img:visible').click()
  await fileFrame.locator('#photo-viewer[open]').waitFor()
  const fileLoads = requests.filter(path => path === '/gallery/').length
  payload.status = 'processing'
  await fileManager.evaluate(() => emitFileState(1))
  await fileFrame.locator('#status').filter({ hasText: '本地处理中' }).waitFor()
  assert.equal(requests.filter(path => path === '/gallery/').length, fileLoads, 'file-origin桌面通过同源只读bridge刷新，不能重载iframe')
  assert.equal(await fileFrame.locator('#photo-viewer').evaluate(element => element.open), true)
  payload.updatedAt = '2026-11-17T12:00:00Z'
  await fileManager.evaluate(() => emitFileState(2))
  const fileGallery = fileManager.frames().find(candidate => candidate.url() === origin + '/gallery/')
  await fileGallery.waitForFunction(() => state.index.updatedAt === '2026-11-17T12:00:00Z', undefined, { timeout: 5000 })
  assert.equal(await fileFrame.locator('#photo-viewer').evaluate(element => element.open), true, '逐组revision发布仍保留当前历史详情')
  assert.equal(requests.filter(path => path === '/gallery/').length, fileLoads)
  await fileManager.close()
  assert.deepEqual(errors, [])
  console.log('PASS: desktop manager + navigation, HTTP/file-origin revision refresh, stable IDs/viewer without reload, persistence, strict message origins, invalid metadata, conflict and sharing consent')
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
}
