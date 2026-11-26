// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, stat, readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import os from 'node:os'
import { createReviewNative, reviewRequest, validateTrashFile } from '../desktop/review-native.mjs'

const token = 'a'.repeat(32)
async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'BirdShot-review-native-'))
  const filename = path.join(root, 'public-test.jpg')
  await writeFile(filename, Buffer.from('Controlled trash fixture; not a user photo'))
  const info = await stat(filename, { bigint: true })
  return { id: 'test1', frameId: 'p000001', kind: 'primary', path: filename, root,
    size: String(info.size), mtimeNs: String(info.mtimeNs),
    sha256: createHash('sha256').update(await readFile(filename)).digest('hex') }
}
test('review requests reject arbitrary path and malformed decisions', () => {
  assert.throws(() => reviewRequest('cleanupExecute', { token, path: 'C:\\private.jpg' }))
  assert.throws(() => reviewRequest('decide', { expectedRevision: 1, groupId: 'g00001', changes: [{ frameId: '../file', selected: true }] }))
  assert.throws(() => reviewRequest('decide', { expectedRevision: 1, groupId: 'g00001', changes: [
    { frameId: 'p000001', selected: true }, { frameId: 'p000001', selected: false }] }))
  assert.deepEqual(reviewRequest('cleanupPreview', { expectedRevision: 1 }), { expectedRevision: 1, includeRaw: false, includeZero: false })
})
test('exact source validation refuses changed bytes, wrong extension and escape', async () => {
  const row = await fixture()
  assert.equal(await validateTrashFile(row), row.path)
  await writeFile(row.path, 'Changed after preview')
  await assert.rejects(validateTrashFile(row))
  await assert.rejects(validateTrashFile({ ...row, kind: 'raw' }))
  await assert.rejects(validateTrashFile({ ...row, root: path.join(row.root, 'nested') }))
})
test('native cleanup requires one prepared plan and confirmation, never permanent fallback', async () => {
  const row = await fixture(), calls = [], reports = []
  const plan = { token, photoCount: 1, rawCount: 0, keepCount: 1,
    files: [{ id: row.id, frameId: row.frameId, kind: row.kind, name: 'public-test.jpg' }] }
  let confirmed = false, moves = 0
  const command = async (action, args) => {
    calls.push(action)
    if (action === 'review_cleanup_preview') return plan
    if (action === 'review_cleanup_claim') return row
    if (action === 'review_cleanup_result') { reports.push(args); return {} }
    if (action === 'review_state') return { version: 1, revision: 2 }
    return {}
  }
  const review = createReviewNative({ command, confirm: async () => confirmed,
    trashItem: async () => { moves++; throw new Error('System recycle bin unavailable') },
    progress: () => {}, busy: () => false })
  await review('cleanupPreview', { expectedRevision: 1 })
  const cancelled = await review('cleanupExecute', { token })
  assert.equal(cancelled.cancelled, true)
  assert.equal(moves, 0)
  confirmed = true
  const result = await review('cleanupExecute', { token })
  assert.equal(moves, 1)
  assert.equal(result.results[0].success, false)
  assert.equal(reports[0].success, false)
  assert.match(reports[0].error, /recycle bin/)
  assert.ok((await stat(row.path)).isFile(), 'No fs.unlink/rmtree fallback')
  await assert.rejects(review('cleanupExecute', { token }))
})
test('a prepared plan does not authorize RAW or a changed claim', async () => {
  const row = await fixture()
  let moves = 0
  const review = createReviewNative({ command: async action => {
    if (action === 'review_cleanup_preview') return { token, files: [{ id: row.id, frameId: row.frameId, kind: 'raw' }] }
    if (action === 'review_cleanup_claim') return { ...row, kind: 'raw' }
    return {}
  }, confirm: async () => true, trashItem: async () => { moves++ }, progress: () => {}, busy: () => false })
  await review('cleanupPreview', { expectedRevision: 1 })
  const result = await review('cleanupExecute', { token })
  assert.equal(moves, 0)
  assert.equal(result.results[0].success, false)
})
