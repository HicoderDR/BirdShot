// SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { test } from 'node:test'

test('nonbird old focus cannot displace the true sibling bird or become preferred', async () => {
  const tools = resolve('.tools')
  await mkdir(tools, { recursive: true })
  const directory = await mkdtemp(join(tools, 'ranking-test-'))
  try {
    const mask = { path: 'M0,0L1,0L1,1L0,1Z' }
    const bird = (id, box, score) => ({
      id, body: box, bodyConfidence: .95, mask, bodySharpness: score, bodyPixels: 20000,
      eye: null, beak: null, patch: null, sharpness: null, eyeIssue: '无眼', posePoints: [],
    })
    const frames = ['a', 'b'].map((id, i) => ({
      id, group: 'g00001', width: 2000, height: 1500, selectedInstance: 1, birdCount: 2,
      decision: 'keep', body: [100, 100, 700, 700], instances: [
        bird(1, [100, 100, 700, 700], 50000), // Controlled false background, not an actual bird photo.
        bird(2, [1000 + i * 5, 400, 250, 300], 1000 - i * 50),
      ],
    }))
    const items = frames.flatMap((f) => f.instances.map((b) => ({
      id: `${f.id}_b${b.id}`, groupId: f.group, frameId: f.id, instanceId: b.id,
      crop: `${f.id}_${b.id}.jpg`, selectedBefore: b.id === 1,
      birdVerification: { status: b.id === 1 ? 'nonBird' : 'bird', evidence: { status: 'completed' } }, species: { top5: [] },
    })))
    await writeFile(join(directory, 'rank-input.json'), JSON.stringify({
      version: 'test', status: 'completed', progress: { processed: 4, total: 4 }, oldSelected: 2,
      groups: [{ group: { id: 'g00001' }, frames }], items, model: {}, policy: {},
    }))
    execFileSync(process.execPath, ['--experimental-strip-types', 'scripts/gallery_rank.mjs', directory, directory])
    const result = JSON.parse(await readFile(join(directory, 'gallery.json'), 'utf8'))
    assert.equal(result.summary.preferred, 2)
    assert.equal(result.summary.removedOld, 2)
    assert.equal(result.summary.addedNew, 2)
    assert.ok(result.items.filter((item) => item.decision === 'keep').every((item) => item.instanceId === 2))
    assert.ok(result.items.filter((item) => item.instanceId === 1).every((item) => !item.preferredRank))
    assert.ok(result.items.filter((item) => item.decision === 'keep').every((item) => item.quality.cohortSize === 2))
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
