// SPDX-License-Identifier: AGPL-3.0-only
import { createInterface } from 'node:readline'
import { matchSubjects, recommend, maskedTenengrad } from '../src/culling/scoring.ts'

for await (const line of createInterface({ input: process.stdin })) {
  try {
    const request = JSON.parse(line)
    if (request.operation === 'metrics') {
      const result = maskedTenengrad(request.rgba, request.width, request.height, new Uint8Array(request.mask))
      console.log(JSON.stringify({ ok: true, result }))
      continue
    }
    const matched = matchSubjects(request.frames, true)
    const safe = matched.map((frame) => frame.identityIssue || (frame.bodyConfidence ?? 0) < .5
      ? { ...frame, bodySharpness: frame.bodySharpness, subject: `uncertain:${frame.id}`, strictIssue: frame.identityIssue ?? '检测置信度不足以严格确认保留' }
      : frame)
    const results = recommend(safe, { useEyes: false }).map((frame) => {
      if (request.frames.length === 1) {
        return { ...frame, decision: 'review', relative: null, rank: null,
          reason: '单张已计算独立技术指标；没有经校准的绝对合焦阈值，严格策略证据不足，待审且不拷贝' }
      }
      if (frame.strictIssue) return { ...frame, decision: 'review', reason: frame.strictIssue }
      return frame
    })
    console.log(JSON.stringify({ ok: true, results }))
  } catch (error) {
    console.log(JSON.stringify({ ok: false, error: error instanceof Error ? error.message : String(error) }))
  }
}
