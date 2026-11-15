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
  assert.equal(await page.locator('#gallery .english-name,#gallery .reference-picture,#gallery .shoot-group-label').count(), 0)
  assert.equal(requests.filter(url => new URL(url).pathname === '/species-reference').length, 0)
  for (const viewport of [{ width: 360, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await page.setViewportSize(viewport)
    const miniCards = await page.locator('.tile').first().locator('.species-candidate').evaluateAll(nodes => nodes.map(node => ({ x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })))
    assert.ok(miniCards.every(card => Math.abs(card.y - miniCards[0].y) < 1) && miniCards[0].x < miniCards[1].x && miniCards[1].x < miniCards[2].x)
    assert.ok(await page.locator('.tile').first().evaluate(tile => Math.abs(tile.querySelector('.species-candidates').getBoundingClientRect().width - tile.clientWidth) < 1))
    const meta = await page.locator('.tile').first().locator('.capture-summary>span').evaluateAll(nodes => nodes.map(node => ({ x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y })))
    assert.ok(meta[0].x < meta[1].x && Math.abs(meta[0].y - meta[1].y) < 1)
    for (const item of selected) {
      await page.evaluate(candidate => openDetail(candidate), item)
      await page.locator('.preview-area[aria-busy="false"]').waitFor()
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll('.classification-details .reference-picture img')]
        return images.length === 3 && images.every(image => image.complete && image.naturalWidth > 0)
      }, undefined, { timeout: 60000 })
      await page.waitForFunction(() => document.querySelectorAll('.reference-source').length === 3)
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
        const source = page.locator('.reference-source').nth(position)
        assert.ok((await source.textContent()).includes(reference.image.author))
        assert.equal(await source.locator('a').first().getAttribute('href'), reference.image.licenseUrl)
        assert.match(await card.locator('.classification-score').textContent(), /^概率 \d+\.\d%$/)
      }
      assert.equal(await page.locator('.image-controls>*').count(), 3)
      assert.equal(await page.locator('.image-controls input,.image-controls [role=switch]').count(), 0)
      assert.equal(await page.locator('.classification-details .english-name').count(), 3)
      const geometry = await page.locator('.classification-details .species-candidate').evaluateAll(nodes => nodes.map(node => ({
        height: node.getBoundingClientRect().height, bottom: node.querySelector('.reference-links').getBoundingClientRect().bottom,
      })))
      assert.ok(geometry.every(card => Math.abs(card.height - geometry[0].height) < 1 && Math.abs(card.bottom - geometry[0].bottom) < 1))
      const colors = await page.locator('.classification-details .species-candidate').evaluateAll(nodes => nodes.map(node => ({
        background: getComputedStyle(node).backgroundColor.match(/[\d.]+/g).map(Number),
        border: getComputedStyle(node).borderTopColor.match(/[\d.]+/g).map(Number),
      })))
      assert.ok(colors.every(color => color.background[0] < color.background[1] && color.background[1] < color.background[2] && color.border[0] < color.border[2]))
      assert.ok(colors.every((color, position) => position === 0 || color.background[3] <= colors[position - 1].background[3]), 'Blue emphasis follows original probability')
      const bottomStyles = await page.locator('.reference-links').evaluateAll(nodes => nodes.map(node => {
        const footer = getComputedStyle(node), link = getComputedStyle(node.firstElementChild)
        return { top: footer.borderTopWidth, sides: [footer.borderLeftWidth, footer.borderRightWidth], linkBorder: link.borderTopWidth, radius: link.borderTopLeftRadius }
      }))
      assert.ok(bottomStyles.every(style => style.top === '1px' && style.sides.every(value => value === '0px') && style.linkBorder === '0px' && style.radius === '0px'))
      const alphas = await page.locator('.image-controls button').evaluateAll(nodes => nodes.map(node => Number(getComputedStyle(node).backgroundColor.match(/,\s*([\d.]+)\)$/)?.[1] || 1)))
      assert.ok(alphas.every(alpha => alpha >= .25 && alpha <= .35))
      for (const name of ['分割轮廓', '姿态点']) {
        assert.equal(await page.getByRole('button', { name, exact: true }).evaluate(node => node.getAttribute('aria-pressed') === 'true'), false)
      }
      await page.getByRole('button', { name: '分割轮廓', exact: true }).click()
      const activeStyle = await page.getByRole('button', { name: '分割轮廓', exact: true }).evaluate(node => ({
        background: getComputedStyle(node).backgroundColor, opacity: getComputedStyle(node).opacity, shadow: getComputedStyle(node).textShadow,
      }))
      const activeAlpha = Number(activeStyle.background.match(/,\s*([\d.]+)\)$/)?.[1] || 1)
      assert.ok(activeAlpha >= .35 && activeAlpha <= .45 && activeStyle.opacity === '1' && activeStyle.shadow !== 'none')
      assert.equal(await page.locator('[data-instance-mask]').count(), 1)
      assert.equal(await page.getByRole('button', { name: '姿态点', exact: true }).evaluate(node => node.getAttribute('aria-pressed') === 'true'), false)
      assert.equal(await page.locator('#photo-viewer').evaluate(node => node.open), false)
      await page.getByRole('button', { name: '分割轮廓', exact: true }).click()
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
  const nativeProof = []
  for (const dpr of [1, 3]) {
    const nativeContext = await browser.newContext({ isMobile: true, hasTouch: true, deviceScaleFactor: dpr, viewport: { width: 360, height: 800 } })
    const nativePage = await nativeContext.newPage()
    await nativePage.goto(base.href)
    await nativePage.locator('#gallery[aria-busy="false"]').waitFor()
    for (const frameId of ['p000003', 'p000560']) {
      const target = index.items.find(row => row.frameId === frameId)
      assert.ok(target)
      await nativePage.evaluate(candidate => openDetail(candidate), target)
      await nativePage.locator('.preview-area[aria-busy="false"]').waitFor()
      await nativePage.locator('.original-toggle').click()
      await nativePage.locator('.preview-area[aria-busy="false"]').waitFor()
      const fit = await nativePage.locator('.crop-wrap img:visible').evaluate(image => ({ src: new URL(image.currentSrc).pathname, size: [image.naturalWidth, image.naturalHeight] }))
      assert.match(fit.src, /\/(960|1920)\.jpg$/)
      await nativePage.locator('.crop-wrap img:visible').click()
      await nativePage.evaluate(() => {
        const geometry = sizePreviewImage(state.preview, document.createElement('img'), 'original', true)
        state.photo.scale = Math.min(state.photo.maxScale - .01, 3730 / Math.max(geometry.width, geometry.height) / devicePixelRatio)
        transformPhoto(); state.preview.schedule()
      })
      await nativePage.locator('#photo-stage img[src$="/3840.jpg"]').waitFor()
      const medium = await nativePage.locator('#photo-stage img').evaluate(image => [image.naturalWidth, image.naturalHeight])
      await nativePage.evaluate(() => { state.photo.scale = state.photo.maxScale; state.photo.x = 25; transformPhoto(); state.preview.schedule() })
      await nativePage.locator('#photo-stage img[src="/original/' + frameId + '"]').waitFor()
      const proof = await nativePage.evaluate(() => ({
        frameId: state.preview.frame.id, dpr: devicePixelRatio, src: new URL(state.photo.image.currentSrc).pathname,
        natural: [state.photo.image.naturalWidth, state.photo.image.naturalHeight], source: [state.preview.frame.width, state.preview.frame.height],
        scale: state.photo.scale, maxScale: state.photo.maxScale, native: state.preview.images.get('original').native,
        status: document.getElementById('photo-status').textContent,
      }))
      assert.deepEqual(proof.natural, proof.source)
      assert.equal(proof.native, true)
      assert.match(proof.status, /原文件已加载/)
      nativeProof.push({ ...proof, fit, medium })
      await nativePage.locator('#photo-exit').click()
      await nativePage.locator('#close').click()
    }
    await nativeContext.close()
  }
  console.log(JSON.stringify({ nativeResolutionVerified: nativeProof }))
} finally {
  await browser.close()
}
