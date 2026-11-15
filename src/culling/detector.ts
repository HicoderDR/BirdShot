// SPDX-License-Identifier: AGPL-3.0-only
import type { InferenceSession } from 'onnxruntime-web'
import { eyePatch, maskedTenengrad, reliableVisibleEye, selectInstance, tenengrad, type Analysis, type BirdInstance, type Box, type Point } from './scoring'
import { decodeMask, maskContains, prototypeMask, type BirdMask } from './masks'

const SIZE = 640
export const DETECTOR_REVISION = '2026-11-14.4'
type Detection = { box: Box; modelBox: Box; coefficients: number[]; confidence: number; points: Point[]; mask: BirdMask | null }

function iou(a: Box, b: Box) {
  const intersection = Math.max(0, Math.min(a[0] + a[2], b[0] + b[2]) - Math.max(a[0], b[0]))
    * Math.max(0, Math.min(a[1] + a[3], b[1] + b[3]) - Math.max(a[1], b[1]))
  return intersection / (a[2] * a[3] + b[2] * b[3] - intersection)
}

export class CullingDetector {
  readonly revision = DETECTOR_REVISION
  private bird: InferenceSession | null = null
  private pose: InferenceSession | null = null
  private canvas = document.createElement('canvas')

  async load() {
    if (this.bird && this.pose) return
    const ort = await import('onnxruntime-web/wasm')
    ort.env.wasm.numThreads = 1
    this.bird = await ort.InferenceSession.create('/culling/models/bird-yolo11s-seg.onnx', { executionProviders: ['wasm'] })
    this.pose = await ort.InferenceSession.create('/culling/models/eye.onnx', { executionProviders: ['wasm'] })
  }

  async dispose() {
    await this.bird?.release()
    await this.pose?.release()
    this.bird = null
    this.pose = null
  }

  private async infer(image: CanvasImageSource, region: Box, session: InferenceSession, pose: boolean, threshold = .35) {
    const ort = await import('onnxruntime-web/wasm')
    const [rx, ry, rw, rh] = region
    const scale = Math.min(SIZE / rw, SIZE / rh)
    const padX = (SIZE - rw * scale) / 2
    const padY = (SIZE - rh * scale) / 2
    this.canvas.width = SIZE
    this.canvas.height = SIZE
    const context = this.canvas.getContext('2d', { willReadFrequently: true })
    if (!context) throw new Error('无法创建模型预处理画布')
    context.fillStyle = 'rgb(114,114,114)'
    context.fillRect(0, 0, SIZE, SIZE)
    context.drawImage(image, rx, ry, rw, rh, padX, padY, rw * scale, rh * scale)
    const pixels = context.getImageData(0, 0, SIZE, SIZE).data
    const plane = SIZE * SIZE
    const input = new Float32Array(plane * 3)
    for (let p = 0; p < plane; p++) {
      for (let c = 0; c < 3; c++) input[c * plane + p] = pixels[p * 4 + c] / 255
    }
    const tensor = new ort.Tensor('float32', input, [1, 3, SIZE, SIZE])
    const outputs = await session.run({ [session.inputNames[0]]: tensor })
    tensor.dispose()
    const output = outputs[session.outputNames[0]]
    try {
      const channels = Number(output.dims[1])
      const count = Number(output.dims[2])
      if (output.dims.length !== 3 || channels !== (pose ? 23 : 116)) {
        throw new Error(`不支持的模型输出形状：${output.dims.join('×')}`)
      }
      const data = output.data
      const at = (index: number, channel: number) => Number(data[channel * count + index])
      const detections: Detection[] = []
      for (let i = 0; i < count; i++) {
        const confidence = at(i, pose ? 4 : 18)
        if (confidence < threshold) continue
        if (!pose) {
          let birdIsBest = true
          for (let c = 4; c < 84; c++) {
            if (at(i, c) > confidence) { birdIsBest = false; break }
          }
          if (!birdIsBest) continue
        }
        const x = (at(i, 0) - at(i, 2) / 2 - padX) / scale + rx
        const y = (at(i, 1) - at(i, 3) / 2 - padY) / scale + ry
        const left = Math.max(rx, x)
        const top = Math.max(ry, y)
        const right = Math.min(rx + rw, x + at(i, 2) / scale)
        const bottom = Math.min(ry + rh, y + at(i, 3) / scale)
        if (right - left < 2 || bottom - top < 2) continue
        const points: Point[] = []
        if (pose) {
          for (let k = 0; k < 6; k++) {
            points.push({
              x: (at(i, 5 + k * 3) - padX) / scale + rx,
              y: (at(i, 6 + k * 3) - padY) / scale + ry,
              confidence: at(i, 7 + k * 3),
            })
          }
        }
        const coefficients = pose ? [] : Array.from({ length: 32 }, (_, c) => at(i, 84 + c))
        detections.push({
          box: [left, top, right - left, bottom - top],
          modelBox: [at(i, 0) - at(i, 2) / 2, at(i, 1) - at(i, 3) / 2, at(i, 2), at(i, 3)],
          coefficients, confidence, points, mask: null,
        })
      }
      const selected: Detection[] = []
      for (const candidate of detections.sort((a, b) => b.confidence - a.confidence)) {
        if (selected.every((d) => iou(d.box, candidate.box) < .45)) selected.push(candidate)
      }
      if (!pose) {
        const prototype = Object.values(outputs).find((value) => value.dims.length === 4)
        if (!prototype || prototype.dims[1] !== 32 || !(prototype.data instanceof Float32Array)) {
          throw new Error('实例分割模型未输出有效的 32 通道掩码原型；不使用检测框代替分割')
        }
        for (const candidate of selected) {
          candidate.mask = prototypeMask(candidate.coefficients, prototype.data,
            Number(prototype.dims[3]), Number(prototype.dims[2]), candidate.modelBox, region, scale, padX, padY)
        }
      }
      return selected
    } finally {
      for (const value of Object.values(outputs)) value.dispose()
    }
  }

