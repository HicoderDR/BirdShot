// SPDX-License-Identifier: AGPL-3.0-only
import type { Box } from './scoring'

export type BirdMask = {
  width: number
  height: number
  runs: number[]
  region: Box
  scale: number
  padX: number
  padY: number
  path: string
}

export function encodeMask(pixels: Uint8Array): number[] {
  const runs: number[] = []
  let value = 0
  let length = 0
  for (const pixel of pixels) {
    if (pixel === value) length++
    else { runs.push(length); length = 1; value = pixel }
  }
  runs.push(length)
  return runs
}

export function decodeMask(mask: Pick<BirdMask, 'width' | 'height' | 'runs'>): Uint8Array {
  const pixels = new Uint8Array(mask.width * mask.height)
  let offset = 0
  let value = 0
  for (const length of mask.runs) {
    if (!Number.isInteger(length) || length < 0 || offset + length > pixels.length) throw new Error('分割掩码编码无效')
    pixels.fill(value, offset, offset + length)
    offset += length
    value = 1 - value
  }
  if (offset !== pixels.length) throw new Error('分割掩码数据不完整')
  return pixels
}

export function maskContains(mask: BirdMask, pixels: Uint8Array, x: number, y: number) {
  const px = Math.floor(((x - mask.region[0]) * mask.scale + mask.padX) * mask.width / 640)
  const py = Math.floor(((y - mask.region[1]) * mask.scale + mask.padY) * mask.height / 640)
  return px >= 0 && py >= 0 && px < mask.width && py < mask.height && pixels[py * mask.width + px] === 1
}

function contours(pixels: Uint8Array, width: number, height: number, map: (x: number, y: number) => [number, number]) {
  const edges = new Map<string, [number, number][]>()
  const add = (x: number, y: number, tx: number, ty: number) => {
    const key = `${x},${y}`
    const list = edges.get(key) ?? []
    list.push([tx, ty])
    edges.set(key, list)
  }
  const filled = (x: number, y: number) => x >= 0 && y >= 0 && x < width && y < height && !!pixels[y * width + x]
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (!filled(x, y)) continue
    if (!filled(x, y - 1)) add(x, y, x + 1, y)
    if (!filled(x + 1, y)) add(x + 1, y, x + 1, y + 1)
    if (!filled(x, y + 1)) add(x + 1, y + 1, x, y + 1)
    if (!filled(x - 1, y)) add(x, y + 1, x, y)
  }
  let path = ''
  while (edges.size) {
    const start = edges.keys().next().value!
    let key = start
    const points: [number, number][] = []
    do {
      const [x, y] = key.split(',').map(Number)
      points.push([x, y])
      const next = edges.get(key)
      if (!next?.length) throw new Error('分割轮廓不闭合')
      const point = next.pop()!
      if (!next.length) edges.delete(key)
      key = `${point[0]},${point[1]}`
    } while (key !== start)
    const simplified = points.filter((point, i) => {
      const before = points[(i + points.length - 1) % points.length]
      const after = points[(i + 1) % points.length]
      return !((before[0] === point[0] && after[0] === point[0]) || (before[1] === point[1] && after[1] === point[1]))
    })
    path += simplified.map((point, i) => {
      const [x, y] = map(...point)
      return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    }).join('') + 'Z'
  }
  return path
}

export function prototypeMask(
  coefficients: number[], prototypes: ArrayLike<number>, width: number, height: number,
  modelBox: Box, region: Box, scale: number, padX: number, padY: number,
): BirdMask {
  if (prototypes.length !== coefficients.length * width * height) throw new Error('分割原型输出形状不匹配')
  const pixels = new Uint8Array(width * height)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const mx = (x + .5) * 640 / width
    const my = (y + .5) * 640 / height
    if (mx < modelBox[0] || my < modelBox[1] || mx > modelBox[0] + modelBox[2] || my > modelBox[1] + modelBox[3]
      || mx < padX || my < padY || mx > padX + region[2] * scale || my > padY + region[3] * scale) continue
    let logit = 0
    for (let c = 0; c < coefficients.length; c++) logit += coefficients[c] * Number(prototypes[c * width * height + y * width + x])
    pixels[y * width + x] = logit > 0 ? 1 : 0
  }
  const path = contours(pixels, width, height, (x, y) => [
    Math.max(region[0], Math.min(region[0] + region[2], (x * 640 / width - padX) / scale + region[0])),
    Math.max(region[1], Math.min(region[1] + region[3], (y * 640 / height - padY) / scale + region[1])),
  ])
  return { width, height, runs: encodeMask(pixels), region, scale, padX, padY, path }
}
