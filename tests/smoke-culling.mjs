// SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { chromium } from 'playwright'

const url = process.env.BIRDSHOT_DEMO_URL ?? 'http://127.0.0.1:5173/cull'
const artifacts = resolve(process.env.BIRDSHOT_ARTIFACT_DIR ?? '.tools')
await mkdir(artifacts, { recursive: true })
await mkdir('.tools', { recursive: true })
const browser = await chromium.launch({ channel: process.env.BIRDSHOT_BROWSER_CHANNEL ?? 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 })
const externalRequests = []
const errors = []
page.on('request', (request) => {
  if (new URL(request.url()).origin !== new URL(url).origin && !request.url().startsWith('data:') && !request.url().startsWith('blob:')) {
    externalRequests.push(request.url())
  }
})
page.on('pageerror', (error) => errors.push(error.message))

async function analyze() {
  await page.locator('.analyze-button').click()
  await page.waitForFunction(() => !document.querySelector('.analyze-button')?.disabled, undefined, { timeout: 360000 })
  const failures = await page.locator('.cull-error').count()
  assert.equal(failures, 0, failures ? await page.locator('.cull-error').textContent() : '')
}

async function downloadReport(path) {
  const pending = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出标签 JSON ↓' }).click()
  const download = await pending
  await download.saveAs(path)
  return JSON.parse(await readFile(path, 'utf8'))
}

function counts(results) {
  return Object.fromEntries(['keep', 'review', 'reject'].map((d) => [d, results.filter((r) => r.decision === d).length]))
}

