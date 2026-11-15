// SPDX-License-Identifier: AGPL-3.0-only
import type { BirdMask } from './masks'
import { updateTracks, type BirdTrack } from '../lib/tracker.ts'

export type Box = [number, number, number, number]
export type Point = { x: number; y: number; confidence: number }
export type Decision = 'keep' | 'review' | 'reject'
export type QualityBasis = 'eye' | 'body'
export type PosePoint = Point & { index: number }
export type BirdInstance = {
  id: number
  trackId: number | null
  body: Box
  bodyConfidence: number
  mask: BirdMask | null
  maskIssue: string | null
  posePoints: PosePoint[]
  poseIssue: string | null
  eye: Point | null
  beak: Point | null
  patch: Box | null
  sharpness: number | null
  eyeIssue: string | null
  bodySharpness: number | null
  bodyPixels: number
  matchingIssue?: string | null
}
export type Analysis = {
  id: string
  group: string
  width: number
  height: number
  body: Box | null
  bodyConfidence: number | null
  birdCount: number
  eye: Point | null
  beak: Point | null
  patch: Box | null
  sharpness: number | null
  issue: string | null
  decision: Decision
  reason: string
  relative: number | null
  rank: number | null
  cohortSize: number
  instances?: BirdInstance[]
  selectedInstance?: number | null
  subject?: string
  basis?: QualityBasis | null
  bodySharpness?: number | null
  bodyPixels?: number
  detectionMode?: string
  mask?: BirdMask | null
  posePoints?: PosePoint[]
  identityIssue?: string | null
}

export function matchSubjects(frames: Analysis[], strict = false): Analysis[] {
  const groups = new Map<string, { tracks: BirdTrack[]; nextId: number; target: number | null }>()
  const overlap = (a: Box, b: Box) => {
    const intersection = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]))
      * Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]))
    return intersection / (a[2] * a[3] + b[2] * b[3] - intersection)
  }
  return frames.map((frame) => {
    const state = groups.get(frame.group) ?? { tracks: [], nextId: 1, target: null }
    const detections = (frame.instances ?? []).map((bird) => ({ bbox: bird.body, score: bird.bodyConfidence }))
    const tracked = updateTracks(state.tracks, detections, state.nextId, .35)
    const instances = (frame.instances ?? []).map((bird) => {
      const alternatives = state.tracks.map((track) => {
        const predicted: Box = [0, 1, 2, 3].map((i) => track.bbox[i] + track.velocity[i]) as Box
        return overlap(predicted, bird.body)
      }).sort((a, b) => b - a)
      const ambiguous = strict && alternatives.length > 1 && alternatives[1] >= .18 && alternatives[0] - alternatives[1] < .1
      return {
        ...bird, trackId: tracked.tracks.find((track) => !track.missed && track.bbox === bird.body)?.id ?? null,
        matchingIssue: ambiguous ? '多个跨帧鸟对象匹配接近，严格策略不确认同一对象' : null,
      }
    })
    state.tracks = tracked.tracks
    state.nextId = tracked.nextId
    const largest = instances.toSorted((a, b) => b.body[2] * b.body[3] - a.body[2] * a.body[3])[0]
    if (state.target === null && largest) state.target = largest.trackId
    groups.set(frame.group, state)
    const target = instances.find((bird) => bird.trackId !== null && bird.trackId === state.target) ?? largest
    return target ? selectInstance({ ...frame, instances }, target.id) : { ...frame, instances }
  })
}

export function tenengrad(rgba: ArrayLike<number>, width: number, height: number): number {
  if (width < 3 || height < 3 || rgba.length !== width * height * 4) {
    throw new Error('清晰度区域必须至少为 3×3，且像素数据完整')
  }
  const gray = new Float64Array(width * height)
  for (let i = 0; i < gray.length; i++) {
    gray[i] = .299 * rgba[i * 4] + .587 * rgba[i * 4 + 1] + .114 * rgba[i * 4 + 2]
  }
  let sum = 0
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x
      const sx = -gray[i - width - 1] + gray[i - width + 1]
        - 2 * gray[i - 1] + 2 * gray[i + 1] - gray[i + width - 1] + gray[i + width + 1]
      const sy = -gray[i - width - 1] - 2 * gray[i - width] - gray[i - width + 1]
        + gray[i + width - 1] + 2 * gray[i + width] + gray[i + width + 1]
      sum += sx * sx + sy * sy
    }
  }
  return sum / ((width - 2) * (height - 2))
}

