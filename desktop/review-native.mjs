// SPDX-License-Identifier: AGPL-3.0-only
import { lstat, realpath, open } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'

const samePath = (left, right) => path.resolve(left).toLowerCase() === path.resolve(right).toLowerCase()
export function reviewRequest(action, value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('审阅参数无效')
  if (action === 'state') {
    if (Object.keys(value).length) throw new Error('审阅状态不接受额外参数')
    return {}
  }
  if (['cleanupExecute', 'cleanupCancel'].includes(action)) {
    if (Object.keys(value).some(key => key !== 'token') || typeof value.token !== 'string' ||
        !/^[A-Za-z0-9_-]{16,128}$/.test(value.token)) throw new Error('清理预览标识无效')
    return { token: value.token }
  }
  if (!Number.isSafeInteger(value.expectedRevision) || value.expectedRevision < 0) throw new Error('审阅版本无效，请刷新')
  if (action === 'preferences') {
    if (Object.keys(value).some(key => !['expectedRevision', 'exitAction'].includes(key)) ||
        !['ask', 'exit', 'cleanup'].includes(value.exitAction)) throw new Error('退出偏好无效')
    return { expectedRevision: value.expectedRevision, exitAction: value.exitAction }
  }
  if (action === 'cleanupPreview') {
    if (Object.keys(value).some(key => !['expectedRevision', 'includeRaw', 'includeZero'].includes(key)) ||
        [value.includeRaw, value.includeZero].some(flag => flag !== undefined && typeof flag !== 'boolean')) throw new Error('清理范围无效')
    return { expectedRevision: value.expectedRevision, includeRaw: value.includeRaw === true, includeZero: value.includeZero === true }
  }
  if (action !== 'decide' || Object.keys(value).some(key => !['expectedRevision', 'groupId', 'changes', 'action'].includes(key)) ||
      !/^g(?:\d{5}|[a-f0-9]{24})$/.test(value.groupId || '') ||
      !['set', 'restoreAutomatic', 'undo'].includes(value.action || 'set')) throw new Error('审阅操作无效')
  const changes = value.changes ?? []
  if (!Array.isArray(changes) || changes.length > 5000 ||
      changes.some(row => !row || Object.keys(row).some(key => !['frameId', 'selected'].includes(key)) ||
        !/^p(?:\d{6}|[a-f0-9]{24})$/.test(row.frameId || '') || typeof row.selected !== 'boolean') ||
      new Set(changes.map(row => row.frameId)).size !== changes.length) throw new Error('照片选择集合无效')
  return { expectedRevision: value.expectedRevision, groupId: value.groupId, changes, action: value.action || 'set' }
}

export async function validateTrashFile(record) {
  if (!record || typeof record.path !== 'string' || typeof record.root !== 'string' ||
      !/^[a-f0-9]{64}$/.test(record.sha256 || '') || !['primary', 'raw'].includes(record.kind)) throw new Error('未登记的回收文件')
  const filename = path.resolve(record.path), root = path.resolve(record.root)
  const relative = path.relative(root, filename)
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative) ||
      !(record.kind === 'primary' ? /\.(?:jpe?g|png)$/i : /\.(?:arw|dng|nef|cr2|cr3|orf|raf|rw2)$/i).test(filename)) throw new Error('回收文件越界或类型无效')
  const rootReal = await realpath(root), actual = await realpath(filename)
  if (!samePath(rootReal, root) || !samePath(actual, filename)) throw new Error('回收路径含链接或已改变')
  for (let cursor = filename; ; cursor = path.dirname(cursor)) {
    const info = await lstat(cursor, { bigint: true })
    if (info.isSymbolicLink()) throw new Error('回收路径含符号链接')
    if (samePath(cursor, root)) break
    if (cursor === path.dirname(cursor)) throw new Error('回收路径未位于登记根目录')
  }
  const before = await lstat(filename, { bigint: true })
  if (!before.isFile() || before.size !== BigInt(record.size) || before.mtimeNs !== BigInt(record.mtimeNs)) throw new Error('原文件在预览后已变化，未回收')
  const stream = await open(filename, 'r')
  try {
    const opened = await stream.stat({ bigint: true })
    if (opened.dev !== before.dev || opened.ino !== before.ino ||
        opened.size !== before.size || opened.mtimeNs !== before.mtimeNs) throw new Error('打开原文件时身份已变化，未回收')
    const digest = createHash('sha256')
    for await (const block of stream.createReadStream({ autoClose: false })) digest.update(block)
    if (digest.digest('hex') !== record.sha256) throw new Error('原文件内容在预览后已变化，未回收')
    const after = await stream.stat({ bigint: true })
    const current = await lstat(filename, { bigint: true })
    if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size ||
        after.mtimeNs !== before.mtimeNs || current.dev !== before.dev || current.ino !== before.ino ||
        current.size !== before.size || current.mtimeNs !== before.mtimeNs || current.isSymbolicLink() ||
        !samePath(await realpath(filename), filename)) throw new Error('原文件仍在写入或路径已变化，未回收')
  } finally { await stream.close() }
  return filename
}