  private async detectBirds(image: HTMLImageElement, progress: (message: string) => void) {
    const width = image.naturalWidth
    const height = image.naturalHeight
    const full = await this.infer(image, [0, 0, width, height], this.bird!, false, .15)
    const scale = SIZE / Math.max(width, height)
    const needsLocal = Math.max(width, height) > 2048
      && (!full.some((bird) => bird.confidence >= .6) || full.some((bird) => Math.min(bird.box[2], bird.box[3]) * scale < 48))
    const candidates = [...full]
    let tileCount = 0
    if (needsLocal) {
      const tile = 2048
      const positions = (length: number) => {
        const size = Math.min(tile, length)
        const count = Math.ceil((length - size) / (size * .75)) + 1
        return Array.from({ length: count }, (_, i) => count === 1 ? 0 : (length - size) * i / (count - 1))
      }
      const xs = positions(width)
      const ys = positions(height)
      for (const y of ys) for (const x of xs) {
        tileCount++
        progress(`鸟体分割：原图局部 ${tileCount}/${xs.length * ys.length}`)
        candidates.push(...await this.infer(image, [x, y, Math.min(tile, width), Math.min(tile, height)], this.bird!, false, .15))
      }
      const regions: Box[] = []
      for (const candidate of candidates.toSorted((a, b) => b.confidence - a.confidence)) {
        const size = Math.min(1536, Math.max(512, Math.max(candidate.box[2], candidate.box[3]) * 3))
        const rw = Math.min(size, width)
        const rh = Math.min(size, height)
        const x = Math.max(0, Math.min(width - rw, candidate.box[0] + candidate.box[2] / 2 - rw / 2))
        const y = Math.max(0, Math.min(height - rh, candidate.box[1] + candidate.box[3] / 2 - rh / 2))
        const region: Box = [x, y, rw, rh]
        if (regions.every((other) => iou(other, region) < .5)) regions.push(region)
        if (regions.length >= 8) break
      }
      for (let i = 0; i < regions.length; i++) {
        progress(`鸟体分割：小目标精查 ${i + 1}/${regions.length}`)
        candidates.push(...await this.infer(image, regions[i], this.bird!, false))
      }
    }
    const selected: Detection[] = []
    for (const candidate of candidates.filter((bird) => bird.confidence >= .35).sort((a, b) => b.confidence - a.confidence)) {
      const region = candidate.mask!.region
      const border = 6 / candidate.mask!.scale
      const clipped = (region[0] > 0 && candidate.box[0] - region[0] < border)
        || (region[1] > 0 && candidate.box[1] - region[1] < border)
        || (region[0] + region[2] < width && region[0] + region[2] - candidate.box[0] - candidate.box[2] < border)
        || (region[1] + region[3] < height && region[1] + region[3] - candidate.box[1] - candidate.box[3] < border)
      if (!clipped && selected.every((bird) => iou(bird.box, candidate.box) < .4)) selected.push(candidate)
    }
    return { birds: selected.toSorted((a, b) => a.box[0] - b.box[0]), mode: needsLocal ? `全图 + ${tileCount} 个原图局部 + 小目标精查` : '全图 640×640' }
  }