try {
  await page.goto(url)
  await page.waitForFunction(() => document.querySelectorAll('.frame-card').length === 36)
  await analyze()
  const sample = await downloadReport(resolve('public', 'culling', 'results.json'))
  assert.equal(sample.results.length, 36)
  assert.ok(sample.results.some((r) => r.eye && r.sharpness > 0), '真实鸟帧至少应有一个可靠眼区结果')
  assert.ok(sample.results.filter((r) => r.sharpness === null).every((r) => r.decision !== 'reject'), '没有眼区结果不能自动淘汰')
  assert.ok(sample.results.every((r) => r.instances.some((bird) => bird.mask?.path && bird.bodySharpness !== null)), '实际分割和主体内部指标必须执行')
  assert.ok(await page.locator('[data-instance-mask]').count() > 0)
  assert.ok(await page.locator('[data-pose-point]').count() > 0)
  await writeFile(join(artifacts, 'birdshot-demo-results.json'), JSON.stringify(sample, null, 2))
  await page.screenshot({ path: join(artifacts, 'birdshot-demo-preview.png'), fullPage: true })

  const controlled = await page.evaluate(async (report) => {
    const { tenengrad } = await import('/src/culling/scoring.ts')
    const selected = report.results.find((r) => r.patch && r.sharpness > 0)
    const image = new Image()
    image.src = report.photos.find((p) => p.id === selected.id).url
    await image.decode()
    const [x, y, width, height] = selected.patch
    const crop = document.createElement('canvas')
    crop.width = width
    crop.height = height
    const context = crop.getContext('2d')
    context.drawImage(image, x, y, width, height, 0, 0, width, height)
    const original = tenengrad(context.getImageData(0, 0, width, height).data, width, height)
    const blur = document.createElement('canvas')
    blur.width = width
    blur.height = height
    const blurredContext = blur.getContext('2d')
    blurredContext.filter = 'blur(3px)'
    blurredContext.drawImage(crop, 0, 0)
    const blurred = tenengrad(blurredContext.getImageData(0, 0, width, height).data, width, height)
    return { kind: 'explicitly-controlled-eye-crop-blur-not-real-burst', frame: selected.id, original, blurred }
  }, sample)
  assert.ok(controlled.blurred < controlled.original * .5, '同一真实眼区的明确受控模糊应明显降低梯度')
  await writeFile(join(artifacts, 'birdshot-controlled-check.json'), JSON.stringify(controlled, null, 2))
  console.log('Real video:', JSON.stringify(counts(sample.results)), 'controlled eye blur ratio:', (controlled.blurred / controlled.original).toFixed(3))

  const first = sample.results.find((r) => r.patch)
  const firstPhoto = sample.photos.find((p) => p.id === first.id)
  const encoded = (await readFile(resolve('public', firstPhoto.url.slice(1)))).toString('base64')
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>BirdShot 鸟类筛片实测</title>
<style>body{max-width:1050px;margin:40px auto;padding:0 24px;font:15px/1.8 "Segoe UI",sans-serif;background:#f5f5ee;color:#344529}h1{font-size:32px}img{width:100%;border-radius:10px}table{border-collapse:collapse;width:100%;background:#fffef9}td,th{text-align:left;padding:7px 14px;border-bottom:1px solid #e4e9d9}small{color:#768368}.card{padding:20px;background:#e9efd9;border-radius:10px;margin:20px 0}a{color:#56743a}</style>
<h1>BirdShot · 鸟类筛片实测</h1><p>36 张真实连续 4K 视频帧；YOLO11s-seg 真实实例分割 → 六点候选姿态 → 原像素主体/可选眼区指标 → 同对象比较。只读原片，未上传照片。</p>
<div class="card">实际结果：优先保留 ${counts(sample.results).keep}，待审 ${counts(sample.results).review}，建议淘汰 ${counts(sample.results).reject}。这段视频相似帧差异较小，不为了展示淘汰而硬删。<br>首次帧眼区 ${first.patch[2]}×${first.patch[3]}，梯度 ${Math.round(first.sharpness)}。受控验证：同一眼区人工模糊后的梯度为原来的 ${(controlled.blurred / controlled.original * 100).toFixed(1)}%（受控测试，非真实连拍）。</div>
<img src="data:image/jpeg;base64,${encoded}" alt="Giles Laurent 拍摄的真实斑鱼狗视频帧">
<p><small>Giles Laurent / <a href="${sample.source}">Wikimedia Commons 原视频</a> / <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>；保持原分辨率抽取连续帧，JPEG 重新编码。视频连续帧，非相机连拍，无人工质量标签。</small></p>
<table><thead><tr><th>帧</th><th>眼区梯度</th><th>相对最佳</th><th>建议</th></tr></thead><tbody>${sample.results.map((r) => `<tr><td>${r.id}</td><td>${r.sharpness === null ? '未判定' : Math.round(r.sharpness)}</td><td>${r.relative === null ? '—' : `${Math.round(r.relative * 100)}%`}</td><td>${({ keep: '优先保留', review: '待审', reject: '建议淘汰' })[r.decision]}</td></tr>`).join('')}</tbody></table>
<h2>限制</h2><p>没有真实合焦或选片真值评测，不代表算法准确率已验证。主体内部纹理不是眼部合焦，无眼仍可独立展示与比较主体指标，且不据此自动淘汰。姿态只支持喙、左右眼、头顶、左右肩部；不画人体骨架。候选跟踪不是身份保证，须人工选对象和覆核。个人原片不包含在本报告中。</p>
<p><a href="${url}">本机体验 Demo</a> · <a href="${new URL('/culling/source.zip', url)}">AGPL-3.0 源代码包</a> · <a href="https://huggingface.co/synthet/eye-pose-v0">模型来源与 CUB 非商业训练数据限制</a></p></html>`
  await writeFile(join(artifacts, 'birdshot-demo-report.html'), html)

  // Import is exercised through the real file picker, without a server upload.
  await page.locator('input[type=file]').setInputFiles(sample.photos.slice(0, 3).map((p) => resolve('public', p.url.slice(1))))
  await page.waitForFunction(() => document.querySelectorAll('.frame-card').length === 3)
  await analyze()
  const imported = await downloadReport(resolve('.tools', 'import-check.json'))
  assert.equal(imported.results.length, 3)
  assert.ok(imported.results.some((r) => r.sharpness > 0))
  await page.getByRole('button', { name: '淘汰', exact: true }).click()
  const manual = await downloadReport(resolve('.tools', 'manual-check.json'))
  assert.equal(Object.values(manual.overrides)[0], 'reject')
  assert.deepEqual(manual.results, imported.results, '人工覆核不改写算法分析')
  assert.ok(manual.photos.every((p) => !p.url), '导入照片的 blob 地址不写入报告')

  await page.getByRole('button', { name: '公开多鸟图', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.frame-card').length === 1)
  await analyze()
  const multi = await downloadReport(resolve('public', 'culling', 'multi-results.json'))
  assert.ok(multi.results[0].instances.length >= 2, '真实公开雌雄绿头鸭图应检出两只鸟')
  assert.ok(multi.results[0].instances.every((bird) => bird.mask?.path), '每个实际实例应有模型分割掩码')
  const selectedBefore = multi.results[0].selectedInstance
  await page.locator('.instance-picker button').filter({ hasText: selectedBefore === 1 ? '#2' : '#1' }).click()
  const switched = await downloadReport(resolve('.tools', 'multi-switch-check.json'))
  assert.notEqual(switched.results[0].selectedInstance, selectedBefore)
  await page.screenshot({ path: join(artifacts, 'birdshot-multi-preview.png'), fullPage: true })
  const maskCount = await page.locator('[data-instance-mask]').count()
  await page.getByLabel('分割', { exact: true }).uncheck()
  assert.equal(await page.locator('[data-instance-mask]').count(), 0)
  await page.getByLabel('分割', { exact: true }).check()
  assert.equal(await page.locator('[data-instance-mask]').count(), maskCount)
  console.log('Public multi-bird instances:', multi.results[0].instances.length, 'real mask overlay and instance switching passed.')

  const requested = (process.env.BIRDSHOT_LOCAL_GROUPS ?? '').split(',').filter(Boolean)
  if (requested.length) {
    const localResponse = await page.request.get(new URL('/culling/local/manifest.json', url).href)
    assert.ok(localResponse.ok(), '本机验证需要先配置只读照片目录')
    const manifest = await localResponse.json()
    await page.getByRole('button', { name: '本机照片目录', exact: true }).click()
    await page.getByRole('combobox', { name: '选择本机 EXIF 时间候选组' }).waitFor()
    const reports = []
    for (const id of requested) {
      const group = manifest.groups.find((g) => g.id === id)
      assert.ok(group, `缺失指定本机候选组 ${id}`)
      assert.ok(group.photos.length <= 20, '本机验证只读取少量选定帧')
      const times = group.photos.map((p) => Date.parse(p.capturedAt))
      assert.ok(times.every((t) => Number.isFinite(t)), '验证组应有可读取的真实 EXIF 拍摄时间')
      assert.ok(times.every((t, i) => !i || t - times[i - 1] <= 2000), '不跨时间间隔混入其他组')
      assert.ok(times.at(-1) - times[0] <= 10000, '整个验证组时间跨度不超过 10 秒')
      await page.getByRole('combobox', { name: '选择本机 EXIF 时间候选组' }).selectOption(id)
      await page.waitForFunction((count) => document.querySelectorAll('.frame-card').length === count, group.photos.length)
      await analyze()
      reports.push(await downloadReport(resolve('.tools', `${id}-results.json`)))
    }
    assert.ok(reports.some((r) => r.results.some((a) => a.decision === 'review')), '真实本机照片必须覆盖待审分支')
    assert.ok(reports.some((r) => r.results.some((a) => a.decision === 'keep')), '修复版真实组应实际产生有明确依据的候选推荐')
    assert.ok(reports.some((r) => r.results.some((a) => a.sharpness === null && a.bodySharpness !== null)), '无眼仍应有独立主体指标')
    assert.ok(reports.some((r) => r.results.some((a) => a.instances.length > 1)), '本机验证应覆盖多鸟实例')
    const coverage = reports.map((r) => ({
      group: r.results[0].group, frames: r.results.length,
      bodyFrames: r.results.filter((a) => a.birdCount > 0).length,
      instances: r.results.flatMap((a) => a.instances).length,
      masks: r.results.flatMap((a) => a.instances).filter((bird) => bird.mask?.path).length,
      poseInstances: r.results.flatMap((a) => a.instances).filter((bird) => bird.posePoints.some((point) => point.confidence >= .65)).length,
      eyeFrames: r.results.filter((a) => a.sharpness !== null).length,
      bodyMetricFrames: r.results.filter((a) => a.bodySharpness !== null).length,
      comparableFrames: r.results.filter((a) => a.rank !== null).length,
      basisCounts: { eye: r.results.filter((a) => a.basis === 'eye').length, body: r.results.filter((a) => a.basis === 'body').length },
      counts: counts(r.results),
    }))
    await writeFile(resolve('.tools', 'local-coverage.json'), JSON.stringify(coverage, null, 2))
    console.log('Local selected groups:', coverage)
    const folder = process.env.BIRDSHOT_PHOTO_DIR
    if (folder) {
      const before = JSON.parse(await readFile(resolve('.tools', 'local-before-hashes.json'), 'utf8'))
      for (const [name, hash] of Object.entries(before)) {
        assert.equal(createHash('sha256').update(await readFile(join(folder, name))).digest('hex'), hash, '本机原片必须逐字节保持不变')
      }
      console.log('Local originals remain byte-identical:', Object.keys(before).length)
    }
  }

  await page.locator('input[type=file]').setInputFiles({ name: 'invalid.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('not-an-image') })
  await page.waitForFunction(() => document.querySelectorAll('.frame-card').length === 1)
  await page.locator('.analyze-button').click()
  await page.getByRole('alert').waitFor()
  assert.match(await page.getByRole('alert').textContent(), /分析未完成/)
  assert.ok(await page.getByRole('button', { name: '导出标签 JSON ↓' }).isDisabled())
  assert.deepEqual(externalRequests, [], '本地推理不访问第三方评分或上传服务')
  assert.deepEqual(errors, [], '页面不应有未处理的 JavaScript 错误')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '载入真实样例', exact: true }).click()
  await page.waitForFunction(() => document.querySelectorAll('.frame-card').length === 36)
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), '手机界面不应横向溢出')
  console.log('Browser analysis, import, override, malformed-image, no-upload and mobile checks passed.')
} finally {
  await browser.close()
}
