// SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { eyePatch, groupByModifiedTime, maskedTenengrad, recommend, reliableVisibleEye, tenengrad, type Analysis } from '../src/culling/scoring.ts'
import { decodeMask, encodeMask, maskContains, prototypeMask } from '../src/culling/masks.ts'
import { updateTracks } from '../src/lib/tracker.ts'

function frame(id: string, sharpness: number | null, changes: Partial<Analysis> = {}): Analysis {
  return {
    id, group: 'burst', width: 1000, height: 800, body: [200, 100, 400, 300],
    bodyConfidence: .9, birdCount: 1, eye: { x: 300, y: 180, confidence: .9 },
    beak: { x: 340, y: 180, confidence: .9 }, patch: [287, 167, 27, 27],
    sharpness, issue: null, decision: 'review', reason: '', relative: null, rank: null,
    cohortSize: 0, ...changes,
  }
}

test('受控边缘模糊降低局部 Tenengrad；无纹理不是有效合焦依据', () => {
  const pixels = (smooth: boolean) => {
    const data = new Uint8ClampedArray(33 * 33 * 4)
    for (let y = 0; y < 33; y++) for (let x = 0; x < 33; x++) {
      const level = smooth ? Math.max(0, Math.min(255, (x - 8) * 16)) : x < 16 ? 0 : 255
      const i = (y * 33 + x) * 4
      data[i] = data[i + 1] = data[i + 2] = level
      data[i + 3] = 255
    }
    return data
  }
  assert.ok(tenengrad(pixels(true), 33, 33) < tenengrad(pixels(false), 33, 33) * .2)
  assert.equal(tenengrad(new Uint8Array(33 * 33 * 4), 33, 33), 0)
  assert.throws(() => tenengrad([], 33, 33), /像素数据完整/)
})

test('缺失眼睛、低置信度、多鸟和零梯度不被当成失焦淘汰', () => {
  const inputs = [
    frame('sharp', 100), frame('similar', 85), frame('soft', 20),
    frame('missing', null, { eye: null, issue: '眼睛未定位' }),
    frame('uncertain', 1, { eye: { x: 300, y: 180, confidence: .4 } }),
    frame('multiple', 1, { birdCount: 2 }),
    frame('flat', 0),
  ]
  const results = recommend(inputs)
  assert.deepEqual(results.slice(0, 3).map((r) => r.decision), ['keep', 'keep', 'reject'])
  assert.ok(results.slice(3).every((r) => r.decision === 'review' && r.relative === null))
  assert.equal(results[3].sharpness, null)
  assert.equal(inputs[0].rank, null, '推荐不改写输入分析数据')
})

test('不跨组、不跨尺度或转头比较；两张可比照片也可推荐但不淘汰', () => {
  const results = recommend([
    frame('a', 100), frame('b', 90),
    frame('another-group', 1, { group: 'other' }),
    frame('another-scale', 1, { body: [200, 100, 100, 80] }),
    frame('turned', 1, { beak: { x: 260, y: 180, confidence: .9 } }),
  ])
  assert.deepEqual(results.slice(0, 2).map((r) => r.decision), ['keep', 'keep'])
  assert.ok(results.slice(2).every((r) => r.decision === 'review' && r.rank === null))
  assert.equal(recommend([frame('a', 100), frame('b', 1)])[1].decision, 'review')
})

test('眼区越界不裁短测量，且区域来自原图坐标', () => {
  assert.equal(eyePatch({ x: 2, y: 2, confidence: .9 }, null, [0, 0, 400, 300], 1000, 800), null)
  const patch = eyePatch({ x: 300, y: 180, confidence: .9 }, { x: 340, y: 180, confidence: .9 }, [200, 100, 400, 300], 1000, 800)
  assert.deepEqual(patch, [287, 167, 27, 27])
})

test('背向等双眼可见性矛盾时，单个高置信度眼点不足以评分', () => {
  const eye = { x: 300, y: 180, confidence: .89 }
  assert.equal(reliableVisibleEye([eye, { x: 320, y: 180, confidence: .53 }]), null)
  assert.equal(reliableVisibleEye([eye, { x: 320, y: 180, confidence: .01 }]), eye)
  assert.equal(reliableVisibleEye([eye, { x: 320, y: 180, confidence: .85 }]), eye)
})

