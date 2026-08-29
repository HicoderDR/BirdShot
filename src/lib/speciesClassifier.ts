import type { InferenceSession } from 'onnxruntime-web'
import type { BoundingBox } from './tracker'

export type SpeciesPrediction = {
  label: string
  score: number
}

type ModelConfig = {
  id2label: Record<string, string>
}

const INPUT_SIZE = 260
const MODEL_BASE_URL = 'https://huggingface.co/ozzyonfire/bird-species-classifier/resolve/main'
const IMAGE_MEAN = [0.485, 0.456, 0.406]
const IMAGE_STD = [0.47853944, 0.4732864, 0.47434163]

function softmax(values: Float32Array) {
  const maximum = values.reduce((current, value) => Math.max(current, value), Number.NEGATIVE_INFINITY)
  const exponentials = Array.from(values, (value) => Math.exp(value - maximum))
  const sum = exponentials.reduce((current, value) => current + value, 0)
  return exponentials.map((value) => value / sum)
}

export class BirdSpeciesClassifier {
  private session: InferenceSession | null = null
  private labels: string[] = []
  private readonly canvas = document.createElement('canvas')
  private readonly context: CanvasRenderingContext2D

  constructor() {
    this.canvas.width = INPUT_SIZE
    this.canvas.height = INPUT_SIZE
    const context = this.canvas.getContext('2d', { willReadFrequently: true })
    if (!context) throw new Error('无法创建鸟种分类预处理画布')
    this.context = context
  }

  async load() {
    if (this.session) return
    const [ort, configResponse] = await Promise.all([
      import('onnxruntime-web/wasm'),
      fetch(`${MODEL_BASE_URL}/config.json`),
    ])
    if (!configResponse.ok) throw new Error(`鸟种标签加载失败：HTTP ${configResponse.status}`)
    const config = await configResponse.json() as ModelConfig
    this.labels = Object.entries(config.id2label)
      .toSorted(([left], [right]) => Number(left) - Number(right))
      .map(([, label]) => label)
    this.session = await ort.InferenceSession.create(`${MODEL_BASE_URL}/onnx/model.onnx`, {
      executionProviders: ['wasm'],
      graphOptimizationLevel: 'all',
    })
  }

  async classify(video: HTMLVideoElement, bbox: BoundingBox): Promise<SpeciesPrediction[]> {
    if (!this.session) throw new Error('鸟种分类模型尚未加载')
    const [boxX, boxY, boxWidth, boxHeight] = bbox
    const padding = Math.max(boxWidth, boxHeight) * 0.12
    const size = Math.min(
      Math.max(boxWidth, boxHeight) + padding * 2,
      video.videoWidth,
      video.videoHeight,
    )
    const centerX = boxX + boxWidth / 2
    const centerY = boxY + boxHeight / 2
    const sourceX = Math.max(0, Math.min(video.videoWidth - size, centerX - size / 2))
    const sourceY = Math.max(0, Math.min(video.videoHeight - size, centerY - size / 2))

    this.context.drawImage(
      video,
      sourceX,
      sourceY,
      size,
      size,
      0,
      0,
      INPUT_SIZE,
      INPUT_SIZE,
    )

    const rgba = this.context.getImageData(0, 0, INPUT_SIZE, INPUT_SIZE).data
    const planeSize = INPUT_SIZE * INPUT_SIZE
    const input = new Float32Array(planeSize * 3)
    for (let pixel = 0; pixel < planeSize; pixel++) {
      for (let channel = 0; channel < 3; channel++) {
        const normalized = (rgba[pixel * 4 + channel] / 255 - IMAGE_MEAN[channel]) / IMAGE_STD[channel]
        input[channel * planeSize + pixel] = normalized
      }
    }

    const ort = await import('onnxruntime-web/wasm')
    const tensor = new ort.Tensor('float32', input, [1, 3, INPUT_SIZE, INPUT_SIZE])
    const outputs = await this.session.run({ [this.session.inputNames[0]]: tensor })
    const logits = outputs[this.session.outputNames[0]].data as Float32Array
    return softmax(logits)
      .map((score, index) => ({ label: this.labels[index] ?? `Species ${index}`, score }))
      .toSorted((a, b) => b.score - a.score)
      .slice(0, 3)
  }
}
