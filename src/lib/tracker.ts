export type BoundingBox = [number, number, number, number]

export type BirdDetection = {
  bbox: BoundingBox
  score: number
}

export type BirdTrack = BirdDetection & {
  id: number
  hits: number
  missed: number
}

const MIN_IOU = 0.25
const MAX_MISSED_FRAMES = 5

function intersectionOverUnion(a: BoundingBox, b: BoundingBox) {
  const left = Math.max(a[0], b[0])
  const top = Math.max(a[1], b[1])
  const right = Math.min(a[0] + a[2], b[0] + b[2])
  const bottom = Math.min(a[1] + a[3], b[1] + b[3])
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top)
  const union = a[2] * a[3] + b[2] * b[3] - intersection
  return union > 0 ? intersection / union : 0
}

export function updateTracks(
  currentTracks: BirdTrack[],
  detections: BirdDetection[],
  nextId: number,
): { tracks: BirdTrack[]; nextId: number } {
  const unmatchedDetections = new Set(detections.map((_, index) => index))
  const matchedTracks = currentTracks.map((track) => {
    let bestIndex = -1
    let bestIou = MIN_IOU

    unmatchedDetections.forEach((index) => {
      const iou = intersectionOverUnion(track.bbox, detections[index].bbox)
      if (iou > bestIou) {
        bestIou = iou
        bestIndex = index
      }
    })

    if (bestIndex < 0) return { ...track, missed: track.missed + 1 }

    unmatchedDetections.delete(bestIndex)
    const detection = detections[bestIndex]
    return {
      ...track,
      bbox: detection.bbox,
      score: detection.score,
      hits: track.hits + 1,
      missed: 0,
    }
  })

  const newTracks = Array.from(unmatchedDetections, (index) => ({
    ...detections[index],
    id: nextId++,
    hits: 1,
    missed: 0,
  }))

  return {
    tracks: [...matchedTracks.filter((track) => track.missed <= MAX_MISSED_FRAMES), ...newTracks],
    nextId,
  }
}
