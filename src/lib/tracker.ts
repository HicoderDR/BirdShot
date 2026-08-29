export type BoundingBox = [number, number, number, number]

export type BirdDetection = {
  bbox: BoundingBox
  score: number
}

export type BirdTrack = BirdDetection & {
  id: number
  hits: number
  missed: number
  velocity: BoundingBox
}

type MatchResult = {
  tracks: BirdTrack[]
  unmatchedTracks: BirdTrack[]
  unmatchedDetections: BirdDetection[]
}

const HIGH_CONFIDENCE = 0.5
const LOW_CONFIDENCE = 0.1
const FIRST_MATCH_IOU = 0.25
const SECOND_MATCH_IOU = 0.18
const MAX_MISSED_FRAMES = 8

function intersectionOverUnion(a: BoundingBox, b: BoundingBox) {
  const left = Math.max(a[0], b[0])
  const top = Math.max(a[1], b[1])
  const right = Math.min(a[0] + a[2], b[0] + b[2])
  const bottom = Math.min(a[1] + a[3], b[1] + b[3])
  const intersection = Math.max(0, right - left) * Math.max(0, bottom - top)
  const union = a[2] * a[3] + b[2] * b[3] - intersection
  return union > 0 ? intersection / union : 0
}

function predict(track: BirdTrack): BoundingBox {
  return track.bbox.map((value, index) => value + track.velocity[index]) as BoundingBox
}

function associate(tracks: BirdTrack[], detections: BirdDetection[], minimumIou: number): MatchResult {
  const candidates: Array<{ trackIndex: number; detectionIndex: number; iou: number }> = []
  tracks.forEach((track, trackIndex) => {
    detections.forEach((detection, detectionIndex) => {
      const iou = intersectionOverUnion(predict(track), detection.bbox)
      if (iou >= minimumIou) candidates.push({ trackIndex, detectionIndex, iou })
    })
  })
  candidates.sort((a, b) => b.iou - a.iou)

  const usedTracks = new Set<number>()
  const usedDetections = new Set<number>()
  const matched: BirdTrack[] = []

  candidates.forEach(({ trackIndex, detectionIndex }) => {
    if (usedTracks.has(trackIndex) || usedDetections.has(detectionIndex)) return
    usedTracks.add(trackIndex)
    usedDetections.add(detectionIndex)
    const track = tracks[trackIndex]
    const detection = detections[detectionIndex]
    const velocity = detection.bbox.map((value, index) => (
      (value - track.bbox[index]) * 0.65 + track.velocity[index] * 0.35
    )) as BoundingBox

    matched.push({
      ...track,
      bbox: detection.bbox,
      score: detection.score,
      velocity,
      hits: track.hits + 1,
      missed: 0,
    })
  })

  return {
    tracks: matched,
    unmatchedTracks: tracks.filter((_, index) => !usedTracks.has(index)),
    unmatchedDetections: detections.filter((_, index) => !usedDetections.has(index)),
  }
}

export function updateTracks(
  currentTracks: BirdTrack[],
  detections: BirdDetection[],
  nextId: number,
): { tracks: BirdTrack[]; nextId: number } {
  const highConfidence = detections.filter((detection) => detection.score >= HIGH_CONFIDENCE)
  const lowConfidence = detections.filter((detection) => (
    detection.score >= LOW_CONFIDENCE && detection.score < HIGH_CONFIDENCE
  ))

  const firstPass = associate(currentTracks, highConfidence, FIRST_MATCH_IOU)
  const secondPass = associate(firstPass.unmatchedTracks, lowConfidence, SECOND_MATCH_IOU)
  const unmatchedTracks = secondPass.unmatchedTracks
    .map((track) => ({
      ...track,
      bbox: predict(track),
      missed: track.missed + 1,
    }))
    .filter((track) => track.missed <= MAX_MISSED_FRAMES)

  const newTracks = firstPass.unmatchedDetections.map((detection) => ({
    ...detection,
    id: nextId++,
    hits: 1,
    missed: 0,
    velocity: [0, 0, 0, 0] as BoundingBox,
  }))

  return {
    tracks: [...firstPass.tracks, ...secondPass.tracks, ...unmatchedTracks, ...newTracks],
    nextId,
  }
}
