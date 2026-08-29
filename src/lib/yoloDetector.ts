import type { InferenceSession } from 'onnxruntime-web'
import type { BirdDetection, BoundingBox } from './tracker'

const INPUT_SIZE = 640
const BIRD_CLASS_INDEX = 14
const SCORE_THRESHOLD = 0.1
const NMS_IOU_THRESHOLD = 0.45
const MODEL_URL = 'https://raw.githubusercontent.com/nomi30701/YOLO-ByteTrack-ONNX-Web/main/public/models/yolo11n.onnx'

function intersectionOverUnion(a: BoundingBox, b: BoundingBox) {
  const left = Math.max(a[0], b[0])
  const top = Math.max(a[1], b[1])
  const right = Math.min(a[0] + a[2], b[0] + b[2])
  const bottom = Math.min(a[1] + a[3], b[1] + b[3])
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top)
  const union = a[2] * a[3] + b[2] * b[3] - intersection
  return union > 0 ? intersection / union : 0
}

function nonMaximumSuppression(detections: BirdDetection[]) {
  const sorted = detections.toSorted((a, b) => b.score - a.score)
  const selected: BirdDetection[] = []
  sorted.forEach((candidate) => {
    if (selected.every((detection) => intersectionOverUnion(candidate.bbox, detection.bbox) < NMS_IOU_THRESHOLD)) {
      selected.push(candidate)
    }
  })
  return selected.slice(0, 20)
}

export class YoloBirdDetector {
  private session: InferenceSession | null = null
  private readonly canvas = document.createElement('canvas')
  private readonly context: CanvasRenderingContext2D

  constructor() {
    this.canvas.width = INPUT_SIZE
    this.canvas.height = INPUT_SIZE
    const context = this.canvas.getContext('2d', { willReadFrequently: true })
    if (!context) throw new Error('无法创建 YOLO 图像预处理画布')
    this.context = context
  }

  async load() {
    if (this.session) return
    const ort = await import('onnxruntime-web/wasm')
    ort.env.wasm.numThreads = Math.min(4, navigator.hardwareConcurrency || 1)
    this.session = await ort.InferenceSession.create(MODEL_URL, {
      executionProviders: ['wasm'],
      graphOptimizationLevel: 'all',
    })
  }

  async detect(video: HTMLVideoElement): Promise<BirdDetection[]> {
    if (!this.session) throw new Error('YOLO 模型尚未加载')
    if (!video.videoWidth || !video.videoHeight) return []

    const ort = await import('onnxruntime-web/wasm')
    const scale = Math.min(INPUT_SIZE / video.videoWidth, INPUT_SIZE / video.videoHeight)
    const scaledWidth = video.videoWidth * scale
    const scaledHeight = video.videoHeight * scale
    const padX = (INPUT_SIZE - scaledWidth) / 2
    const padY = (INPUT_SIZE - scaledHeight) / 2

    this.context.fillStyle = '#727272'
    this.context.fillRect(0, 0, INPUT_SIZE, INPUT_SIZE)
    this.context.drawImage(video, padX, padY, scaledWidth, scaledHeight)
    const rgba = this.context.getImageData(0, 0, INPUT_SIZE, INPUT_SIZE).data
    const planeSize = INPUT_SIZE * INPUT_SIZE
    const input = new Float32Array(planeSize * 3)
    for (let pixel = 0; pixel < planeSize; pixel++) {
      input[pixel] = rgba[pixel * 4] / 255
      input[planeSize + pixel] = rgba[pixel * 4 + 1] / 255
      input[planeSize * 2 + pixel] = rgba[pixel * 4 + 2] / 255
    }

    const tensor = new ort.Tensor('float32', input, [1, 3, INPUT_SIZE, INPUT_SIZE])
    const outputs = await this.session.run({ [this.session.inputNames[0]]: tensor })
    const output = outputs[this.session.outputNames[0]]
    const dimensions = output.dims.map(Number)
    const values = output.data as Float32Array
    const channelsFirst = dimensions[1] < dimensions[2]
    const channelCount = channelsFirst ? dimensions[1] : dimensions[2]
    const detectionCount = channelsFirst ? dimensions[2] : dimensions[1]
    const valueAt = (detection: number, channel: number) => (
      channelsFirst
        ? values[channel * detectionCount + detection]
        : values[detection * channelCount + channel]
    )

    const detections: BirdDetection[] = []
    for (let index = 0; index < detectionCount; index++) {
      const score = valueAt(index, 4 + BIRD_CLASS_INDEX)
      if (score < SCORE_THRESHOLD) continue

      const centerX = valueAt(index, 0)
      const centerY = valueAt(index, 1)
      const width = valueAt(index, 2)
      const height = valueAt(index, 3)
      const x = Math.max(0, (centerX - width / 2 - padX) / scale)
      const y = Math.max(0, (centerY - height / 2 - padY) / scale)
      const mappedWidth = Math.min(video.videoWidth - x, width / scale)
      const mappedHeight = Math.min(video.videoHeight - y, height / scale)
      if (mappedWidth > 1 && mappedHeight > 1) {
        detections.push({ bbox: [x, y, mappedWidth, mappedHeight], score })
      }
    }

    return nonMaximumSuppression(detections)
  }
}
