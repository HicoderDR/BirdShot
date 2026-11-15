// SPDX-License-Identifier: AGPL-3.0-only
// Fixtures are synthetic. Optional local-index / explicitly selected JPEG checks stay in memory.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { chromium } from 'playwright'

const html = await readFile(new URL('../scripts/gallery-report.html', import.meta.url))
const placeholder = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#31432f"/><text x="35" y="160" fill="#d1e4a0" font-size="24">SIMULATED - NO PHOTO</text></svg>'
const hostile = '<img src=x onerror="window.injected=true">'
const makeItem = (rank, extra = {}) => ({
  id: 'simulated-' + rank,
  groupId: 'g' + String(Math.ceil(rank / 2)).padStart(5, '0'),
  frameId: 'p' + String(rank).padStart(6, '0'),
  instanceId: 0,
  crop: 'crops/模拟裁剪 #' + rank + '.jpg',
  thumbnail: 'crops/模拟缩略 #' + rank + '.jpg',
  sourceLabel: '模拟来源-' + String(rank).padStart(3, '0') + '.jpg',
  decision: 'keep',
  reason: '模拟数据：仅测试技术排序与决策展示，并非真实验鸟结果',
  birdVerification: { status: 'bird', model: '模拟分类器 - NOT A REAL RESULT', evidence: { simulation: true, birdMass: .8, nonBirdMass: .2, maxBird: .3, maxNonBird: .2, top1IsBird: true, margin: .1, reason: '仅接口测试' } },
  species: { top5: [
    { commonName: rank % 2 ? '模拟鸟种 A' : '模拟鸟种 B', englishName: 'Simulated bird A', scientificName: 'Simulation primus', cosine: .3, probability: .003333 },
    { commonName: '模拟第二候选', englishName: 'Simulated second bird', scientificName: 'Simulation secundus', probability: .002222 },
    { commonName: '模拟第三候选', scientificName: 'Simulation tertius', probability: .001111 },
    { commonName: '不得展示第四候选', scientificName: 'Simulation quartus', probability: .0009 },
    { commonName: '不得展示第五候选', scientificName: 'Simulation quintus', probability: .0008 },
  ], coverage: '模拟覆盖范围' },
  quality: { tier: rank % 2 ? 'good' : 'excellent', tierLabel: ['高可用原像素', '标准可用原像素', '原图触边风险', '低像素细节受限'][(rank - 1) % 4], withinGroupRelative: .9, withinGroupRank: 1, cohortSize: 2, bodyPixels: 12000, bodyTenengrad: 800, edgeRisk: false, representative: rank % 2 === 1 },
  rank,
  ...extra,
})
const baseline = () => {
  const items = Array.from({ length: 60 }, (_, index) => makeItem(index + 1))
  items.push(
    makeItem(61, { birdVerification: { status: 'nonBird', model: '模拟分类器', evidence: hostile }, species: { top5: [{ commonName: hostile }], coverage: '模拟' }, sourceLabel: hostile }),
    makeItem(62, { birdVerification: { status: 'unknown', model: '模拟分类器', evidence: '未知，不能当已验鸟' } }),
    makeItem(63, { decision: 'review', reason: '模拟已验鸟但技术证据不足' }),
  )
  return {
    version: 1, status: 'completed', updatedAt: '2026-11-15T04:00:00Z',
    progress: { processed: 63, total: 63 },
    summary: { preferred: 60, bird: 61, nonBird: 1, unknown: 1, oldSelected: 2, removedOld: 1, addedNew: 59 },
    items: items.reverse(),
  }
}
const shootFixture = () => {
  const data = baseline()
  const groups = [
    { id: 'sim-place-a-day-1', name: '模拟观鸟地甲 - 010203', date: '2001-02-03', placeName: '模拟观鸟地甲' },
    { id: 'sim-place-a-day-2', name: '模拟观鸟地甲 - 010204', date: '2001-02-04', placeName: '模拟观鸟地甲' },
    { id: 'sim-place-b-day-1', name: '模拟观鸟地乙 - 010203', date: '2001-02-03', placeName: '模拟观鸟地乙' },
    { id: 'sim-unknown-day-1', name: '未知地点 - 010203', date: '2001-02-03', placeName: null },
    { id: 'sim-place-a-no-date', name: '模拟观鸟地甲 - 日期未知', date: null, placeName: '模拟观鸟地甲' },
  ]
  for (const item of data.items) {
    const index = item.rank <= 50 || item.rank === 61 ? 0 : item.rank <= 54 ? 1 : item.rank <= 57 || item.rank === 62 ? 2 : item.rank <= 59 || item.rank === 63 ? 3 : 4
    item.shootGroup = groups[index]
    item.capture = { status: 'ready', time: index === 4 ? null : groups[index].date + 'T09:10:11', timeZone: null, timeSource: '模拟相机日期', location: null, placeName: groups[index].placeName, placeSource: index === 3 ? null : '模拟用户已确认地点（非GPS）', placeNote: '仅模拟分组，不是用户照片地点' }
  }
  const sharedPhoto = data.items.find(item => item.rank === 61)
  sharedPhoto.frameId = 'p000001'; sharedPhoto.groupId = 'g00001'; sharedPhoto.instanceId = 1
  data.shootGroups = groups.map(group => {
    const items = data.items.filter(item => item.shootGroup.id === group.id)
    return { ...group, photoCount: new Set(items.map(item => item.frameId)).size, cropCount: items.length, preferredCount: items.filter(item => item.decision === 'keep' && item.birdVerification.status === 'bird').length }
  })
  return data
}
const makeBird = (item, extra = {}) => ({
  id: item.instanceId, crop: item.crop, thumbnail: item.thumbnail, cropRect: [100, 200, 400, 300],
  bodyConfidence: .87, bodySharpness: 800, bodyPixels: 12000, trackId: null,
  mask: { path: 'M 120 220 L 450 220 L 450 480 Z' },
  posePoints: [{ index: 0, x: 150, y: 240, confidence: .9 }, { index: 1, x: 170, y: 240, confidence: .2 }],
  eyeIssue: '模拟眼睛不可见；不阻碍鸟体浏览',
  birdVerification: item.birdVerification, species: item.species, quality: item.quality,
  decision: item.decision, reason: item.reason,
  ...extra,
})
let payload = baseline(), indexStatus = 200, detailStatus = 200, indexDelay = 0, detailDelay = 0, originalStatus = 200, originalDelay = 0, previewStatus = 200, previewDelay = 0, missingInstance = false, missingRect = false
let groupOverride = null, referenceMode = 'ready'
const referenceRequests = [], referenceDelays = new Map()
const frameCaptures = new Map()
const frameShootGroups = new Map()
const frameDimensions = new Map()
const requests = []
function groupData(id) {
  const items = baseline().items.filter(item => item.groupId === id)
  const frames = items.map(item => ({
    id: item.frameId, source: { primary: { relative: item.sourceLabel } },
    ...(frameDimensions.get(item.frameId) || { width: 4000, height: 6000 }),
    capture: frameCaptures.get(item.frameId),
    shootGroup: frameShootGroups.get(item.frameId),
    decision: 'keep', reason: '旧版帧级理由不应冒充实例分类',
    selectedInstance: 0, selectedCrop: 'old/selected.jpg',
    instances: [makeBird(item)],
  }))
  const item = items[0]
  frames[0].instances.push(
    makeBird(item, { id: 1, birdVerification: { status: 'nonBird', model: '模拟分类器', evidence: '模拟非鸟' }, decision: 'reject', reason: '模拟非鸟不入选' }),
    makeBird(item, { id: 2, birdVerification: { status: 'unknown', model: '模拟分类器', evidence: '模拟未知' }, decision: 'review', reason: '模拟待审不入选', posePoints: [] }),
    makeBird(item, { id: 3, birdVerification: undefined, decision: undefined, reason: undefined, quality: undefined }),
  )
  frames.push({ id: 'p999999', source: { primary: { relative: '模拟未检出.jpg' } }, reason: '模拟未检出', instances: [] })
  if (missingRect) for (const frame of frames) for (const bird of frame.instances) bird.cropRect = null
  if (missingInstance) for (const frame of frames) frame.instances = []
  return { id, frames }
}
const server = createServer(async (request, response) => {
  const path = new URL(request.url, 'http://127.0.0.1').pathname
  requests.push(path)
  response.setHeader('Cache-Control', 'no-store')
  if (path === '/') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); response.end(html)
  } else if (path === '/gallery.json') {
    if (indexDelay) await new Promise(resolve => setTimeout(resolve, indexDelay))
    response.writeHead(indexStatus, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(payload))
  } else if (/^\/gallery\/group\/g\d{5}\.json$/.test(path)) {
    if (detailDelay) await new Promise(resolve => setTimeout(resolve, detailDelay))
    response.writeHead(detailStatus, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(groupOverride || groupData(path.split('/').at(-1).slice(0, -5))))
  } else if (path === '/species-reference') {
    const name = new URL(request.url, 'http://127.0.0.1').searchParams.get('scientificName')
    referenceRequests.push(name)
    if (referenceDelays.has(name)) await new Promise(resolve => setTimeout(resolve, referenceDelays.get(name)))
    response.writeHead(referenceMode === 'error' ? 503 : 200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ status: referenceMode, scientificName: name,
      article: referenceMode === 'ready' ? { title: '模拟百科', url: 'https://zh.wikipedia.org/wiki/Synthetic_test', language: 'zh' } : null,
      image: referenceMode === 'ready' ? { url: '/species-media/' + 'a'.repeat(64) + '.jpg', sourceUrl: 'https://commons.wikimedia.org/wiki/File:Synthetic_test.jpg', author: hostile, license: 'CC BY-SA 4.0', licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/' } : null,
      message: referenceMode === 'error' ? '模拟公开网络超时' : null,
    }))
  } else if (path.startsWith('/species-media/')) {
    response.writeHead(200, { 'Content-Type': 'image/svg+xml' }); response.end(placeholder)
  } else if (path === '/media/crops/missing-thumb.jpg') {
    response.writeHead(404); response.end('Synthetic missing thumbnail')
  } else if (/^\/preview\/p\d{6}\/(960|1920|3840)\.jpg$/.test(path)) {
    if (previewDelay) await new Promise(resolve => setTimeout(resolve, previewDelay))
    const dimensions = frameDimensions.get(path.split('/')[2]) || { width: 4000, height: 6000 }
    const factor = Math.min(1, Number(path.split('/').at(-1).slice(0, -4)) / Math.max(dimensions.width, dimensions.height))
    response.writeHead(previewStatus, { 'Content-Type': previewStatus === 200 ? 'image/svg+xml' : 'text/plain' }); response.end(previewStatus === 200 ? placeholder.replace('width="400" height="300"', 'width="' + Math.round(dimensions.width * factor) + '" height="' + Math.round(dimensions.height * factor) + '"') : 'Synthetic preview read error')
  } else if (/^\/original\/p\d{6}$/.test(path)) {
    if (originalDelay) await new Promise(resolve => setTimeout(resolve, originalDelay))
    if (new URL(request.url, 'http://127.0.0.1').searchParams.get('download') === '1') response.setHeader('Content-Disposition', 'attachment; filename="simulated-original.svg"')
    response.writeHead(originalStatus, { 'Content-Type': originalStatus === 200 ? 'image/svg+xml' : 'text/plain' }); response.end(originalStatus === 200 ? placeholder.replace('width="400" height="300"', 'width="4000" height="6000"') : 'Synthetic original read error')
  } else if (path.startsWith('/media/')) {
    response.writeHead(200, { 'Content-Type': 'image/svg+xml' }); response.end(placeholder)
  } else {
    response.writeHead(404); response.end('Synthetic test: unavailable route')
  }
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const origin = 'http://127.0.0.1:' + server.address().port
let browser
try {
  browser = await chromium.launch({ channel: process.env.BIRDSHOT_BROWSER_CHANNEL ?? 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = [], external = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (new URL(request.url()).origin !== origin) external.push(request.url()) })
  async function ready() {
    await page.waitForFunction(() => document.getElementById('gallery').getAttribute('aria-busy') === 'false')
    await page.getByRole('button', { name: '刷新', exact: true }).waitFor()
    await page.waitForFunction(() => !document.getElementById('refresh').disabled)
  }
  async function refresh() {
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await ready()
  }
  const ids = () => page.locator('.tile-open').evaluateAll(nodes => nodes.map(node => node.dataset.itemId))
  await page.goto(origin)
  await ready()
  assert.equal(await page.locator('.tile').count(), 48)
  assert.equal(await page.locator('#result-count,.results-line').count(), 0, '冗余结果摘要行应删除，不留空节点')
  assert.ok(await page.locator('#shoot-group').isDisabled())
  assert.match(await page.locator('#shoot-group-status').textContent(), /目录尚未准备/)
  assert.equal((await ids())[0], 'simulated-1', '技术排序必须用后端 rank，而不是输入或文件顺序')
  assert.equal((await ids()).at(-1), 'simulated-48')
  assert.equal(await page.locator('#counts').textContent(), '优选 60 · 已验鸟 61 · 非鸟 1 · 未知/未确认 1')
  assert.equal(requests.some(path => path.startsWith('/original/')), false, '首页不得预取原图')
  assert.equal(requests.some(path => path.startsWith('/gallery/group/')), false, '点击前不得预取组详情')
  assert.equal(await page.locator('#gallery img').count(), 48, '不得一次创建全部候选图片')
  assert.ok(await page.locator('#gallery img').evaluateAll(nodes => nodes.every(node => node.loading === 'lazy')))
  assert.match(await page.locator('#gallery img').first().getAttribute('src'), /%E6%A8%A1.*%20%231\.jpg$/)
  assert.equal(await page.locator('.tile').first().locator('.species-candidate').count(), 3, '兼容 Top5 时只显示前三候选')
  assert.ok(!(await page.locator('#gallery').textContent()).includes('不得展示第四候选'))
  assert.ok(!(await page.locator('#gallery').textContent()).includes('不得展示第五候选'))
  assert.match(await page.locator('.tile').first().textContent(), /概率 0\.3333%/, '不得按Top3重新归一化分类分数')
  assert.deepEqual(await page.locator('.tile').first().locator('.badges .badge').allTextContents(), ['鸟类候选', '高像素', '连拍代表'])
  assert.deepEqual(await page.locator('.tile').nth(1).locator('.badges .badge').allTextContents(), ['鸟类候选', '标准像素'])
  assert.ok(!(await page.locator('#gallery').textContent()).match(/分类模型|类别覆盖|技术选片依据|后端|模拟数据：|Simulation primus/), '卡片只保留简洁候选、标签及拍摄信息')
  assert.equal(await page.locator('a[href="/legacy/"],a[href="/mapping.json"],a[href="/manifest.json"]').count(), 0)
  assert.equal(await page.locator('.tile').first().locator('.english-name').first().textContent(), 'Simulated bird A')
  await page.getByRole('button', { name: '下一页', exact: true }).click()
  assert.equal(await page.locator('.tile').count(), 12)
  assert.equal((await ids())[0], 'simulated-49')
  assert.ok(await page.getByRole('button', { name: '下一页', exact: true }).isDisabled())
  await page.locator('#page-size').selectOption('24')
  assert.equal(await page.locator('.tile').count(), 24)
  assert.equal(await page.locator('#page-label').textContent(), '1 / 3 页')
  await page.locator('#search').fill('模拟来源-003')
  assert.deepEqual(await ids(), ['simulated-3'])
  await page.locator('#search').fill('no-such-source')
  assert.equal(await page.locator('#empty-title').textContent(), '没有符合筛选的裁剪')
  await page.locator('#search').fill('')
  await page.locator('#tier').selectOption('excellent')
  assert.equal((await ids())[0], 'simulated-2')
  assert.equal(await page.locator('#page-label').textContent(), '1 / 2 页')
  await page.locator('#tier').selectOption('')
  await page.locator('#sort').selectOption('reverse')
  assert.equal((await ids())[0], 'simulated-60')
  await page.locator('#sort').selectOption('rank')
  await page.locator('#collapse').check()
  assert.equal(await page.locator('#page-label').textContent(), '1 / 2 页')
  assert.deepEqual((await ids()).slice(0, 3), ['simulated-1', 'simulated-3', 'simulated-5'])
  await page.locator('#collapse').uncheck()
  await page.getByRole('tab', { name: /独立审核/ }).click()
  assert.deepEqual(await ids(), ['simulated-61', 'simulated-62', 'simulated-63'])
  assert.ok((await page.locator('#gallery').textContent()).includes(hostile))
  assert.equal(await page.locator('#gallery img[src="x"]').count(), 0, '模型文字与文件名必须是文本，不能成为 HTML')
  assert.equal(await page.evaluate(() => window.injected), undefined)
  await page.locator('#verification').selectOption('unknown')
  assert.deepEqual(await ids(), ['simulated-62'], '未知不得混入优选')
  await page.locator('#verification').selectOption('bird')
  assert.deepEqual(await ids(), ['simulated-63'], '已验鸟但未保留只在审核中')
  await page.getByRole('tab', { name: /优选鸟体/ }).click()
  assert.equal((await ids())[0], 'simulated-1')

  await page.locator('.tile-open').first().click()
  await page.locator('#focus-panel').waitFor()
  assert.ok(requests.includes('/gallery/group/g00001.json'))
  assert.ok(!(await page.locator('#detail').textContent()).includes('旧版帧级理由不应冒充'))
  assert.ok(!(await page.locator('#focus-panel .technical-details').evaluate(node => node.open)), '技术依据默认折叠')
  assert.ok(!(await page.locator('#focus-panel').innerText()).includes('模拟眼睛不可见'))
  assert.equal(await page.locator('.classification-details .species-candidate').count(), 3)
  await page.locator('.classification-details .reference-picture img').first().waitFor()
  assert.deepEqual([...new Set(referenceRequests)].sort(), ['Simulation primus', 'Simulation secundus', 'Simulation tertius'])
  assert.equal(referenceRequests.length, 3, '只在详情请求前三个唯一学名，不预抓整面图墙或Top5')
  assert.equal(await page.locator('.classification-details a[href^="https://zh.wikipedia.org/"]').count(), 3)
  assert.equal(await page.locator('.reference-credit').count(), 3)
  assert.equal(await page.locator('.classification-details img[src="x"]').count(), 0, '作者元数据只能当文本')
  assert.match(await page.locator('#dialog-title').textContent(), /模拟鸟种 A · 实例 0/)
  assert.equal(await page.locator('.details-copy>h3').count(), 0, '不重复显示鸟名与实例行')
  assert.equal(await page.locator('.image-controls .switch-control').count(), 2)
  assert.equal(await page.locator('#candidates>h3').textContent(), '鸟体对比')
  assert.ok(!(await page.locator('.classification-details').textContent()).match(/覆盖|来源|状态|原始类别|学名|正确率|Simulation primus/))
  assert.ok(!(await page.locator('#focus-panel').innerText()).match(/鸟类判别|分类模型|查看本地验鸟判据|birdMass|后端/))
  assert.deepEqual(await page.locator('.details-copy > .classification-details, .details-copy > .capture-details, .details-copy > .technical-details').evaluateAll(nodes => nodes.map(node => node.className)), ['classification-details', 'capture-details', 'technical-details'])
  assert.equal(await page.locator('#focus-panel [data-instance-mask]').count(), 0)
  assert.equal(await page.locator('#focus-panel [data-pose-point]').count(), 0, '新详情默认不绘制任何叠加')
  assert.ok(!(await page.getByRole('switch', { name: '分割轮廓', exact: true }).isChecked()))
  assert.ok(!(await page.getByRole('switch', { name: '姿态点', exact: true }).isChecked()))
  assert.ok(!(await page.locator('.classification-details').textContent()).includes('不得展示第四候选'))
  assert.equal(await page.locator('.candidate').count(), 5, '组内其他鸟体、非鸟、未知及无分类实例应同时可浏览')
  assert.match(await page.locator('#candidates').textContent(), /未识别/)
  assert.match(await page.locator('#candidates').textContent(), /未检出鸟体，无鸟体裁剪/)
  await page.getByRole('switch', { name: '分割轮廓', exact: true }).focus()
  await page.keyboard.press('Space')
  assert.equal(await page.locator('#focus-panel [data-instance-mask]').count(), 1, '键盘可打开分割开关')
  assert.equal(await page.locator('#focus-panel [data-pose-point]').count(), 0)
  await page.getByRole('switch', { name: '姿态点', exact: true }).check()
  assert.equal(await page.locator('#focus-panel [data-pose-point]').count(), 1, '只画高置信候选点')
  assert.equal(await page.locator('#focus-panel [data-instance-mask]').count(), 1)
  await page.getByRole('switch', { name: '分割轮廓', exact: true }).uncheck()
  assert.equal(await page.locator('#focus-panel [data-instance-mask]').count(), 0)
  assert.equal(await page.locator('#focus-panel [data-pose-point]').count(), 1, '两个开关彼此独立，不互斥')
  await page.getByRole('switch', { name: '姿态点', exact: true }).uncheck()
  assert.equal(await page.locator('#focus-panel [data-pose-point]').count(), 0)
  await page.locator('.technical-details summary').click()
  assert.match(await page.locator('.technical-details').innerText(), /模拟眼睛不可见/)
  assert.match(await page.locator('.technical-details').innerText(), /未发现触边风险/)
  assert.ok(!(await page.locator('.technical-details').innerText()).match(/false|后端/))
  await page.getByRole('button', { name: '查看组内实例 2 · 模拟来源-002.jpg', exact: true }).click()
  assert.match(await page.locator('#focus-panel').textContent(), /待确认/)
  assert.match(await page.locator('#focus-panel').textContent(), /未入选/)
  assert.ok(await page.getByRole('switch', { name: '姿态点', exact: true }).isDisabled())
  assert.ok(!(await page.getByRole('switch', { name: '分割轮廓', exact: true }).isChecked()))
  assert.equal(await page.locator('#focus-panel [data-instance-mask]').count(), 0, '切换实例不能残留上一实例叠加')
  assert.equal(requests.some(path => path.startsWith('/original/')), false, '切换裁剪不能暗中加载原图')
  assert.equal(await page.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
  assert.equal(await page.locator('a[href^="/original/"]').count(), 0, '原图仅在图片控件原位切换，不再导航新页面')
  assert.equal(await page.locator('a[href$=".json"],a[href="/legacy/"]').count(), 0, '所有调试与旧报告入口从可见UI移除，不删除API')
  await page.keyboard.press('Escape')
  assert.ok(!(await page.locator('#dialog').evaluate(node => node.open)))

  detailStatus = 503
  await page.locator('.tile-open').first().click()
  await page.locator('#detail [role=alert]').waitFor()
  assert.match(await page.locator('#detail').textContent(), /HTTP 503/)
  assert.equal(requests.some(path => /^\/group\//.test(path)), false, '增强详情失败不得回退旧版')
  detailStatus = 200
  await page.getByRole('button', { name: '重试组详情' }).click()
  await page.locator('#focus-panel').waitFor()
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  missingInstance = true
  await page.locator('.tile-open').first().click()
  await page.locator('#detail [role=alert]').waitFor()
  assert.match(await page.locator('#detail').textContent(), /精确帧 \/ 实例/)
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  missingInstance = false
  missingRect = true
  await page.locator('.tile-open').first().click()
  await page.locator('#focus-panel').waitFor()
  assert.ok(await page.getByRole('switch', { name: '分割轮廓', exact: true }).isDisabled())
  assert.ok(await page.getByRole('switch', { name: '姿态点', exact: true }).isDisabled())
  assert.match(await page.locator('#focus-panel').textContent(), /缺少有效裁剪坐标/)
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  missingRect = false
  detailDelay = 150
  await page.locator('.tile-open').first().click()
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.waitForTimeout(200)
  assert.equal(await page.locator('#detail').textContent(), '', '关闭详情应取消在途请求')
  detailDelay = 0

  payload = baseline()
  const top3Item = payload.items.find(item => item.rank === 1)
  top3Item.birdVerification.model = 'Birder（模拟接口测试，并非真实分类）'
  top3Item.species.top3 = [
    { scientificName: 'Simulation untranslated', originalName: 'Original label, not a translation', probability: .000034, nameStatus: '模拟缺名', isBird: true },
    { commonName: '模拟规范中文候选', englishName: 'Simulated standard English bird', scientificName: 'Simulation sinensis', probability: .000021, nameSource: '模拟权威表来源（非真实鉴定）', isBird: true },
    {},
  ]
  payload.items.find(item => item.rank === 61).species.top3 = [
    { isBird: false, originalName: 'simulated_background_class', probability: .45 },
    { commonName: '模拟鸟类候选不构成验鸟通过', scientificName: 'Simulation candidate only', isBird: true, probability: .2 },
  ]
  payload.items.find(item => item.rank === 2).species.top3 = []
  await refresh()
  const top3Tile = page.locator('.tile').first()
  assert.equal(await top3Tile.locator('.species-candidate').count(), 2, '只显示有效实际候选，不补齐三项')
  assert.match(await top3Tile.textContent(), /中文名待补/)
  assert.match(await top3Tile.textContent(), /英文名待补/)
  assert.ok(!(await top3Tile.textContent()).includes('Simulation untranslated'), '拉丁学名不能充当常用英文名')
  assert.match(await top3Tile.textContent(), /Simulated standard English bird/)
  assert.match(await top3Tile.textContent(), /模拟规范中文候选/)
  assert.match(await top3Tile.textContent(), /概率 0\.0034%/)
  assert.ok(!(await top3Tile.textContent()).includes('模拟鸟种 A'), '明确Top3优先于兼容Top5')
  assert.equal(await page.locator('.tile').nth(1).locator('.species-candidate').count(), 0, '明确空Top3不能用旧Top5凑数')
  await page.locator('#search').fill('模拟规范中文候选')
  assert.deepEqual(await ids(), ['simulated-1'], '搜索必须支持新版中文Top3')
  await page.locator('#search').fill('')
  await page.locator('.tile-open').first().click()
  await page.locator('#focus-panel').waitFor()
  assert.equal(await page.locator('.classification-details .species-candidate').count(), 2)
  assert.match(await page.locator('.classification-details').textContent(), /中文名待补/)
  assert.ok(!(await page.locator('.classification-details').textContent()).match(/Original label|模拟缺名|模拟权威表来源|Simulation sinensis/))
  assert.match(await page.locator('.classification-details').textContent(), /英文名待补/)
  assert.match(await page.locator('.classification-details').textContent(), /Simulated standard English bird/)
  assert.ok(!(await page.locator('#focus-panel').textContent()).match(/Birder（模拟接口测试|鸟类判别/))
  assert.match(await page.locator('#focus-panel').textContent(), /技术选片依据/)
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('tab', { name: /独立审核/ }).click()
  await page.locator('#verification').selectOption('nonBird')
  assert.deepEqual(await ids(), ['simulated-61'])
  assert.equal(await page.locator('.species-candidate strong').first().textContent(), '1. 非鸟类别')
  assert.ok(!(await page.locator('#gallery').textContent()).includes('simulated_background_class'), '不展示原始分类ID')
  assert.equal(await page.locator('.tile').first().locator('.species-candidate').count(), 2)
  await page.getByRole('tab', { name: /优选鸟体/ }).click()
  assert.ok(!(await ids()).includes('simulated-61'), '有鸟种候选也不能让非鸟项进入优选池')

  payload = baseline()
  const pending = makeItem(64, { rank: null, decision: 'review', birdVerification: { status: 'unknown', reason: '模拟尚未完成本机验鸟', evidence: null }, species: { top3: [], coverage: '尚未分类' } })
  const processedUnknown = payload.items.find(item => item.id === 'simulated-62')
  delete processedUnknown.rank
  processedUnknown.birdVerification.evidence = { status: 'completed', simulation: true }
  const unrankedNonBird = payload.items.find(item => item.id === 'simulated-61')
  unrankedNonBird.rank = null
  const rankedReview = payload.items.find(item => item.id === 'simulated-63')
  rankedReview.rank = 7
  payload.items = [pending, processedUnknown, rankedReview, unrankedNonBird, ...payload.items.filter(item => item.rank !== null && item.id !== processedUnknown.id && item.id !== rankedReview.id)]
  payload.progress = { processed: 63, total: 64 }; payload.summary.pending = 1; payload.status = 'processing'
  await refresh()
  assert.match(await page.locator('#progress-label').textContent(), /待处理 1/)
  assert.match(await page.locator('#counts').textContent(), /已处理未知 1/)
  await page.getByRole('tab', { name: /独立审核/ }).click()
  await page.locator('#verification').selectOption('')
  assert.deepEqual(await ids(), ['simulated-63', 'simulated-64', 'simulated-62', 'simulated-61'], '审核中有效名次优先；null/缺失名次稳定放后')
  assert.deepEqual(await page.locator('.tile .rank').allTextContents(), ['#7', '未排名', '未排名', '未排名'])
  assert.match(await page.locator('[data-item-id=simulated-64]').textContent(), /待识别/)
  assert.match(await page.locator('[data-item-id=simulated-62]').textContent(), /待确认/)
  await page.locator('#sort').selectOption('reverse')
  assert.deepEqual(await ids(), ['simulated-63', 'simulated-64', 'simulated-62', 'simulated-61'], '反向浏览仍将未排名放后，不把null当0')
  await page.locator('#verification').selectOption('pending')
  assert.deepEqual(await ids(), ['simulated-64'])
  await page.locator('#verification').selectOption('unknown')
  assert.deepEqual(await ids(), ['simulated-62'], '已处理未知不得混入未完成候选')
  await page.getByRole('tab', { name: /优选鸟体/ }).click()
  await page.locator('#sort').selectOption('rank')
  payload = baseline(); payload.items.find(item => item.rank === 1).rank = null
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /优选项缺少有效的技术名次/, '优选不能伪造名次或以未排名冒充成功')

  payload = baseline(); payload.items = []; payload.summary.preferred = 0; payload.status = 'processing'; payload.progress = { processed: 0, total: 5504 }
  await refresh()
  assert.equal(await page.locator('#empty-title').textContent(), '等待本地验鸟结果')
  assert.match(await page.locator('#empty-message').textContent(), /旧版保留不会自动当作已验鸟/)
  payload.status = 'completed'; payload.progress = { processed: 5504, total: 5504 }
  await refresh()
  assert.equal(await page.locator('#empty-title').textContent(), '尚无通过验鸟并保留的鸟体')
  payload = baseline(); payload.status = 'error'
  await refresh()
  assert.equal(await page.locator('.tile').count(), 24, '错误索引可展示明确标注的已写入部分结果')
  assert.match(await page.locator('#notice[role=alert]').textContent(), /并非全量完成/)
  indexStatus = 404
  await refresh()
  assert.equal(await page.locator('.tile').count(), 0, '失败后不得继续展示旧索引为新结果')
  assert.match(await page.locator('#notice').textContent(), /HTTP 404/)
  assert.equal(await page.locator('#empty-title').textContent(), '优选索引不可用')
  indexStatus = 200; payload = baseline(); payload.version = 2
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /索引版本/)
  payload = baseline(); payload.items[0].crop = '../private.jpg'
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /输出目录/)
  payload = baseline(); payload.items[0].thumbnail = 'https://example.invalid/private.jpg'
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /本机相对路径/)
  payload = baseline(); payload.items[0].thumbnail = 'crops/control\n.jpg'
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /本机相对路径/)
  payload = baseline(); payload.summary.preferred = 343
  await refresh()
  assert.match(await page.locator('#notice').textContent(), /计数与已验鸟保留索引不一致/)
  payload = baseline()
  payload.items.find(item => item.rank === 1).thumbnail = 'crops/missing-thumb.jpg'
  await refresh()
  await page.locator('.tile').first().locator('.image-error').waitFor()
  assert.match(await page.locator('.tile').first().textContent(), /裁剪图读取失败/)
  payload = baseline()
  await refresh()

  indexDelay = 150
  await page.reload({ waitUntil: 'domcontentloaded' })
  assert.equal(await page.locator('#status').textContent(), '读取索引中')
  await ready()
  indexDelay = 0
  await page.setViewportSize({ width: 390, height: 844 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机图墙不能横向溢出')
  await page.locator('.tile-open').first().click()
  await page.locator('#focus-panel').waitFor()
  assert.ok(await page.locator('#dialog').evaluate(node => node.scrollWidth <= node.clientWidth + 1), '手机详情不能横向溢出')
  await page.getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('tab', { name: /优选鸟体/ }).focus()
  await page.keyboard.press('ArrowRight')
  assert.equal(await page.getByRole('tab', { name: /独立审核/ }).getAttribute('aria-selected'), 'true')

  payload = baseline()
  payload.items.find(item => item.rank === 1).species.top3 = [
    { commonName: '模拟规范中文长名称用于手机换行检查', scientificName: 'Simulation_' + 'longscientificname'.repeat(8), probability: .000034, isBird: true },
    { commonName: '模拟第二中文候选', scientificName: 'Simulation secundus subspecies syntheticus', probability: .000021, isBird: true },
    { commonName: '中文名待补', scientificName: 'Simulation tertius untranslated', probability: .00001, isBird: true },
  ]
  payload.items.find(item => item.rank === 1).capture = {
    status: 'ready', time: '2001-02-03T04:05:06.123456+08:00', timeZone: '+08:00', timeSource: '模拟EXIF时间来源'.repeat(12),
    location: { latitude: -33.5, longitude: -70.25, crs: 'WGS84', source: 'exif-gps' }, locationNote: '模拟坐标说明用于手机长文本换行'.repeat(15),
  }
  const touchContext = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 360, height: 800 } })
  const touch = await touchContext.newPage()
  const touchErrors = []
  touch.on('pageerror', error => touchErrors.push(error.message))
  await touch.goto(origin)
  await touch.locator('.tile').first().waitFor()
  for (const viewport of [
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 800, height: 360 },
    { width: 844, height: 390 },
    { width: 768, height: 1024 },
    { width: 1440, height: 1000 },
  ]) {
    await touch.setViewportSize(viewport)
    const label = viewport.width + '×' + viewport.height
    assert.ok(await touch.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), label + ' 图墙不能横向溢出')
    assert.equal(await touch.locator('.tile').count(), 48, label + ' 必须保留有界分页而非加载全部图片')
    for (const selector of ['#refresh', '#search', '#tier', '#sort', '#preferred-tab']) {
      const box = await touch.locator(selector).boundingBox()
      assert.ok(box.height >= 44, label + ' 触控区域至少44px：' + selector)
    }
    await touch.locator('.tile-open').first().tap()
    await touch.locator('#focus-panel').waitFor()
    const dialogBox = await touch.locator('#dialog').boundingBox()
    assert.ok(dialogBox.x >= 0 && dialogBox.y >= 0 && dialogBox.x + dialogBox.width <= viewport.width + 1 && dialogBox.y + dialogBox.height <= viewport.height + 1, label + ' 详情弹层必须在视口内')
    assert.ok(await touch.locator('#dialog').evaluate(node => node.scrollWidth <= node.clientWidth + 1), label + ' 详情与长学名不能横向溢出')
    const headingBox = await touch.locator('.dialog-heading').boundingBox(), iconBox = await touch.locator('#close').boundingBox()
    assert.ok(iconBox.x >= headingBox.x + headingBox.width && Math.abs(iconBox.y + iconBox.height / 2 - headingBox.y - headingBox.height / 2) <= 2, label + ' 页头关闭icon与标题同一行，不能另起一行')
    assert.ok(iconBox.width >= 44 && iconBox.height >= 44)
    assert.equal(await touch.locator('#close').evaluate(node => getComputedStyle(node).borderTopWidth), '0px')
    const cards = await touch.locator('.classification-details .species-candidate').evaluateAll(nodes => nodes.map(node => ({ x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })))
    assert.ok(cards.every(card => Math.abs(card.y - cards[0].y) < 1) && cards[1].x > cards[0].x && cards[2].x > cards[1].x, label + ' Top3保持三列卡片')
    await touch.locator('.preview-area[aria-busy="false"]').waitFor()
    const originalBox = await touch.locator('.original-toggle').boundingBox(), areaBox = await touch.locator('.preview-area').boundingBox()
    assert.ok(originalBox.height >= 44 && originalBox.x + originalBox.width <= areaBox.x + areaBox.width && originalBox.y + originalBox.height <= areaBox.y + areaBox.height, '原图icon toggle必须在图片右下角且适合触控')
    assert.ok(!(await touch.locator('#focus-panel .technical-details').evaluate(node => node.open)), '技术依据默认折叠')
    assert.equal(await touch.locator('#focus-panel [data-instance-mask],#focus-panel [data-pose-point]').count(), 0)
    for (const name of ['分割轮廓', '姿态点']) {
      assert.ok(!(await touch.getByRole('switch', { name, exact: true }).isChecked()))
      assert.ok((await touch.getByRole('switch', { name, exact: true }).locator('..').boundingBox()).height >= 44)
      const box = await touch.getByRole('switch', { name, exact: true }).locator('..').boundingBox()
      assert.ok(box.y >= areaBox.y && box.y + box.height <= areaBox.y + areaBox.height + 1, '两个叠加控件均在图片内部底部')
    }
    await touch.locator('#focus-panel .technical-details summary').tap()
    assert.match(await touch.locator('#focus-panel .technical-details').innerText(), /主体内部梯度/)
    await touch.locator('#dialog').evaluate(node => { node.scrollTop = node.scrollHeight })
    const closeBox = await touch.locator('#close').boundingBox()
    assert.ok(closeBox.height >= 44 && closeBox.y >= 0 && closeBox.y + closeBox.height <= viewport.height, label + ' 滚动后关闭按钮仍可触达')
    await touch.locator('#close').tap()
    assert.ok(!(await touch.locator('#dialog').evaluate(node => node.open)), label + ' 触控关闭详情应生效')
  }
  await touch.locator('#next').tap()
  assert.equal(await touch.locator('.tile').count(), 12)
  assert.equal(await touch.locator('.tile-open').first().getAttribute('data-item-id'), 'simulated-49')
  assert.ok(await touch.locator('#gallery img').evaluateAll(nodes => nodes.every(node => node.loading === 'lazy')))
  await touch.setViewportSize({ width: 360, height: 800 })
  payload = baseline(); payload.items = []; payload.summary.preferred = 0; payload.status = 'processing'; payload.progress = { processed: 0, total: 5504 }
  await touch.locator('#refresh').tap()
  await touch.waitForFunction(() => document.getElementById('empty-title').textContent === '等待本地验鸟结果')
  assert.ok(await touch.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机处理进度和等待状态可读')
  payload.status = 'completed'
  await touch.locator('#refresh').tap()
  await touch.waitForFunction(() => document.getElementById('empty-title').textContent === '尚无通过验鸟并保留的鸟体')
  indexStatus = 503
  await touch.locator('#refresh').tap()
  await touch.locator('#notice[role=alert]').waitFor()
  assert.match(await touch.locator('#notice').textContent(), /HTTP 503/)
  assert.ok(await touch.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机错误和空结果不能横向溢出')
  assert.deepEqual(touchErrors, [])
  await touchContext.close()
  indexStatus = 200
  payload = baseline()

  // All requests to this documentation-only address are fulfilled locally; no LAN connection is made.
  const lanOrigin = 'http://192.0.2.10:5174'
  const lan = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const lanRequests = [], unexpectedLanRequests = []
  lan.on('request', request => lanRequests.push(request.url()))
  await lan.route('**/*', async route => {
    const target = new URL(route.request().url())
    if (target.origin !== lanOrigin) { unexpectedLanRequests.push(target.href); await route.abort(); return }
    const response = await lan.request.get(origin + target.pathname + target.search)
    await route.fulfill({ response })
  })
  await lan.goto(lanOrigin)
  await lan.locator('.tile').first().waitFor()
  assert.equal(await lan.locator('#status').textContent(), '索引已完成', '前端不能把内网主机拒绝为非回环来源')
  assert.ok(await lan.locator('#gallery img').evaluateAll(nodes => nodes.every(node => node.getAttribute('src').startsWith('/media/'))))
  await lan.locator('.tile-open').first().tap()
  await lan.locator('#focus-panel').waitFor()
  assert.equal(await lan.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
  assert.equal(await lan.locator('a[href$=".json"],a[href="/legacy/"]').count(), 0)
  await lan.locator('#close').tap()
  assert.ok(lanRequests.some(url => url === lanOrigin + '/gallery.json'))
  assert.ok(lanRequests.some(url => url.startsWith(lanOrigin + '/media/')))
  assert.ok(lanRequests.some(url => url === lanOrigin + '/gallery/group/g00001.json'))
  assert.ok(lanRequests.every(url => new URL(url).origin === lanOrigin), '内网访问时JSON、详情和图床必须同源')
  assert.deepEqual(unexpectedLanRequests, [])
  await lan.close()

  payload = baseline()
  const simulatedCapture = {
    status: 'ready', time: '2001-02-03T04:05:06.123456', timeZone: null, timeSource: '模拟 EXIF DateTimeOriginal',
    location: { latitude: -33.5, longitude: -70.25, crs: 'WGS84', source: 'exif-gps' }, locationNote: '模拟测试坐标，非用户照片地点',
  }
  const setCapture = (rank, capture) => { payload.items.find(item => item.rank === rank).capture = capture }
  setCapture(1, simulatedCapture)
  setCapture(2, { ...simulatedCapture, time: '2002-03-04T05:06:07+08:00', timeZone: '+08:00', location: null })
  setCapture(3, { ...simulatedCapture, time: null, location: null })
  setCapture(4, { status: 'pending', time: 'NOT_READY_DO_NOT_SHOW', location: simulatedCapture.location })
  setCapture(5, { status: 'error', time: 'FAILED_DO_NOT_SHOW', location: simulatedCapture.location, locationNote: hostile })
  setCapture(7, { ...simulatedCapture, location: { ...simulatedCapture.location, latitude: 91 } })
  setCapture(8, { ...simulatedCapture, location: { ...simulatedCapture.location, crs: 'GCJ02' } })
  setCapture(9, { ...simulatedCapture, location: { latitude: 0, longitude: 0, crs: 'WGS84', source: 'exif-gps' } })
  setCapture(10, { ...simulatedCapture, location: { ...simulatedCapture.location, longitude: '-70.25' } })
  setCapture(11, { ...simulatedCapture, time: '2001-02-03T20:30:45Z', timeZone: 'UTC', location: null })
  setCapture(12, { ...simulatedCapture, time: '2001-02-03T23:45:06-03:00', timeZone: '-03:00', location: null })
  setCapture(13, { ...simulatedCapture, time: '2001-02-03T09:10:11', timeZone: '+08:00', location: null })
  setCapture(14, { ...simulatedCapture, time: '2001-02-30T09:10:11+08:00', timeZone: '+08:00', location: null })
  const captureContext = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Pacific/Honolulu', hasTouch: true, isMobile: true })
  const captureRequests = [], mapRequests = [], captureErrors = []
  captureContext.on('request', request => captureRequests.push(request.url()))
  await captureContext.route('https://www.openstreetmap.org/**', async route => {
    mapRequests.push({ url: route.request().url(), headers: route.request().headers() })
    await route.fulfill({ contentType: 'text/html', body: '<title>SIMULATED MAP - no network connection</title>' })
  })
  const capturePage = await captureContext.newPage()
  capturePage.on('pageerror', error => captureErrors.push(error.message))
  await capturePage.goto(origin)
  await capturePage.locator('.tile').first().waitFor()
  const captureTile = rank => capturePage.locator('.tile-open[data-item-id="simulated-' + rank + '"]')
  assert.match(await captureTile(1).locator('.capture-summary').textContent(), /2001年2月3日 04:05（相机时间，时区未记录）/)
  assert.match(await captureTile(1).locator('.capture-summary').textContent(), /南纬 33\.5° · 西经 70\.25°/)
  assert.match(await captureTile(2).locator('.capture-summary').textContent(), /2002年3月4日 05:06（北京时间）/)
  assert.match(await captureTile(11).locator('.capture-summary').textContent(), /2001年2月4日 04:30（北京时间）/, 'UTC时间应固定转换为北京时间，含跨日')
  assert.match(await captureTile(12).locator('.capture-summary').textContent(), /2001年2月4日 10:45（北京时间）/, '其他已知offset同样转北京时间，不受浏览器Honolulu时区影响')
  assert.match(await captureTile(13).locator('.capture-summary').textContent(), /2001年2月3日 09:10（北京时间）/, '独立时区字段明确时可转换')
  assert.match(await captureTile(14).locator('.capture-summary').textContent(), /拍摄时间格式无效/, '无效日期不能经Date自动溢出为另一天')
  assert.ok(!(await capturePage.locator('#gallery').textContent()).match(/T\d\d:\d\d|\.123456|\+08:00/), '页面不显示ISO串、毫秒或原始offset')
  assert.match(await captureTile(3).locator('.capture-summary').textContent(), /未记录拍摄时间.*未记录位置/)
  for (const rank of [4, 6]) assert.match(await captureTile(rank).locator('.capture-summary').textContent(), /元数据尚未读取/)
  assert.match(await captureTile(5).locator('.capture-summary').textContent(), /读取失败/)
  assert.ok(!(await capturePage.locator('#gallery').textContent()).includes('DO_NOT_SHOW'))
  for (const rank of [7, 8, 10]) assert.match(await captureTile(rank).locator('.capture-summary').textContent(), /位置数据无效或非 EXIF WGS84/)
  assert.ok(captureRequests.every(url => new URL(url).origin === origin), '显示EXIF坐标不能自动请求GIS、瓦片或地理编码')
  assert.equal(await capturePage.locator('#gallery a[href*="openstreetmap"]').count(), 0, '卡片按钮内不嵌套外部地图交互')
  await captureTile(1).tap()
  await capturePage.locator('#focus-panel').waitFor()
  assert.match(await capturePage.locator('.capture-details').textContent(), /2001年2月3日 04:05:06/)
  assert.match(await capturePage.locator('.capture-details').textContent(), /相机时间，时区未记录/)
  assert.ok(!(await capturePage.locator('.capture-details').textContent()).includes('模拟 EXIF DateTimeOriginal'), '精简详情不显示原始来源诊断')
  assert.match(await capturePage.locator('.capture-details').textContent(), /EXIF GPS · WGS84/)
  const mapLink = capturePage.getByRole('link', { name: '在地图查看（将打开外部地图）' })
  assert.equal(await mapLink.getAttribute('href'), 'https://www.openstreetmap.org/?mlat=-33.5&mlon=-70.25#map=16/-33.5/-70.25')
  assert.equal(await mapLink.getAttribute('rel'), 'noopener noreferrer')
  assert.equal(await mapLink.getAttribute('referrerpolicy'), 'no-referrer')
  assert.equal(mapRequests.length, 0)
  const popupPromise = capturePage.waitForEvent('popup')
  await mapLink.tap()
  const popup = await popupPromise
  await popup.waitForLoadState()
  assert.equal(mapRequests.length, 1, '只能在明确点击后打开已拦截的模拟外部地图')
  assert.equal(mapRequests[0].headers.referer, undefined, '不得向地图发送报告地址或照片来源')
  const requestedMap = new URL(mapRequests[0].url)
  assert.deepEqual([...requestedMap.searchParams], [['mlat', '-33.5'], ['mlon', '-70.25']], '地图URL只包含数值经纬，不含照片路径/名称/密钥')
  assert.equal(await popup.evaluate(() => window.opener), null)
  await popup.close()
  await capturePage.locator('#close').tap()
  frameCaptures.set('p000001', { ...simulatedCapture, time: '2003-04-05T06:07:08-03:00', timeZone: '-03:00', timeSource: '模拟组帧EXIF优先', location: { latitude: 12.5, longitude: 45.25, crs: 'WGS84', source: 'exif-gps' } })
  await captureTile(1).tap()
  await capturePage.locator('#focus-panel').waitFor()
  assert.match(await capturePage.locator('.capture-details').textContent(), /2003年4月5日 17:07:08/)
  assert.ok(!(await capturePage.locator('.capture-details').textContent()).includes(simulatedCapture.time), '同一精确帧的组capture优先于索引capture')
  await capturePage.getByRole('button', { name: '查看组内实例 2 · 模拟来源-002.jpg', exact: true }).tap()
  assert.match(await capturePage.locator('.capture-details').textContent(), /2002年3月4日 05:06:07/)
  assert.match(await capturePage.locator('.capture-details').textContent(), /未记录位置/)
  assert.equal(await capturePage.getByRole('link', { name: '在地图查看（将打开外部地图）' }).count(), 0, '切换到另一个帧时不能沿用之前照片的GPS')
  await capturePage.locator('#close').tap()
  frameCaptures.clear()
  for (const rank of [3, 4, 5, 6, 7, 8, 9, 10]) {
    await captureTile(rank).tap()
    await capturePage.locator('#focus-panel').waitFor()
    assert.equal(await capturePage.getByRole('link', { name: '在地图查看（将打开外部地图）' }).count(), rank === 9 ? 1 : 0)
    if (rank === 9) assert.match(await capturePage.locator('.capture-details').textContent(), /北纬 0° · 东经 0°/)
    if (rank === 5) {
      assert.match(await capturePage.locator('.capture-details').textContent(), /读取失败/)
      assert.ok((await capturePage.locator('.capture-details').textContent()).includes(hostile))
      assert.equal(await capturePage.locator('.capture-details img').count(), 0)
    }
    assert.ok(await capturePage.locator('#dialog').evaluate(node => node.scrollWidth <= node.clientWidth + 1), '元数据和地图入口手机布局不能横向溢出')
    await capturePage.locator('#close').tap()
  }
  assert.equal(mapRequests.length, 1, '后续浏览其他GPS或状态不能自动打开地图')
  assert.deepEqual(captureErrors, [])
  assert.ok(captureRequests.every(url => new URL(url).origin === origin || url.startsWith('https://www.openstreetmap.org/?')), '不能调用其他定位或第三方服务')
  await captureContext.close()

  payload = shootFixture()
  const shootPage = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  const shootErrors = [], shootRequests = []
  shootPage.on('pageerror', error => shootErrors.push(error.message))
  shootPage.on('request', request => shootRequests.push(request.url()))
  await shootPage.goto(origin)
  await shootPage.locator('.tile').first().waitFor()
  const shootIds = () => shootPage.locator('.tile-open').evaluateAll(nodes => nodes.map(node => node.dataset.itemId))
  const selectShoot = id => shootPage.locator('#shoot-group').selectOption(id)
  assert.ok(!(await shootPage.locator('#shoot-group').isDisabled()))
  assert.equal(await shootPage.locator('#shoot-group option').count(), 6)
  assert.match(await shootPage.locator('#shoot-group-status').textContent(), /全部拍摄组 · 5 组/)
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 60')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 3')
  assert.equal(await shootPage.locator('#page-label').textContent(), '1 / 2 页')
  await shootPage.locator('#next').tap()
  assert.equal(await shootPage.locator('#page-label').textContent(), '2 / 2 页')
  await selectShoot('sim-place-a-day-2')
  assert.equal(await shootPage.locator('#page-label').textContent(), '1 / 1 页', '切换拍摄组必须重置分页，不保留超界页码')
  assert.deepEqual(await shootIds(), ['simulated-51', 'simulated-52', 'simulated-53', 'simulated-54'], '同地不同日期分开筛选且仍按后端rank')
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 4')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 0')
  assert.match(await shootPage.locator('#shoot-group-status').textContent(), /模拟观鸟地甲 - 010204 · 原图 4 · 裁剪 4 · 优选 4/)
  await selectShoot('sim-place-b-day-1')
  assert.deepEqual(await shootIds(), ['simulated-55', 'simulated-56', 'simulated-57'], '同日不同地点不能混组')
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 3')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 1')
  await shootPage.locator('#search').fill('模拟来源-056')
  assert.deepEqual(await shootIds(), ['simulated-56'])
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 1')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 0')
  await shootPage.locator('#tier').selectOption('good')
  assert.equal(await shootPage.locator('.tile').count(), 0)
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 0')
  assert.equal(await shootPage.locator('#empty-title').textContent(), '没有符合筛选的裁剪')
  await shootPage.locator('#search').fill('')
  assert.deepEqual(await shootIds(), ['simulated-55', 'simulated-57'])
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 2')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 0')
  await shootPage.locator('#tier').selectOption('')
  await shootPage.locator('#sort').selectOption('reverse')
  assert.deepEqual(await shootIds(), ['simulated-57', 'simulated-56', 'simulated-55'])
  await shootPage.locator('#sort').selectOption('rank')
  await shootPage.locator('#collapse').check()
  assert.deepEqual(await shootIds(), ['simulated-55', 'simulated-57'], '高层分组可与原连拍折叠组合，不重建技术判断')
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 2')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 1')
  await shootPage.locator('#collapse').uncheck()
  await shootPage.getByRole('tab', { name: /独立审核/ }).tap()
  assert.deepEqual(await shootIds(), ['simulated-62'], '高层拍摄组同样约束审核池')
  await shootPage.locator('#verification').selectOption('nonBird')
  assert.equal(await shootPage.locator('.tile').count(), 0)
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 3', '审核类型只约束审核池')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 0')
  await shootPage.locator('#verification').selectOption('')
  await shootPage.getByRole('tab', { name: /优选鸟体/ }).tap()
  await selectShoot('sim-unknown-day-1')
  assert.deepEqual(await shootIds(), ['simulated-58', 'simulated-59'])
  assert.match(await shootPage.locator('.capture-summary').first().textContent(), /未知地点 - 010203.*地点：未知地点.*未记录位置/)
  await selectShoot('sim-place-a-no-date')
  assert.deepEqual(await shootIds(), ['simulated-60'])
  assert.match(await shootPage.locator('.capture-summary').first().textContent(), /日期未知.*未记录拍摄时间/)
  const detailsBeforeGrouping = shootRequests.filter(url => new URL(url).pathname.startsWith('/gallery/group/')).length
  await selectShoot('sim-place-a-day-1')
  assert.match(await shootPage.locator('#shoot-group-status').textContent(), /原图 50 · 裁剪 51 · 优选 50/, '原图/裁剪/优选为不同目录计数，不按卡片数误标')
  assert.equal(shootRequests.filter(url => new URL(url).pathname.startsWith('/gallery/group/')).length, detailsBeforeGrouping, '筛选本身不能用shootGroup去请求连拍详情')
  await shootPage.locator('.tile-open').first().tap()
  await shootPage.locator('#focus-panel').waitFor()
  assert.equal(new URL(shootRequests.filter(url => new URL(url).pathname.startsWith('/gallery/group/')).at(-1)).pathname, '/gallery/group/g00001.json', '详情仍按原groupId，不使用shootGroup.id')
  assert.match(await shootPage.locator('.capture-details').textContent(), /拍摄组：模拟观鸟地甲 - 010203/)
  assert.match(await shootPage.locator('.capture-details').textContent(), /模拟观鸟地甲/)
  assert.ok(!(await shootPage.locator('.capture-details').textContent()).match(/模拟用户已确认地点（非GPS）|仅模拟分组，不是用户照片地点/), '来源说明在数据保留，不挤占精简详情')
  assert.equal(await shootPage.getByRole('link', { name: '在地图查看（将打开外部地图）' }).count(), 0, '有大致地名但没有GPS仍不能伪造标点')
  await shootPage.locator('#close').tap()
  frameShootGroups.set('p000001', { id: 'sim-latest-frame-group', name: '模拟帧分组优先 - 日期未知', date: null, placeName: '模拟帧分组优先' })
  await shootPage.locator('.tile-open').first().tap()
  await shootPage.locator('#focus-panel').waitFor()
  assert.match(await shootPage.locator('.capture-details').textContent(), /模拟帧分组优先 - 日期未知/)
  assert.match(await shootPage.locator('.capture-details').textContent(), /日期未知/)
  await shootPage.locator('#close').tap()
  frameShootGroups.clear()
  await selectShoot('')
  assert.equal(await shootPage.locator('.tile').count(), 48, '全部拍摄组恢复原优选池，不改变保留集合')
  assert.equal(await shootPage.locator('#preferred-tab').textContent(), '优选鸟体 60', '页签显示分页前总量，不能把当前页48当总量')
  assert.equal(await shootPage.locator('#review-tab').textContent(), '独立审核 3')
  for (const viewport of [{ width: 360, height: 800 }, { width: 390, height: 844 }, { width: 800, height: 360 }, { width: 768, height: 1024 }, { width: 1440, height: 1000 }]) {
    await shootPage.setViewportSize(viewport)
    await selectShoot('sim-place-a-day-2')
    await shootPage.locator('#search').fill('模拟鸟种 B')
    assert.deepEqual(await shootIds(), ['simulated-52', 'simulated-54'])
    assert.ok((await shootPage.locator('#shoot-group').boundingBox()).height >= 44)
    assert.ok(await shootPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机/横屏/平板的分组筛选和计数不得横向溢出')
    await shootPage.locator('#search').fill('')
  }
  const reloadShootIndex = async () => {
    await shootPage.locator('#refresh').tap()
    await shootPage.waitForFunction(() => !document.getElementById('refresh').disabled)
  }
  await reloadShootIndex()
  assert.equal(await shootPage.locator('#shoot-group').inputValue(), 'sim-place-a-day-2', '索引刷新保留仍有效的拍摄组')
  payload.shootGroups = payload.shootGroups.filter(group => group.id !== 'sim-place-a-day-2')
  await reloadShootIndex()
  assert.equal(await shootPage.locator('#shoot-group').inputValue(), '', '目录中已不存在的选择清除为全部，不留空白页')
  assert.equal(await shootPage.locator('.tile').count(), 48)
  delete payload.shootGroups
  await reloadShootIndex()
  assert.ok(await shootPage.locator('#shoot-group').isDisabled())
  assert.match(await shootPage.locator('#shoot-group-status').textContent(), /目录尚未准备/)
  assert.equal(await shootPage.locator('.tile').count(), 48, '旧索引缺少可选拍摄目录不能整页报错')
  payload.shootGroups = [{ id: 'invalid-directory' }]
  await reloadShootIndex()
  assert.match(await shootPage.locator('#shoot-group-status[role=alert]').textContent(), /目录格式无效/)
  assert.equal(await shootPage.locator('.tile').count(), 48, '可选目录错误只停用该筛选，不阻断原图墙')
  payload = shootFixture()
  payload.shootGroups[0].name = hostile + ' - 010203' + '模拟长地名'.repeat(30)
  payload.items.find(item => item.rank === 1).capture.placeName = hostile
  payload.items.find(item => item.rank === 1).capture.placeSource = hostile
  await reloadShootIndex()
  await selectShoot('sim-place-a-day-1')
  await shootPage.setViewportSize({ width: 360, height: 800 })
  assert.ok((await shootPage.locator('#shoot-group-status').textContent()).includes(hostile))
  assert.ok((await shootPage.locator('.capture-summary').first().textContent()).includes(hostile))
  assert.equal(await shootPage.locator('img[src=x]').count(), 0)
  assert.ok(await shootPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '超长地名目录选项不得撑宽手机页面')
  assert.deepEqual(shootErrors, [])
  assert.ok(shootRequests.every(url => new URL(url).origin === origin), '地点筛选与大致地名不得自动外发或加载地图')
  assert.ok(shootRequests.every(url => !new URL(url).pathname.includes('/gallery/group/sim-')), '高层组ID绝不用于连拍详情请求')
  await shootPage.close()

  payload = baseline()
  const photoContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true })
  const photoPage = await photoContext.newPage(), photoRequests = [], photoErrors = []
  photoPage.on('request', request => photoRequests.push(request.url()))
  photoPage.on('pageerror', error => photoErrors.push(error.message))
  await photoPage.goto(origin)
  await photoPage.locator('.tile-open').first().tap()
  await photoPage.locator('.preview-area[aria-busy="false"]').waitFor()
  const originalCount = () => photoRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length
  const previewImage = () => photoPage.locator('.crop-wrap img:visible')
  const waitPhoto = open => photoPage.waitForFunction(value => document.getElementById('photo-viewer').open === value, open)
  const unchangedUrl = photoPage.url()
  assert.equal(originalCount(), 0, '初次加载和打开详情不请求原片')
  assert.match(await previewImage().getAttribute('src'), /^\/media\//)
  assert.equal(await photoPage.locator('a[href^="/original/"]').count(), 0)
  await photoPage.getByRole('switch', { name: '分割轮廓', exact: true }).check()
  await photoPage.getByRole('switch', { name: '姿态点', exact: true }).check()
  assert.equal(await photoPage.locator('.crop-wrap>svg').getAttribute('viewBox'), '100 200 400 300')
  previewDelay = 150
  await photoPage.locator('.original-toggle').tap()
  assert.match(await photoPage.locator('.preview-status').textContent(), /正在加载完整画面预览/)
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'false', '加载未成功时不伪装原图状态')
  await photoPage.locator('.preview-area[aria-busy="false"]').waitFor()
  previewDelay = 0
  assert.equal(originalCount(), 0, 'DPR3手机fit仍只用适当预览，不首开6K原图')
  assert.equal(await previewImage().getAttribute('src'), '/preview/p000001/960.jpg')
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'true')
  assert.equal(await photoPage.locator('.crop-wrap>svg').getAttribute('viewBox'), '0 0 4000 6000', '预览仍按EXIF后原frame尺寸映射原坐标，不按小图像素改变坐标')
  assert.equal(await photoPage.locator('.crop-wrap [data-instance-mask]').count(), 1)
  assert.equal(await photoPage.locator('.crop-wrap [data-pose-point]').count(), 1)
  assert.equal(photoPage.url(), unchangedUrl)
  await photoPage.locator('.original-toggle').tap()
  assert.match(await previewImage().getAttribute('src'), /^\/media\//)
  assert.equal(await photoPage.locator('.crop-wrap>svg').getAttribute('viewBox'), '100 200 400 300')
  await photoPage.locator('.original-toggle').tap()
  assert.equal(originalCount(), 0)
  assert.equal(photoRequests.filter(url => url.endsWith('/preview/p000001/960.jpg')).length, 1, '当前照片重复同档不重复下载')
  const selectedBeforeViewer = await photoPage.locator('.candidate.focused').getAttribute('data-candidate-id')
  await previewImage().tap()
  await waitPhoto(true)
  await photoPage.locator('#photo-stage img[src="/preview/p000001/1920.jpg"]').waitFor()
  assert.equal(originalCount(), 0, '全屏fit仅升级1920预览')
  assert.equal(await photoPage.locator('#photo-stage svg, #photo-stage canvas').count(), 0, 'viewer始终为真实img，不烘焙标注')
  assert.equal(await photoPage.locator('#photo-stage img').evaluate(image => image.tagName), 'IMG')
  const cdp = await photoContext.newCDPSession(photoPage)
  previewStatus = 503
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 130, y: 380, id: 1 }, { x: 230, y: 380, id: 2 }] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 80, y: 380, id: 1 }, { x: 280, y: 380, id: 2 }] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await photoPage.locator('#photo-status[role=alert]').waitFor()
  assert.match(await photoPage.locator('#photo-status').textContent(), /升级失败，仍显示已加载预览/)
  assert.equal(await photoPage.locator('#photo-stage img').getAttribute('src'), '/preview/p000001/1920.jpg', '高档失败保留已加载低档，不闪空或整图fallback')
  assert.equal(originalCount(), 0)
  const failedTransform = await photoPage.locator('#photo-stage img').evaluate(image => image.style.transform)
  previewStatus = 200
  await photoPage.locator('#photo-retry').tap()
  await photoPage.locator('#photo-stage img[src="/preview/p000001/3840.jpg"]').waitFor()
  assert.equal(await photoPage.locator('#photo-stage img').evaluate(image => image.style.transform), failedTransform, '重试升级不重置手势状态')
  assert.ok(await photoPage.locator('#photo-viewer').evaluate(node => node.open), '捏合释放不能误退出')
  assert.ok(await photoPage.locator('#photo-stage img').evaluate(image => new DOMMatrixReadOnly(getComputedStyle(image).transform).a > 1.5), '双指实际放大图片')
  const beforePan = await photoPage.locator('#photo-stage img').evaluate(image => new DOMMatrixReadOnly(getComputedStyle(image).transform).m41)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 160, y: 400, id: 1 }] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: 210, y: 420, id: 1 }] })
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  assert.ok(await photoPage.locator('#photo-viewer').evaluate(node => node.open), '拖动释放不能误退出')
  assert.notEqual(await photoPage.locator('#photo-stage img').evaluate(image => new DOMMatrixReadOnly(getComputedStyle(image).transform).m41), beforePan)
  const transformBeforeUpgrade = await photoPage.locator('#photo-stage img').evaluate(image => image.style.transform)
  const sizeBeforeUpgrade = await photoPage.locator('#photo-stage img').evaluate(image => [image.clientWidth, image.clientHeight])
  await photoPage.getByRole('button', { name: '放大照片', exact: true }).tap()
  const transformAtUpgradeStart = await photoPage.locator('#photo-stage img').evaluate(image => image.style.transform)
  await photoPage.locator('#photo-stage img[src="/original/p000001"]').waitFor()
  assert.equal(originalCount(), 1, '明显放大到超过3840档需求时才取原文件')
  assert.notEqual(transformAtUpgradeStart, transformBeforeUpgrade)
  assert.equal(await photoPage.locator('#photo-stage img').evaluate(image => image.style.transform), transformAtUpgradeStart, '升级解码替换不重置缩放和平移')
  assert.deepEqual(await photoPage.locator('#photo-stage img').evaluate(image => [image.clientWidth, image.clientHeight]), sizeBeforeUpgrade, '显示尺寸按原几何固定，不跟naturalWidth跳变')
  assert.equal(await photoPage.locator('.crop-wrap>svg').getAttribute('viewBox'), '0 0 4000 6000')
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: 180, y: 400, id: 1 }] })
  await photoPage.waitForTimeout(550)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  assert.ok(await photoPage.locator('#photo-viewer').evaluate(node => node.open), '长按释放不能误退出')
  assert.ok(await photoPage.locator('#photo-stage img').evaluate(image => image.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }))), '不得阻止原生contextmenu')
  assert.notEqual(await photoPage.locator('#photo-stage img').evaluate(image => getComputedStyle(image).getPropertyValue('-webkit-touch-callout')), 'none')
  await photoPage.getByRole('button', { name: '返回详情', exact: true }).tap()
  await waitPhoto(false)
  assert.ok(await photoPage.locator('#dialog').evaluate(node => node.open))
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'true')
  assert.equal(await photoPage.locator('.candidate.focused').getAttribute('data-candidate-id'), selectedBeforeViewer)
  await previewImage().tap()
  await waitPhoto(true)
  await photoPage.locator('#photo-stage img').tap()
  await waitPhoto(false)
  assert.ok(await photoPage.locator('#dialog').evaluate(node => node.open), '短单击只退出照片viewer，不关闭详情')
  await previewImage().tap()
  await waitPhoto(true)
  await photoPage.keyboard.press('Escape')
  await waitPhoto(false)
  assert.ok(await photoPage.locator('#dialog').evaluate(node => node.open), 'Esc只退出最上层viewer')
  for (const viewport of [{ width: 360, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await photoPage.setViewportSize(viewport)
    await previewImage().tap()
    await waitPhoto(true)
    assert.ok(await photoPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
    const viewerBounds = await photoPage.locator('#photo-viewer').boundingBox()
    assert.ok(viewerBounds.x >= 0 && viewerBounds.y >= 0 && viewerBounds.width <= viewport.width + 1 && viewerBounds.height <= viewport.height + 1)
    await photoPage.getByRole('button', { name: '返回详情', exact: true }).tap()
    await waitPhoto(false)
  }
  await photoPage.setViewportSize({ width: 390, height: 844 })
  await photoPage.getByRole('button', { name: '查看组内实例 2 · 模拟来源-002.jpg', exact: true }).tap()
  await photoPage.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'false', '换帧后恢复该鸟裁剪，不沿用别帧原图')
  assert.equal(await photoPage.locator('.crop-wrap [data-instance-mask],.crop-wrap [data-pose-point]').count(), 0)
  previewStatus = 503
  await photoPage.locator('.original-toggle').tap()
  await photoPage.locator('.preview-status[role=alert]').waitFor()
  assert.match(await photoPage.locator('.preview-status').textContent(), /完整画面预览读取失败，仍显示裁剪/)
  assert.equal(originalCount(), 1, '预览404/生成失败不得静默下载整张原图')
  assert.match(await previewImage().getAttribute('src'), /^\/media\//)
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
  previewStatus = 200
  await photoPage.locator('.original-toggle').tap()
  await photoPage.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.equal(await previewImage().getAttribute('src'), '/preview/p000002/960.jpg', '重试精确使用当前帧，不使用拍摄组或上一个鸟实例')
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'true')
  previewDelay = 500
  await previewImage().tap()
  await waitPhoto(true)
  await photoPage.waitForFunction(() => document.querySelector('.preview-area').getAttribute('aria-busy') === 'true')
  await photoPage.getByRole('button', { name: '返回详情', exact: true }).tap()
  await waitPhoto(false)
  await photoPage.getByRole('button', { name: '查看组内实例 0 · 模拟来源-001.jpg', exact: true }).tap()
  await photoPage.locator('.preview-area[aria-busy="false"]').waitFor()
  await photoPage.waitForTimeout(600)
  assert.match(await previewImage().getAttribute('src'), /%231\.jpg$/, '关闭/快速换帧后迟到高清不能覆盖当前裁剪')
  assert.equal(await photoPage.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
  previewDelay = 0
  await previewImage().tap()
  await waitPhoto(true)
  assert.match(await photoPage.locator('#photo-download').getAttribute('href'), /^\/original\/p000001\?download=1$/)
  assert.ok((await photoPage.locator('.photo-hint').textContent()).includes('长按保存当前图'))
  const servedOriginalsBeforeDownload = requests.filter(path => path.startsWith('/original/')).length
  const downloadEvent = photoPage.waitForEvent('download')
  await photoPage.getByRole('link', { name: '保存原图', exact: true }).tap()
  const download = await downloadEvent
  assert.equal(download.url(), origin + '/original/p000001?download=1')
  await download.cancel()
  assert.equal(requests.filter(path => path.startsWith('/original/')).length, servedOriginalsBeforeDownload + 1, '明确保存原图才额外请求未改原文件')
  assert.equal(photoPage.url(), unchangedUrl)
  await photoPage.getByRole('button', { name: '返回详情', exact: true }).tap()
  for (const viewport of [{ width: 360, height: 800 }, { width: 844, height: 390 }]) {
    const fitPage = await photoContext.newPage(), fitRequests = []
    fitPage.on('request', request => fitRequests.push(request.url()))
    await fitPage.setViewportSize(viewport)
    await fitPage.goto(origin)
    await fitPage.locator('.tile-open').first().tap()
    await fitPage.locator('.preview-area[aria-busy="false"]').waitFor()
    await fitPage.locator('.original-toggle').tap()
    await fitPage.locator('.preview-area[aria-busy="false"]').waitFor()
    assert.match(await fitPage.locator('.crop-wrap img:visible').getAttribute('src'), /\/(960|1920)\.jpg$/)
    assert.equal(fitRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 0)
    assert.ok(await fitPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '手机初次完整画面fit保持合适档位与布局')
    await fitPage.close()
  }
  frameDimensions.set('p000001', { width: 400, height: 600 })
  const smallPage = await photoContext.newPage(), smallRequests = []
  smallPage.on('request', request => smallRequests.push(request.url()))
  await smallPage.goto(origin)
  await smallPage.locator('.tile-open').first().tap()
  await smallPage.locator('.preview-area[aria-busy="false"]').waitFor()
  await smallPage.locator('.original-toggle').tap()
  await smallPage.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.deepEqual(await smallPage.locator('.crop-wrap img:visible').evaluate(image => [image.naturalWidth, image.naturalHeight]), [400, 600], '小源图预览不被上采样')
  assert.equal(smallRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 0)
  await smallPage.close(); frameDimensions.clear()
  assert.ok(photoRequests.every(url => new URL(url).origin === origin))
  assert.deepEqual(photoErrors, [])
  await photoContext.close()

  payload = baseline()
  const comparePage = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await comparePage.goto(origin)
  await comparePage.locator('.tile-open').first().waitFor()
  const subject = payload.items.find(item => item.rank === 1)
  const comparisonFrame = { id: subject.frameId, source: { primary: { relative: subject.sourceLabel } }, width: 4000, height: 6000, instances: [makeBird(subject)] }
  for (const [frames, visible] of [
    [[comparisonFrame], false],
    [[{ ...comparisonFrame, instances: [makeBird(subject), makeBird(subject, { id: 8 })] }], true],
    [[comparisonFrame, { ...comparisonFrame, id: 'p999998', instances: [makeBird(subject, { id: 9 })] }], true],
    [[comparisonFrame, { ...comparisonFrame, id: 'p999997', instances: [] }], false],
  ]) {
    groupOverride = { id: subject.groupId, frames }
    await comparePage.locator('.tile-open').first().click()
    await comparePage.locator('#focus-panel').waitFor()
    assert.equal(await comparePage.locator('#candidates').count(), visible ? 1 : 0, '按实际实例数隐藏；单图多鸟、同组多鸟均显示')
    if (visible) {
      assert.equal(await comparePage.locator('#candidates>h3').textContent(), '鸟体对比')
      await comparePage.locator('.candidate button').last().click()
      assert.match(await comparePage.locator('#dialog-title').textContent(), /实例 (8|9)$/)
    }
    await comparePage.locator('#close').click()
  }
  groupOverride = null
  await comparePage.locator('.tile-open').first().click()
  await comparePage.locator('#focus-panel').waitFor()
  await comparePage.evaluate(() => {
    document.getElementById('dialog').close()
    return openDetail(state.active)
  })
  await comparePage.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.equal(await comparePage.locator('#dialog').evaluate(node => node.open), true, '迟到的旧close事件不能清空快速重开的详情')
  await comparePage.locator('#close').click()
  for (const mode of ['notFound', 'error']) {
    referenceMode = mode
    await comparePage.locator('.tile-open').first().click()
    await comparePage.getByText(mode === 'notFound' ? '暂无准确条目' : '参考资料暂不可用', { exact: true }).first().waitFor()
    assert.equal(await comparePage.locator('.classification-details .reference-picture img').count(), 0)
    assert.equal(await comparePage.locator('.classification-details .classification-score').count(), 3, '参考缺失不阻塞原有概率')
    assert.equal(await comparePage.locator('#focus-panel .crop-wrap img').count(), 1)
    await comparePage.locator('#close').click()
  }
  referenceMode = 'ready'
  referenceDelays.set('Simulation primus', 900)
  await comparePage.locator('.tile-open').first().click()
  await comparePage.locator('#focus-panel').waitFor()
  await comparePage.locator('#close').click()
  await comparePage.waitForTimeout(1000)
  assert.equal(await comparePage.locator('#detail').textContent(), '', '关闭详情后迟到资料不得重新插入')
  referenceDelays.clear()
  await comparePage.close()

  const polling = await browser.newPage()
  await polling.clock.install()
  payload = baseline(); payload.status = 'processing'; payload.items = []; payload.summary.preferred = 0; payload.progress = { processed: 0, total: 63 }
  await polling.goto(origin)
  await polling.waitForFunction(() => document.getElementById('empty-title').textContent === '等待本地验鸟结果')
  const beforePolling = requests.filter(path => path === '/gallery.json').length
  payload = baseline()
  await polling.clock.fastForward(10000)
  await polling.locator('.tile').first().waitFor()
  assert.ok(requests.filter(path => path === '/gallery.json').length > beforePolling, '处理中必须自动读取新索引')
  const afterCompletion = requests.filter(path => path === '/gallery.json').length
  await polling.clock.fastForward(20000)
  assert.equal(requests.filter(path => path === '/gallery.json').length, afterCompletion, '完成后停止自动刷新')
  await polling.close()
  if (process.env.BIRDSHOT_GALLERY_INDEX_URL) {
    const source = new URL(process.env.BIRDSHOT_GALLERY_INDEX_URL)
    assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(source.hostname) && source.pathname === '/gallery.json' && !source.username && !source.password && !source.search, '真实索引验证仅允许无密钥的本机 gallery.json')
    const response = await fetch(source, { redirect: 'error' })
    assert.ok(response.ok, '真实本机索引应可读取')
    const snapshot = await response.json()
    payload = snapshot
    const live = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const liveErrors = [], liveRequests = []
    const probeFrameId = process.env.BIRDSHOT_GALLERY_ORIGINAL_FRAME
    let probeFrame
    if (probeFrameId) {
      assert.match(probeFrameId, /^p\d{6}$/, '仅允许显式选择一个真实本机帧验证原图')
      await live.route('**/preview/*', async route => {
        const path = new URL(route.request().url()).pathname
        assert.ok(new RegExp('^/preview/' + probeFrameId + '/(960|1920|3840)\\.jpg$').test(path), '仅代理指定帧和已声明预览档')
        const preview = await fetch(new URL(path, source.origin), { redirect: 'error' })
        assert.ok(preview.ok, '真实分档预览接口必须已部署，不得静默退回原图')
        await route.fulfill({ contentType: 'image/jpeg', body: Buffer.from(await preview.arrayBuffer()) })
      })
      await live.route('**/original/*', async route => {
        const path = new URL(route.request().url()).pathname
        assert.equal(path, '/original/' + probeFrameId, '原图验证不得访问其他帧')
        const original = await fetch(new URL(path, source.origin), { redirect: 'error' })
        assert.ok(original.ok)
        await route.fulfill({ contentType: 'image/jpeg', body: Buffer.from(await original.arrayBuffer()) })
      })
    }
    live.on('pageerror', error => liveErrors.push(error.message))
    live.on('request', request => liveRequests.push(request.url()))
    await live.route('**/gallery/group/*.json', async route => {
      const path = new URL(route.request().url()).pathname
      assert.match(path, /^\/gallery\/group\/g\d{5}\.json$/)
      const groupResponse = await fetch(new URL(path, source.origin), { redirect: 'error' })
      assert.ok(groupResponse.ok, '真实增强组详情应可读取')
      const body = await groupResponse.text()
      if (probeFrameId) probeFrame = JSON.parse(body).frames.find(frame => frame.id === probeFrameId) || probeFrame
      await route.fulfill({ contentType: 'application/json', body })
    })
    await live.goto(origin)
    await live.waitForFunction(() => !document.getElementById('refresh').disabled)
    assert.ok(await live.locator('#notice').evaluate(node => node.hidden || node.getAttribute('role') !== 'alert'), '真实索引应通过新版页面形状校验')
    const preferred = snapshot.items.filter(item => item.decision === 'keep' && item.birdVerification?.status === 'bird')
    assert.equal(await live.locator('.tile').count(), Math.min(48, preferred.length))
    assert.ok(await live.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '真实索引的中文Top3和模型文字在手机上不能溢出')
    const firstPreferred = preferred.toSorted((a, b) => a.rank - b.rank)[0]
    if (firstPreferred?.species?.top3?.[0]?.englishName) assert.equal(await live.locator('.tile').first().locator('.english-name').first().textContent(), firstPreferred.species.top3[0].englishName, '真实常用英文上线后原样展示，不用拉丁学名代替')
    if (firstPreferred?.capture?.status === 'ready') {
      if (firstPreferred.capture.time) {
        assert.match(await live.locator('.tile').first().locator('.capture-summary').textContent(), /\d{4}年\d{1,2}月\d{1,2}日 \d{2}:\d{2}/, '真实拍摄时间应本地化，不保存或输出具体EXIF')
        assert.ok(!(await live.locator('.tile').first().locator('.capture-summary').textContent()).includes(firstPreferred.capture.time), '界面不显示原始ISO时间')
      }
      if (!firstPreferred.capture.location) assert.match(await live.locator('.tile').first().locator('.capture-summary').textContent(), /未记录位置/)
      await live.locator('.tile-open').first().click()
      await live.locator('#focus-panel').waitFor()
      if (!firstPreferred.capture.location) {
        assert.match(await live.locator('.capture-details').textContent(), /未记录位置/)
        assert.equal(await live.getByRole('link', { name: '在地图查看（将打开外部地图）' }).count(), 0, '真实无GPS照片不能使用用户地区参考生成标点')
      }
      assert.ok(await live.locator('#dialog').evaluate(node => node.scrollWidth <= node.clientWidth + 1), '真实拍摄时间/来源在手机详情可换行')
      await live.locator('#close').click()
    }
    if (Array.isArray(snapshot.shootGroups)) {
      assert.equal(await live.locator('#shoot-group option').count(), snapshot.shootGroups.length + 1)
      for (const group of snapshot.shootGroups) {
        const groupItems = snapshot.items.filter(item => item.shootGroup?.id === group.id)
        const groupPreferred = groupItems.filter(item => item.decision === 'keep' && item.birdVerification?.status === 'bird').toSorted((a, b) => a.rank - b.rank)
        assert.equal(group.cropCount, groupItems.length, '真实目录裁剪计数应与对应索引一致；不输出私人组名')
        assert.equal(group.preferredCount, groupPreferred.length, '真实目录优选计数不得改写保留集合')
        assert.ok(group.photoCount >= new Set(groupItems.map(item => item.frameId)).size, '目录原图数不能误标为裁剪数；允许包含未检出照片')
        if (!group.placeName) assert.ok(group.name.startsWith('未知地点 - '), '没有真实地点依据时使用未知地点，不填用户举例')
        await live.locator('#shoot-group').selectOption(group.id)
        assert.equal(await live.locator('#preferred-tab').textContent(), '优选鸟体 ' + groupPreferred.length.toLocaleString('zh-CN'))
        assert.equal(await live.locator('#review-tab').textContent(), '独立审核 ' + (groupItems.length - groupPreferred.length).toLocaleString('zh-CN'), '真实每组两页签独立统计，空裁剪组两数均为零')
        assert.equal(await live.locator('.tile').count(), Math.min(48, groupPreferred.length))
        assert.ok((await live.locator('#shoot-group-status').textContent()).includes(group.name), '真实后端组名必须原样展示')
        assert.ok((await live.locator('#shoot-group-status').textContent()).includes('原图 ' + group.photoCount.toLocaleString('zh-CN')))
        const expectedIds = groupPreferred.slice(0, 48).map(item => item.id)
        assert.deepEqual(await live.locator('.tile-open').evaluateAll(nodes => nodes.map(node => node.dataset.itemId)), expectedIds)
        if (groupPreferred.length) {
          await live.locator('.tile-open').first().click()
          await live.locator('#focus-panel').waitFor()
          assert.equal(new URL(liveRequests.filter(url => new URL(url).pathname.startsWith('/gallery/group/')).at(-1)).pathname, '/gallery/group/' + groupPreferred[0].groupId + '.json', '真实拍摄分组筛选不得改变详情连拍路由')
          assert.ok((await live.locator('.capture-details .shoot-group-label').textContent()).includes(group.name), '真实组详情应包含精确帧的拍摄分组')
          await live.locator('#close').click()
        }
        assert.ok(await live.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '真实分组名称与计数在手机不溢出')
      }
      await live.locator('#shoot-group').selectOption('')
      assert.equal(await live.locator('.tile').count(), Math.min(48, preferred.length))
      console.log('PASS: real shoot-group names/counts checked only in memory; groups=' + snapshot.shootGroups.length + ', directoryPhotos=' + snapshot.shootGroups.reduce((sum, group) => sum + group.photoCount, 0) + ', directoryCrops=' + snapshot.shootGroups.reduce((sum, group) => sum + group.cropCount, 0) + ', directoryPreferred=' + snapshot.shootGroups.reduce((sum, group) => sum + group.preferredCount, 0) + '. No names, dates or private JSON saved.')
    } else {
      assert.ok(await live.locator('#shoot-group').isDisabled())
      assert.match(await live.locator('#shoot-group-status').textContent(), /目录尚未准备/)
      console.log('Current local shoot-group directory is not ready; existing gallery compatibility checked without fabricating groups.')
    }
    if (probeFrameId) {
      const item = snapshot.items.find(candidate => candidate.frameId === probeFrameId)
      assert.ok(item, '指定本机帧必须存在于实际索引')
      const before = liveRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length
      assert.equal(before, 0, '真实索引浏览和详情默认仍零原图请求')
      await live.evaluate(candidate => openDetail(candidate), item)
      await live.locator('.preview-area[aria-busy="false"]').waitFor()
      assert.ok(probeFrame && probeFrame.width > 0 && probeFrame.height > 0)
      await live.locator('.original-toggle').click()
      await live.locator('.preview-area[aria-busy="false"]').waitFor()
      assert.equal(await live.locator('.original-toggle').getAttribute('aria-pressed'), 'true')
      assert.match(await live.locator('.crop-wrap img:visible').getAttribute('src'), /\/(960|1920)\.jpg$/, '真实手机fit仅加载较小预览档')
      assert.equal(liveRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 0)
      const initialSize = await live.locator('.crop-wrap img:visible').evaluate(image => [image.naturalWidth, image.naturalHeight])
      assert.ok(Math.max(...initialSize) <= 1920)
      assert.ok(Math.abs(initialSize[0] / initialSize[1] - probeFrame.width / probeFrame.height) < .002, '预览方向必须对应EXIF转正后的frame比例')
      await live.getByRole('switch', { name: '分割轮廓', exact: true }).check()
      assert.equal(await live.locator('.crop-wrap>svg').getAttribute('viewBox'), [0, 0, probeFrame.width, probeFrame.height].join(' '))
      await live.locator('.crop-wrap img:visible').click()
      await live.waitForFunction(() => document.getElementById('photo-viewer').open)
      for (let step = 0; step < 14; step++) await live.getByRole('button', { name: '放大照片', exact: true }).click()
      await live.locator('#photo-stage img[src="/original/' + probeFrameId + '"]').waitFor()
      assert.deepEqual(await live.locator('#photo-stage img').evaluate(image => [image.naturalWidth, image.naturalHeight]), [probeFrame.width, probeFrame.height])
      await live.getByRole('button', { name: '返回详情', exact: true }).click()
      await live.waitForFunction(() => !document.getElementById('photo-viewer').open)
      await live.locator('.original-toggle').click()
      assert.equal(await live.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
      assert.equal(await live.locator('.crop-wrap>svg').getAttribute('viewBox'), probeFrame.instances.find(bird => String(bird.id) === String(item.instanceId)).cropRect.join(' '))
      assert.equal(liveRequests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 1)
      await live.locator('#close').click()
      console.log('PASS: one selected real frame uses preview at fit, upgrades to full JPEG only after zoom; EXIF orientation, original-coordinate overlays, clean img viewer and crop return checked locally. No private image or screenshot saved.')
    }
    await live.getByRole('tab', { name: /独立审核/ }).click()
    assert.ok(await live.locator('.tile').count() <= 48, '真实审核索引也必须分页')
    await live.locator('#verification').selectOption('pending')
    assert.deepEqual(liveErrors, [])
    assert.ok(liveRequests.every(url => new URL(url).origin === origin), '真实元数据验证不得自动把位置或照片发到外部')
    await live.close()
    console.log('PASS: current local index accepted in memory; items=' + snapshot.items.length + ', processed=' + snapshot.progress.processed + ', preferred=' + preferred.length + ', captureReady=' + snapshot.items.filter(item => item.capture?.status === 'ready').length + ', captureGPS=' + snapshot.items.filter(item => item.capture?.location).length + '. ' + (probeFrameId ? 'One selected original decoded locally; ' : 'Media stayed synthetic; ') + 'no private JSON, EXIF values, photos or screenshots saved.')
  }
  assert.deepEqual(errors, [], '浏览器不能出现未处理错误')
  assert.deepEqual(external, [], '图墙不能向第三方请求照片、脚本或模型')
  assert.ok(requests.filter(path => path.startsWith('/original/')).every(path => ['/original/p000001', '/original/p000002'].includes(path)), '只有明确点击原图控件的模拟帧可以请求原图')
  console.log('PASS: synthetic place/date filters and dynamic tab counts, simplified Chinese/English Top3 and Beijing time, independent default-off overlays, inline original toggle/error/cache/mapping, clean img viewer, pinch/pan/tap/long-press/contextmenu/Esc, mobile layouts and same-origin/no-upload checks. Native phone save-menu behavior is not claimed as device-tested.')
} finally {
  if (browser) await browser.close()
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
}
