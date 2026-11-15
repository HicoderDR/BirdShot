// SPDX-License-Identifier: AGPL-3.0-only
// Explicit local/LAN opt-in; real images stay in browser memory, never screenshots.
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { chromium } from 'playwright'

assert.ok(process.env.BIRDSHOT_REPORT_URL, 'Set BIRDSHOT_REPORT_URL to an explicitly authorized report')
const base = new URL(process.env.BIRDSHOT_REPORT_URL)
assert.ok(base.protocol === 'http:' && base.port === '5174' && !base.username && !base.password &&
  base.pathname === '/' && !base.search && !base.hash &&
  /^(127\.0\.0\.1|localhost|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)$/.test(base.hostname))
const digest = value => createHash('sha256').update(value).digest('hex')
const indexResponse = await fetch(new URL('/gallery.json', base), { redirect: 'error' })
assert.equal(indexResponse.status, 200)
const indexText = await indexResponse.text(), index = JSON.parse(indexText)
const served = await fetch(base)
assert.equal(digest(await served.text()), digest(await readFile(new URL('../scripts/gallery-report.html', import.meta.url))), 'Live entry must match this checkout')
if (process.env.BIRDSHOT_EXPECT_PREFERRED) assert.equal(index.summary.preferred, Number(process.env.BIRDSHOT_EXPECT_PREFERRED))
const selected = ['Copsychus saularis', 'Acridotheres cristatellus'].map(name =>
  index.items.find(item => item.species?.top3?.[0]?.scientificName === name))