export function eyePatch(eye: Point, beak: Point | null, body: Box, width: number, height: number): Box | null {
  const headScale = beak ? Math.hypot(beak.x - eye.x, beak.y - eye.y) : body[2] * .08
  const radius = Math.round(Math.max(8, Math.min(32, headScale * .32)))
  const x = Math.round(eye.x) - radius
  const y = Math.round(eye.y) - radius
  if (x < 0 || y < 0 || x + radius * 2 + 1 > width || y + radius * 2 + 1 > height) return null
  return [x, y, radius * 2 + 1, radius * 2 + 1]
}

export function reliableVisibleEye(eyes: [Point, Point]): Point | null {
  const [best, other] = eyes.toSorted((a, b) => b.confidence - a.confidence)
  if (!Number.isFinite(best.confidence) || !Number.isFinite(other.confidence) || best.confidence < .65) return null
  if (other.confidence > .2 && other.confidence < .65) return null
  return best
}

export function maskedTenengrad(rgba: ArrayLike<number>, width: number, height: number, mask: Uint8Array) {
  if (rgba.length !== width * height * 4 || mask.length !== width * height) throw new Error('主体像素与分割掩码尺寸不匹配')
  const gray = new Float64Array(width * height)
  for (let i = 0; i < gray.length; i++) gray[i] = .299 * rgba[i * 4] + .587 * rgba[i * 4 + 1] + .114 * rgba[i * 4 + 2]
  let sum = 0
  let pixels = 0
  for (let y = 1; y < height - 1; y++) for (let x = 1; x < width - 1; x++) {
    const i = y * width + x
    if (!mask[i - width - 1] || !mask[i - width] || !mask[i - width + 1] || !mask[i - 1] || !mask[i]
      || !mask[i + 1] || !mask[i + width - 1] || !mask[i + width] || !mask[i + width + 1]) continue
    const sx = -gray[i - width - 1] + gray[i - width + 1] - 2 * gray[i - 1] + 2 * gray[i + 1] - gray[i + width - 1] + gray[i + width + 1]
    const sy = -gray[i - width - 1] - 2 * gray[i - width] - gray[i - width + 1] + gray[i + width - 1] + 2 * gray[i + width] + gray[i + width + 1]
    sum += sx * sx + sy * sy
    pixels++
  }
  return { score: pixels >= 64 ? sum / pixels : null, pixels }
}

export function selectInstance(frame: Analysis, id: number): Analysis {
  const instance = frame.instances?.find((bird) => bird.id === id)
  if (!instance) throw new Error('选中的鸟实例不存在')
  return {
    ...frame, selectedInstance: id, body: instance.body, bodyConfidence: instance.bodyConfidence,
    eye: instance.eye, beak: instance.beak, patch: instance.patch, sharpness: instance.sharpness,
    issue: instance.eyeIssue, bodySharpness: instance.bodySharpness, bodyPixels: instance.bodyPixels,
    mask: instance.mask, posePoints: instance.posePoints,
    identityIssue: instance.matchingIssue ?? null,
    subject: instance.trackId === null ? `unmatched:${frame.id}:${id}` : `${frame.group}:track-${instance.trackId}`,
  }
}

function comparable(a: Analysis, b: Analysis, basis: QualityBasis) {
  if (a.group !== b.group || a.width !== b.width || a.height !== b.height) return false
  if (a.subject !== b.subject || !a.body || !b.body) return false
  const areaRatio = a.body[2] * a.body[3] / (b.body[2] * b.body[3])
  if (basis === 'body') return areaRatio >= .5 && areaRatio <= 2
  if (!a.eye || !b.eye || !a.beak || !b.beak) return false
  const centerDistance = Math.hypot(
    a.body[0] + a.body[2] / 2 - b.body[0] - b.body[2] / 2,
    a.body[1] + a.body[3] / 2 - b.body[1] - b.body[3] / 2,
  )
  if (areaRatio < .75 || areaRatio > 1.33 || centerDistance > Math.min(a.body[2], b.body[2]) * .35) return false
  const ax = a.beak.x - a.eye.x
  const ay = a.beak.y - a.eye.y
  const bx = b.beak.x - b.eye.x
  const by = b.beak.y - b.eye.y
  const al = Math.hypot(ax, ay)
  const bl = Math.hypot(bx, by)
  if (al < 8 || bl < 8 || al / bl < .8 || al / bl > 1.25) return false
  return (ax * bx + ay * by) / (al * bl) > .9
}