  private async analyzeBird(image: HTMLImageElement, detection: Detection, id: number): Promise<BirdInstance> {
    const width = image.naturalWidth
    const height = image.naturalHeight
    const body = detection.box
    const instance: BirdInstance = {
      id, trackId: null, body, bodyConfidence: detection.confidence, mask: detection.mask,
      maskIssue: null, posePoints: [], poseIssue: null, eye: null, beak: null, patch: null,
      sharpness: null, eyeIssue: null, bodySharpness: null, bodyPixels: 0,
    }
    if (instance.mask?.path) {
      const x = Math.floor(body[0])
      const y = Math.floor(body[1])
      const w = Math.min(width - x, Math.ceil(body[2]) + 1)
      const h = Math.min(height - y, Math.ceil(body[3]) + 1)
      this.canvas.width = w
      this.canvas.height = h
      const context = this.canvas.getContext('2d', { willReadFrequently: true })
      if (!context) throw new Error('无法创建原像素主体画布')
      context.drawImage(image, x, y, w, h, 0, 0, w, h)
      const binary = decodeMask(instance.mask)
      const nativeMask = new Uint8Array(w * h)
      for (let py = 0; py < h; py++) for (let px = 0; px < w; px++) {
        nativeMask[py * w + px] = maskContains(instance.mask, binary, x + px + .5, y + py + .5) ? 1 : 0
      }
      const measured = maskedTenengrad(context.getImageData(0, 0, w, h).data, w, h, nativeMask)
      instance.bodySharpness = measured.score
      instance.bodyPixels = measured.pixels
      if (measured.score === null) instance.maskIssue = '分割内有效原像素不足；主体技术指标未判定'
    } else instance.maskIssue = '实例分割未输出有效鸟体掩码；不以检测框代替分割'
    const margin = Math.max(body[2], body[3]) * .1
    const x = Math.max(0, body[0] - margin)
    const y = Math.max(0, body[1] - margin)
    const region: Box = [x, y, Math.min(width, body[0] + body[2] + margin) - x, Math.min(height, body[1] + body[3] + margin) - y]
    const poses = await this.infer(image, region, this.pose!, true)
    const matching = poses.filter((pose) => iou(pose.box, body) > .3)
    if (matching.length !== 1) {
      instance.poseIssue = '姿态模型没有唯一可靠匹配；鸟体分割仍可查看'
      instance.eyeIssue = '鸟眼未可靠定位；不影响主体分割和独立技术指标'
      return instance
    }
    const points = matching[0].points
    instance.posePoints = points.map((point, index) => ({ ...point, index }))
    instance.eye = [points[1], points[2]].toSorted((a, b) => b.confidence - a.confidence)[0]
    instance.beak = points[0].confidence >= .5 ? points[0] : null
    if (Math.min(body[2], body[3]) < 90) instance.eyeIssue = '鸟体原像素过少；眼部清晰度未判定，主体分析仍有效'
    else if (instance.eye.confidence < .65) instance.eyeIssue = '鸟眼定位置信度不足；未判定，不等于失焦'
    else if (!reliableVisibleEye([points[1], points[2]])) instance.eyeIssue = '左右眼可见性不一致；可能背向或误定位，眼部未判定'
    else if (instance.eye.x < body[0] || instance.eye.y < body[1] || instance.eye.x > body[0] + body[2] || instance.eye.y > body[1] + body[3]) {
      instance.eyeIssue = '鸟眼关键点落在鸟体外；眼部未判定'
    }
    if (instance.eyeIssue) return instance
    const patch = eyePatch(instance.eye, instance.beak, body, width, height)
    if (!patch) { instance.eyeIssue = '眼区无法完整测量；眼部未判定'; return instance }
    instance.patch = patch
    this.canvas.width = patch[2]
    this.canvas.height = patch[3]
    const context = this.canvas.getContext('2d', { willReadFrequently: true })
    if (!context) throw new Error('无法创建原像素眼区画布')
    context.drawImage(image, ...patch, 0, 0, patch[2], patch[3])
    instance.sharpness = tenengrad(context.getImageData(0, 0, patch[2], patch[3]).data, patch[2], patch[3])
    if (!instance.beak) instance.eyeIssue = '眼区已测量，但喙点不足以比较头部方向'
    return instance
  }

  async analyze(image: HTMLImageElement, id: string, group: string, progress: (message: string) => void = () => {}): Promise<Analysis> {
    if (!this.bird || !this.pose) throw new Error('请先加载本地模型')
    const width = image.naturalWidth
    const height = image.naturalHeight
    const result: Analysis = {
      id, group, width, height, body: null, bodyConfidence: null, birdCount: 0,
      eye: null, beak: null, patch: null, sharpness: null, issue: null,
      decision: 'review', reason: '尚未比较', relative: null, rank: null, cohortSize: 0,
      instances: [], selectedInstance: null, bodySharpness: null, bodyPixels: 0, basis: null,
    }
    const { birds, mode } = await this.detectBirds(image, progress)
    result.detectionMode = mode
    result.birdCount = birds.length
    if (!birds.length) return { ...result, issue: '全图及局部推理仍未可靠检出鸟体', reason: '鸟体未可靠检出；请人工覆核，不能当成失焦' }
    for (let i = 0; i < birds.length; i++) {
      progress(`鸟 #${i + 1}/${birds.length}：原像素主体与六点姿态`)
      result.instances!.push(await this.analyzeBird(image, birds[i], i + 1))
    }
    const largest = result.instances!.toSorted((a, b) => b.body[2] * b.body[3] - a.body[2] * a.body[3])[0]
    return selectInstance(result, largest.id)
  }
}