assert.ok(selected.every(Boolean), 'This explicit regression expects these two actual candidate species')
const browser = await chromium.launch({ channel: process.env.BIRDSHOT_BROWSER_CHANNEL || 'msedge', headless: true })
try {
  const context = await browser.newContext({ isMobile: true, hasTouch: true, deviceScaleFactor: 2, viewport: { width: 390, height: 844 } })
  const page = await context.newPage(), requests = [], errors = []
  page.on('request', request => requests.push(request.url()))
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(base.href)
  await page.locator('#gallery[aria-busy="false"]').waitFor()
  for (const viewport of [{ width: 360, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(viewport)
    for (const item of selected) {
      await page.evaluate(candidate => openDetail(candidate), item)
      await page.locator('.preview-area[aria-busy="false"]').waitFor()
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll('.classification-details .reference-picture img')]
        return images.length === 3 && images.every(image => image.complete && image.naturalWidth > 0)
      }, undefined, { timeout: 60000 })
      assert.equal(await page.locator('#dialog-title').textContent(), item.species.top3[0].commonName + ' · 实例 ' + item.instanceId)
      assert.equal(await page.locator('.details-copy>h3').count(), 0)
      assert.ok(await page.locator('#dialog').evaluate(node => node.scrollWidth <= node.clientWidth + 1))
      const heading = await page.locator('.dialog-heading').boundingBox(), close = await page.locator('#close').boundingBox()
      assert.ok(close.x >= heading.x + heading.width && close.width >= 44 && close.height >= 44 &&
        Math.abs(close.y + close.height / 2 - heading.y - heading.height / 2) < 2)
      const cards = await page.locator('.classification-details .species-candidate').evaluateAll(nodes =>
        nodes.map(node => ({ x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })))
      assert.ok(cards.every(card => Math.abs(card.y - cards[0].y) < 1) && cards[0].x < cards[1].x && cards[1].x < cards[2].x)
      for (const [position, candidate] of item.species.top3.entries()) {
        const reference = await (await fetch(new URL('/species-reference?scientificName=' + encodeURIComponent(candidate.scientificName), base))).json()
        assert.equal(reference.scientificName, candidate.scientificName)
        const card = page.locator('.classification-details .species-candidate').nth(position)
        assert.equal(await card.locator('.reference-picture img').getAttribute('src'), reference.image.url)
        assert.equal(await card.locator('.reference-links>a').getAttribute('href'), reference.article.url)
        assert.ok((await card.locator('.reference-credit').textContent()).includes(reference.image.author))
        assert.equal(await card.locator('.reference-credit a').first().getAttribute('href'), reference.image.licenseUrl)
      }
      assert.equal(await page.locator('.image-controls>*').count(), 3)
      for (const name of ['分割轮廓', '姿态点']) {
        assert.equal(await page.getByRole('switch', { name, exact: true }).isChecked(), false)
      }
      await page.getByRole('switch', { name: '分割轮廓', exact: true }).check()
      assert.equal(await page.locator('[data-instance-mask]').count(), 1)
      assert.equal(await page.getByRole('switch', { name: '姿态点', exact: true }).isChecked(), false)
      assert.equal(await page.locator('#photo-viewer').evaluate(node => node.open), false)
      await page.getByRole('switch', { name: '分割轮廓', exact: true }).uncheck()
      const group = await (await fetch(new URL('/gallery/group/' + item.groupId + '.json', base))).json()
      const count = group.frames.reduce((sum, frame) => sum + frame.instances.length, 0)
      assert.equal(await page.locator('#candidates').count(), count > 1 ? 1 : 0)
      if (count > 1) {
        assert.equal(await page.locator('#candidates>h3').textContent(), '鸟体对比')
        await page.locator('.candidate button').last().click()
        const lastFrame = group.frames.findLast(frame => frame.instances.length)
        assert.ok((await page.locator('#dialog-title').textContent()).endsWith('实例 ' + lastFrame.instances.at(-1).id))
      }
      await page.locator('#close').click()
    }
  }
  assert.equal(requests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 0, 'No original preloading')
  assert.ok(requests.every(url => new URL(url).origin === base.origin), 'Browser media and metadata must stay same-origin')
  await page.setViewportSize({ width: 390, height: 844 })
  const item = index.items.find(row => row.frameId === 'p000560') || selected[0]
  await page.evaluate(candidate => openDetail(candidate), item)
  await page.locator('.preview-area[aria-busy="false"]').waitFor()
  await page.locator('.original-toggle').tap()
  await page.locator('.preview-area[aria-busy="false"]').waitFor()
  assert.match(await page.locator('.crop-wrap img:visible').getAttribute('src'), /^\/preview\/p\d{6}\/(960|1920)\.jpg$/)
  assert.equal(requests.filter(url => new URL(url).pathname.startsWith('/original/')).length, 0)
  const address = page.url()
  await page.locator('.crop-wrap img:visible').tap({ position: { x: 20, y: 20 } })
  await page.waitForFunction(() => document.getElementById('photo-viewer').open)
  for (let step = 0; step < 14; step++) await page.locator('#photo-zoom-in').click()
  await page.locator('#photo-stage img[src="/original/' + item.frameId + '"]').waitFor()
  await page.locator('#photo-exit').click()
  await page.locator('.original-toggle').tap()
  assert.equal(await page.locator('.original-toggle').getAttribute('aria-pressed'), 'false')
  assert.equal(page.url(), address)
  await page.locator('#close').click()
  const single = index.items.find(row => index.items.filter(candidate => candidate.groupId === row.groupId).length === 1)
  if (single) {
    await page.evaluate(candidate => openDetail(candidate), single)
    await page.locator('#focus-panel').waitFor()
    assert.equal(await page.locator('#candidates').count(), 0)
    await page.locator('#close').click()
  }
  assert.deepEqual(errors, [])
  const after = await (await fetch(new URL('/gallery.json', base))).text()
  assert.equal(digest(after), digest(indexText), 'Browsing must not change any selection or metadata')
  console.log(JSON.stringify({ passed: true, sourceHtmlMatches: true, preferred: index.summary.preferred,
    crops: index.items.length, shootGroups: index.shootGroups.length,
    photos: index.shootGroups.reduce((sum, group) => sum + group.photoCount, 0),
    actualReferenceSpecies: new Set(selected.flatMap(row => row.species.top3.map(candidate => candidate.scientificName))).size,
    viewports: ['360x800', '390x844', '844x390'],
    privatePhotosOrScreenshotsSaved: false, nativePhoneSaveMenuTested: false }))
  await context.close()
} finally {
  await browser.close()
}