function eligible(a: Analysis, basis: QualityBasis) {
  if (basis === 'body') return a.mask && a.bodyConfidence !== null && a.bodyConfidence >= .35
    && a.bodySharpness !== null && a.bodySharpness !== undefined && Number.isFinite(a.bodySharpness) && a.bodySharpness > 0
  return !a.issue && (a.instances ? !!a.selectedInstance : a.birdCount === 1) && a.bodyConfidence !== null && a.bodyConfidence >= .35
    && a.eye !== null && a.eye.confidence >= .65 && a.beak !== null && a.beak.confidence >= .5
    && a.sharpness !== null && Number.isFinite(a.sharpness) && a.sharpness > 0
}

export function recommend(results: Analysis[], options: { useEyes: boolean } = { useEyes: true }): Analysis[] {
  const output = results.map((a): Analysis => ({
    ...a, decision: 'review', basis: null,
    reason: a.bodySharpness != null ? '主体分割与纹理已测量；同一对象的可比帧不足，不代表眼部失焦' : a.issue ?? '可比帧不足；需要人工覆核',
    relative: null, rank: null, cohortSize: 0,
  }))
  for (const basis of ['eye', 'body'] as const) {
    if (basis === 'eye' && !options.useEyes) continue
    const score = (a: Analysis) => basis === 'eye' ? a.sharpness! : a.bodySharpness!
    const remaining = output.filter((a) => a.rank === null && eligible(a, basis)).sort((a, b) => score(b) - score(a))
    while (remaining.length) {
      const anchor = remaining.shift()!
      const cohort = [anchor]
      for (let i = remaining.length - 1; i >= 0; i--) {
        if (cohort.every((member) => comparable(member, remaining[i], basis))) {
          cohort.push(remaining.splice(i, 1)[0])
        }
      }
      cohort.sort((a, b) => score(b) - score(a))
      for (let i = 0; i < cohort.length; i++) {
        const frame = cohort[i]
        frame.cohortSize = cohort.length
        if (cohort.length < 2) continue
        const relative = score(frame) / score(cohort[0])
        frame.basis = basis
        frame.relative = relative
        frame.rank = i + 1
        if (relative >= .8) {
          frame.decision = 'keep'
          frame.reason = basis === 'eye'
            ? '同一关注对象的可比帧中眼区梯度较高；优先保留，不代表绝对合焦'
            : '同一候选对象的分割主体内纹理梯度较高；优先保留候选，非眼部合焦判断，须覆核对象与姿态'
        } else if (basis === 'eye' && cohort.length >= 3 && relative < .35) {
          frame.decision = 'reject'
          frame.reason = '可信眼区梯度低于可比最佳帧的 35%；建议淘汰，仍需人工确认'
        } else {
          frame.reason = basis === 'eye' ? '眼区已测量，但组内优势不明显；两帧比较不自动淘汰'
            : '主体纹理相对较弱，可能受姿态、曝光和噪声影响；待审，不据此自动淘汰'
        }
      }
    }
  }
  return output
}

export function groupByModifiedTime(files: { name: string; lastModified: number }[]): Map<string, string> {
  const sorted = [...files].sort((a, b) => a.lastModified - b.lastModified || a.name.localeCompare(b.name, undefined, { numeric: true }))
  const groups = new Map<string, string>()
  let group = 0
  let first = 0
  let previous = 0
  for (const file of sorted) {
    if (!group || file.lastModified - previous > 2000 || file.lastModified - first > 10000) {
      group++
      first = file.lastModified
    }
    groups.set(file.name, `导入组 ${group}`)
    previous = file.lastModified
  }
  return groups
}
