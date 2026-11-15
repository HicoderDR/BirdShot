// SPDX-License-Identifier: AGPL-3.0-only
import { readFile, writeFile, rename, mkdir } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { matchSubjects, recommend, selectInstance } from '../src/culling/scoring.ts'

const revision = resolve(process.argv[3])
const input = JSON.parse(await readFile(join(revision, 'rank-input.json'), 'utf8'))
const items = new Map(input.items.map((item) => [item.id, item]))
const outputItems = []
const details = []
for (const group of input.groups) {
  const frames = group.frames.map((frame) => ({
    ...frame,
    instances: frame.instances.filter((bird) => items.get(`${frame.id}_b${bird.id}`)?.birdVerification.status === 'bird'),
  }))
  const tracked = matchSubjects(frames, true)
  const candidates = tracked.flatMap((frame) => frame.instances.map((bird) => ({
    ...selectInstance(frame, bird.id), id: `${frame.id}_b${bird.id}`, originalFrameId: frame.id,
  })))
  const ranked = recommend(candidates, { useEyes: false })
  const rankedById = new Map(ranked.map((frame) => [frame.id, frame]))
  const newFrames = group.frames.map((frame) => {
    const instances = frame.instances.map((bird) => {
      const item = items.get(`${frame.id}_b${bird.id}`)
      const result = rankedById.get(item.id)
      const body = bird.body
      const edgeRisk = body[0] <= 3 || body[1] <= 3 || body[0] + body[2] >= frame.width - 3 || body[1] + body[3] >= frame.height - 3
      const pixels = bird.bodyPixels ?? 0
      const shortEdge = Math.min(body[2], body[3])
      const tier = edgeRisk ? 3 : pixels >= 100000 && shortEdge >= 256 ? 0 : pixels >= 10000 && shortEdge >= 120 ? 1 : 2
      const tierLabel = ['高可用原像素', '标准可用原像素', '低像素细节受限', '原图触边风险'][tier]
      let decision = result?.decision ?? 'review'
      let reason = result?.reason ?? (item.birdVerification.status === 'nonBird'
        ? '鸟/非鸟竞争偏向非鸟，退出优选池；保留原裁剪供覆核'
        : '验鸟证据不足或尚未处理，待审，不将闭集鸟种预测当验鸟结论')
      if (group.frames.length < 2) {
        decision = 'review'
        reason = '单张缺乏已校准绝对清晰度依据；已验鸟和测量指标，严格待审'
      } else if (result?.identityIssue || result?.subject?.startsWith('unmatched:')) {
        decision = 'review'
        reason = result.identityIssue ?? '跨帧主体无法可靠匹配，严格待审，不串鸟比较'
      } else if (edgeRisk || shortEdge < 80 || pixels < 512) {
        decision = 'review'
        reason = edgeRisk ? '鸟体框触及原图边缘，有截断风险；严格待审，不宣称完整解剖部位'
          : '主体有效原像素过少；严格待审，不以小目标纹理噪声作保留证据'
      }
      if (item.birdVerification.status !== 'bird') {
        decision = 'review'
        reason = item.birdVerification.reason
          ?? (item.birdVerification.status === 'nonBird' ? '全类别竞争偏向非鸟，退出优选池' : '验鸟证据不足，待审')
      }
      const quality = {
        tier, tierLabel, withinGroupRelative: result?.relative ?? null, withinGroupRank: result?.rank ?? null,
        cohortSize: result?.cohortSize ?? 0, bodyPixels: pixels, bodyTenengrad: bird.bodySharpness,
        edgeRisk, representative: false, shortEdgePixels: shortEdge,
        basis: '仅在已验鸟且同一候选对象的可比组内使用主体纹理；跨组按原像素层级/触边风险，不按原始Tenengrad总排序',
      }
      const updated = { ...item, decision, reason, quality, selectedBefore: !!item.selectedBefore,
        trackId: result?.instances?.find((x) => x.id === bird.id)?.trackId ?? null }
      outputItems.push(updated)
      return { ...bird, birdVerification: item.birdVerification, species: item.species, quality, decision, reason,
        relative: result?.relative ?? null, cohortSize: result?.cohortSize ?? 0 }
    })
    return { ...frame, instances, verificationVersion: input.version }
  })
  details.push({ ...group, frames: newFrames, verificationVersion: input.version })
}
const preferred = outputItems.filter((item) => item.decision === 'keep' && item.birdVerification.status === 'bird')
const byGroup = new Map()
for (const item of preferred) {
  const list = byGroup.get(item.groupId) ?? []
  list.push(item)
  byGroup.set(item.groupId, list)
}
for (const list of byGroup.values()) {
  list.sort((a, b) => a.quality.tier - b.quality.tier || (a.quality.withinGroupRank ?? 999) - (b.quality.withinGroupRank ?? 999)
    || (b.quality.withinGroupRelative ?? 0) - (a.quality.withinGroupRelative ?? 0))
  list.forEach((item, index) => { item.quality.groupPresentationRound = index; item.quality.representative = index === 0 })
}
preferred.sort((a, b) => a.quality.tier - b.quality.tier
  || a.quality.groupPresentationRound - b.quality.groupPresentationRound
  || (a.quality.withinGroupRank ?? 999) - (b.quality.withinGroupRank ?? 999)
  || (b.quality.withinGroupRelative ?? 0) - (a.quality.withinGroupRelative ?? 0))