export function createReviewNative({ command, confirm, trashItem, progress, busy }) {
  const plans = new Map()
  let running = null
  const actions = { state: 'review_state', decide: 'review_decide', preferences: 'review_preferences',
    cleanupPreview: 'review_cleanup_preview', cleanupCancel: 'review_cleanup_cancel' }
  const invoke = async (action, input) => {
    const params = reviewRequest(action, input)
    if (action === 'cleanupCancel') {
      if (running?.token === params.token) running.cancelled = true
      plans.delete(params.token)
      return command(actions[action], params)
    }
    if (action !== 'cleanupExecute') {
      if (running && ['decide', 'preferences', 'cleanupPreview'].includes(action)) throw new Error('正在回收，请先完成或取消')
      const result = await command(actions[action], params)
      if (action === 'cleanupPreview') {
        plans.clear()
        plans.set(result.token, { ...result, includeRaw: params.includeRaw, includeZero: params.includeZero })
      }
      return result
    }
    if (running) throw new Error('已有回收任务在执行')
    const plan = plans.get(params.token)
    if (!plan || !Array.isArray(plan.files) || plan.files.length > 20000) throw new Error('请重新预览此次清理范围')
    if (busy()) throw new Error('正在处理照片，请待任务安全完成后再清理')
    if (!plan.files.length) return { results: [], cancelled: false, snapshot: await command('review_state') }
    if (!await confirm(plan)) return { results: [], cancelled: true, snapshot: await command('review_state') }
    const execution = { token: params.token, cancelled: false }
    running = execution
    const results = []
    try {
      for (const file of plan.files) {
        if (execution.cancelled) break
        let success = false, error
        try {
          if (busy()) throw new Error('处理任务已开始，未回收该文件')
          const claim = await command('review_cleanup_claim', { token: params.token, fileId: file.id })
          if (claim.id !== file.id || claim.frameId !== file.frameId || claim.kind !== file.kind ||
              claim.kind === 'raw' && !plan.includeRaw) throw new Error('文件授权与预览不一致')
          const filename = await validateTrashFile(claim)
          if (execution.cancelled) break
          await trashItem(filename)
          success = true
        } catch (failure) { error = failure.message }
        // Never repeat an uncertain OS move because recording the result failed.
        await command('review_cleanup_result', { token: params.token, fileId: file.id, success, ...(error ? { error } : {}) })
        results.push({ id: file.id, success, ...(error ? { error } : {}) })
        progress({ token: params.token, completed: results.length, total: plan.files.length, result: results.at(-1) })
      }
      if (execution.cancelled) await command('review_cleanup_cancel', params)
      return { results, cancelled: execution.cancelled, snapshot: await command('review_state') }
    } finally { plans.delete(params.token); running = null }
  }
  invoke.isRunning = () => running !== null
  return invoke
}