test('修改时间分组有间隔与总长度上限，避免链式合并整场拍摄', () => {
  const files = Array.from({ length: 8 }, (_, i) => ({ name: `${i}.jpg`, lastModified: 100000 + i * 2000 }))
  const groups = groupByModifiedTime(files)
  assert.equal(groups.get('0.jpg'), groups.get('5.jpg'))
  assert.notEqual(groups.get('5.jpg'), groups.get('6.jpg'))
  assert.notEqual(groupByModifiedTime([{ name: 'a', lastModified: 1000 }, { name: 'b', lastModified: 5000 }]).get('a'),
    groupByModifiedTime([{ name: 'a', lastModified: 1000 }, { name: 'b', lastModified: 5000 }]).get('b'))
})

test('真实原型掩码正确消除 letterbox 并映射到原图局部，不以框填充分割', () => {
  const prototypes = new Float32Array(16 * 16).fill(-1)
  for (let y = 4; y < 12; y++) for (let x = 5; x < 9; x++) prototypes[y * 16 + x] = 1
  const mask = prototypeMask([1], prototypes, 16, 16, [0, 0, 640, 640], [100, 200, 1280, 640], .5, 0, 160)
  const decoded = decodeMask(mask)
  assert.equal(decoded.reduce((sum, p) => sum + p, 0), 32)
  assert.ok(maskContains(mask, decoded, 580, 360))
  assert.ok(!maskContains(mask, decoded, 120, 360))
  assert.ok(mask.path.includes('M500.0,200.0'))
  assert.deepEqual(decodeMask({ width: 4, height: 4, runs: encodeMask(new Uint8Array(16).fill(1)) }), new Uint8Array(16).fill(1))
})

test('主体指标只计算分割内部原像素，背景边缘不冒充主体清晰度', () => {
  const rgba = new Uint8ClampedArray(33 * 33 * 4)
  const mask = new Uint8Array(33 * 33)
  for (let y = 0; y < 33; y++) for (let x = 0; x < 33; x++) {
    const inside = x >= 8 && x <= 24 && y >= 8 && y <= 24
    const level = inside ? 100 : (x % 2) * 255
    const i = y * 33 + x
    rgba[i * 4] = rgba[i * 4 + 1] = rgba[i * 4 + 2] = level
    rgba[i * 4 + 3] = 255
    mask[i] = inside ? 1 : 0
  }
  assert.equal(maskedTenengrad(rgba, 33, 33, mask).score, 0)
  assert.ok(tenengrad(rgba, 33, 33) > 0)
})

test('多鸟无眼可以依据同一候选对象的主体指标推荐，不能自动淘汰或串对象', () => {
  const mask = prototypeMask([1], new Float32Array(16).fill(1), 4, 4, [0, 0, 640, 640], [0, 0, 1000, 800], .64, 0, 64)
  const results = recommend([
    frame('a', null, { eye: null, birdCount: 3, issue: '眼睛不可见', bodySharpness: 100, mask, subject: 'bird-1' }),
    frame('b', null, { eye: null, birdCount: 3, issue: '眼睛不可见', bodySharpness: 10, mask, subject: 'bird-1' }),
    frame('another-bird', null, { eye: null, birdCount: 3, bodySharpness: 1000, mask, subject: 'bird-2' }),
  ])
  assert.equal(results[0].decision, 'keep')
  assert.equal(results[0].basis, 'body')
  assert.equal(results[1].decision, 'review')
  assert.equal(results[2].rank, null)
  assert.ok(results.every((r) => r.decision !== 'reject'))
})

test('筛片已接受的低置信度实例可建立候选轨迹，保留原相机默认阈值', () => {
  const detections = [{ bbox: [10, 10, 50, 80] as [number, number, number, number], score: .4 }]
  assert.equal(updateTracks([], detections, 1).tracks.length, 0)
  const first = updateTracks([], detections, 1, .35)
  assert.equal(first.tracks.length, 1)
  assert.equal(updateTracks(first.tracks, detections, first.nextId, .35).tracks[0].id, first.tracks[0].id)
})

test('未覆核眼睛可见性时，高置信度候选眼区不得参与推荐', () => {
  const results = recommend([frame('a', 100), frame('b', 90), frame('c', 1)], { useEyes: false })
  assert.ok(results.every((r) => r.basis === null && r.decision === 'review'))
})