preferred.forEach((item, index) => { item.rank = index + 1 })
preferred.forEach((item) => { item.preferredRank = item.rank })
for (const item of outputItems) if (!item.preferredRank) item.rank = null
const summary = {
  preferred: preferred.length, bird: outputItems.filter((item) => item.birdVerification.status === 'bird').length,
  nonBird: outputItems.filter((item) => item.birdVerification.status === 'nonBird').length,
  unknown: outputItems.filter((item) => item.birdVerification.status === 'unknown'
    && item.birdVerification.evidence?.status).length,
  pending: input.progress.total - input.progress.processed,
  failed: outputItems.filter((item) => item.birdVerification.evidence?.status === 'error').length,
  oldSelected: input.oldSelected, removedOld: outputItems.filter((item) => item.selectedBefore && !item.rank
    && item.birdVerification.evidence?.status === 'completed').length,
  addedNew: preferred.filter((item) => !item.selectedBefore).length,
  ranking: '先原像素可用细节层级，再按组轮次保留代表性、同对象相对名次；不跨鸟种/尺度混排原始Tenengrad，不用稀有度。',
}
const gallery = { version: 1, analysisVersion: input.version, revision: input.version,
  status: input.status === 'completed' ? 'completed' : input.status === 'error' ? 'error' : 'processing', updatedAt: new Date().toISOString(),
  progress: input.progress, summary, model: input.model, policy: input.policy,
  captureSummary: input.captureSummary, rawMetadataSummary: input.rawMetadataSummary,
  additionalLocationSummary: input.additionalLocationSummary,
  shootGroups: input.shootGroups, userRegionReference: input.userRegionReference, items: outputItems }
async function atomic(path, value) {
  const temporary = path + '.part'
  await writeFile(temporary, JSON.stringify(value))
  for (let attempt = 0; attempt < 10; attempt++) {
    try { await rename(temporary, path); return } catch (error) {
      if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt === 9) throw error
      await new Promise((resolve) => setTimeout(resolve, 50))
    }
  }
}
await mkdir(join(revision, 'groups'), { recursive: true })
for (const group of details) await atomic(join(revision, 'groups', group.group.id + '.json'), group)
await atomic(join(revision, 'gallery.json'), gallery)
console.log(JSON.stringify({ processed: input.progress.processed, total: input.progress.total, ...summary }))
