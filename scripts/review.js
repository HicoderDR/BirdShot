// SPDX-License-Identifier: AGPL-3.0-only
const NS = 'http://www.w3.org/2000/svg'
const LEVELS = [480, 960, 1920, 3840]
const finite = value => typeof value === 'number' && Number.isFinite(value)
const ISO_CAPTURE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/i
export const validFrameId = value => /^p(?:\d{6}|[a-f0-9]{24})$/.test(value || '')
export const validGroupId = value => /^g(?:\d{5}|[a-f0-9]{24})$/.test(value || '')
export const mediaURL = path => typeof path === 'string' && path && !path.split(/[\\/]/).some(part => part === '..') ?
  '/media/' + path.split(/[\\/]/).map(encodeURIComponent).join('/') : null
export const previewURL = (id, level = 480) => validFrameId(id) && LEVELS.includes(level) ? `/preview/${id}/${level}.jpg` : null
const probability = value => finite(value) && value >= 0 && value <= 1
export const probabilityLabel = value => probability(value) ? (value * 100).toFixed(1) + '%' : '概率未提供'
export function groupLabel(group, ordinal) {
  if (typeof group.name === 'string' && group.name.trim() && !ISO_CAPTURE.test(group.name.trim())) return group.name
  const source = group.sourceLabel || group.frames?.[0]?.sourceLabel
  const filename = typeof source === 'string' ? source.split(/[\\/]/).filter(Boolean).at(-1) : null
  if (filename) return filename.length > 48 ? filename.slice(0, 32) + '…' + filename.slice(-12) : filename
  return `连拍 ${ordinal ?? group.id}`
}
export function formatCaptureTime(value, seconds = false) {
  if (typeof value !== 'string') return ''
  const match = ISO_CAPTURE.exec(value)
  if (!match) return value
  if (!match[7]) {
    // No offset means camera wall time, not UTC or the browser's timezone.
    return `${match[1]}/${match[2]}/${match[3]} ${match[4]}:${match[5]}${seconds && match[6] ? ':' + match[6] : ''}`
  }
  const instant = new Date(value)
  if (!Number.isFinite(instant.getTime())) return value
  const parts = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}), hourCycle: 'h23' }).formatToParts(instant)
  const fields = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${fields.year}/${fields.month}/${fields.day} ${fields.hour}:${fields.minute}${seconds ? ':' + fields.second : ''}`
}
export function topThree(species) {
  const values = species?.top3 || species?.top5 || []
  return values.filter(value => value && value.isBird !== false)
    .slice().sort((a, b) => (probability(b.probability) ? b.probability : -1) - (probability(a.probability) ? a.probability : -1)).slice(0, 3)
}
export function validateSnapshot(snapshot) {
  if (snapshot?.version !== 1 || !Number.isInteger(snapshot.revision) || !Array.isArray(snapshot.groups)) throw new Error('审阅状态格式变化，请刷新')
  const ids = new Set(), frames = new Set()
  for (const group of snapshot.groups) {
    if (!validGroupId(group.id) || ids.has(group.id) || !Array.isArray(group.frames)) throw new Error('连拍组格式无效')
    ids.add(group.id)
    for (const frame of group.frames) {
      if (!validFrameId(frame.id) || frames.has(frame.id) || typeof frame.selected !== 'boolean') throw new Error('原始帧格式无效')
      frames.add(frame.id)
    }
  }
  return snapshot
}
export function frameInstances(groupId, frame, items = []) {
  return (frame.instances || []).filter(instance => instance && typeof instance.crop === 'string' && instance.crop).map(instance => {
    const item = items.find(item => item.groupId === groupId && item.frameId === frame.id &&
      String(item.instanceId) === String(instance.id) && item.crop === instance.crop)
    return { ...item, ...instance, frameId: frame.id, groupId,
      birdVerification: instance.birdVerification || item?.birdVerification,
      species: instance.species || item?.species, quality: instance.quality || item?.quality,
      decision: instance.decision || item?.decision }
  })
}
export function filteredGroups(snapshot, gallery, { mode = 'all', directory = '', search = '', status = 'all' } = {}) {
  const query = search.trim().toLocaleLowerCase()
  return snapshot.groups.flatMap(group => {
    const ids = new Set(group.frames.map(frame => frame.id))
    const items = (gallery.items || []).filter(item => item.groupId === group.id && ids.has(item.frameId))
    const directoryFrames = new Set(items.filter(item => mode === 'all' || !directory || item.navigationGroups?.[mode] === directory).map(item => item.frameId))
    for (const frame of gallery.frames || []) {
      if (ids.has(frame.id) && (mode === 'all' || !directory || frame.navigationGroups?.[mode] === directory)) directoryFrames.add(frame.id)
    }
    if (mode !== 'all' && directory && !directoryFrames.size) return []
    const preferred = group.frames.some(frame => frame.selected)
    if ((status === 'preferred' && !preferred) || (status === 'pending' && group.manualReviewed && !group.newFramesPending) ||
      (status === 'reviewed' && !group.manualReviewed)) return []
    const words = [group.name, group.sourceLabel, ...group.frames.map(frame => frame.sourceLabel),
      ...items.flatMap(item => (item.species?.top3 || item.species?.top5 || []).flatMap(candidate => [candidate.commonName, candidate.scientificName]))]
    if (query && !words.some(value => typeof value === 'string' && value.toLocaleLowerCase().includes(query))) return []
    return [{ group, matchedFrames: mode === 'all' || !directory ? group.frames.length : directoryFrames.size }]
  })
}
export function geometry(frame, width, height, view = { scale: 1, cx: .5, cy: .5 }) {
  const sourceWidth = finite(frame.width) && frame.width > 0 ? frame.width : 1
  const sourceHeight = finite(frame.height) && frame.height > 0 ? frame.height : 1
  const fit = Math.min(width / sourceWidth, height / sourceHeight)
  const factor = fit * view.scale
  return { fit, factor, width: sourceWidth, height: sourceHeight,
    x: width / 2 - view.cx * sourceWidth * factor, y: height / 2 - view.cy * sourceHeight * factor }
}
export function zoomAt(view, frame, width, height, x, y, scale) {
  const before = geometry(frame, width, height, view)
  const sourceX = (x - before.x) / before.factor, sourceY = (y - before.y) / before.factor
  const next = { ...view, scale: Math.max(1, Math.min(32, scale)) }
  const after = geometry(frame, width, height, next)
  next.cx = (sourceX - (x - width / 2) / after.factor) / after.width
  next.cy = (sourceY - (y - height / 2) / after.factor) / after.height
  return next
}
export function nextResolution(level, frame, needed) {
  const longest = Math.max(frame.width || 1, frame.height || 1)
  if (level === 'original' || needed <= Number(level) * 1.08 || Number(level) >= longest) return null
  const index = LEVELS.indexOf(level)
  return index >= 0 && index < LEVELS.length - 1 ? LEVELS[index + 1] : needed > 3840 * 1.08 ? 'original' : null
}
export function shortcut(event) {
  const target = event.target
  if (event.repeat || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey ||
    target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),dialog')) return null
  return ({ q: 'previousGroup', e: 'nextGroup', a: 'previousFrame', d: 'nextFrame', ' ': 'toggle', w: 'keep', s: 'reject', Escape: 'exit' })[event.key?.length === 1 ? event.key.toLowerCase() : event.key] || null
}
export function canvasShortcut(event) {
  if (event.isComposing || event.keyCode === 229 || event.metaKey || event.altKey ||
      event.target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),dialog')) return null
  if (event.ctrlKey) {
    if (['+', '='].includes(event.key) || ['Equal', 'NumpadAdd'].includes(event.code)) return 'zoomIn'
    if (event.key === '-' || ['Minus', 'NumpadSubtract'].includes(event.code)) return 'zoomOut'
    return null
  }
  return ({ ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' })[event.key] || null
}
export function panView(view, frame, width, height, dx, dy) {
  const bounds = geometry(frame, width, height, view)
  const limit = (center, viewport, image) => image <= viewport ? .5 :
    Math.min(1 - viewport / (2 * image), Math.max(viewport / (2 * image), center))
  return { ...view, cx: limit(view.cx + dx / (bounds.width * bounds.factor), width, bounds.width * bounds.factor),
    cy: limit(view.cy + dy / (bounds.height * bounds.factor), height, bounds.height * bounds.factor) }
}
export function technicalSentence(instance) {
  if (!instance) return '此帧没有已确认鸟体；保留仅改变照片遴选状态，不代表鸟种确认。'
  const verification = instance.birdVerification?.status
  if (verification !== 'bird') return '验鸟结果未知 / 待确认，不能当作已确认鸟体。'
  const quality = instance.quality
  const evidence = []
  if (quality?.tierLabel) evidence.push(quality.tierLabel)
  if (finite(quality?.withinGroupRank)) evidence.push(`同对象第 ${quality.withinGroupRank} 名`)
  if (finite(quality?.bodyPixels)) evidence.push(`主体 ${Math.round(quality.bodyPixels).toLocaleString()} 像素`)
  if (quality?.edgeRisk === true) evidence.push('有触边风险')
  if (finite(quality?.bodyTenengrad)) evidence.push('主体纹理指标可比较，不是眼睛合焦判断')
  const decision = instance.decision === 'keep' ? '自动优选' : instance.decision === 'reject' ? '自动未选' : '自动待审'
  return `Birder 已验鸟，${decision}；${evidence.length ? evidence.join('，') : '暂无可比较技术质量证据'}。`
}
export function createNativeBridge(win, { capability = () => {}, progress = () => {} } = {}) {
  let writable = false, validated = true, sequence = 0, disposed = false
  const pending = new Map()
  const embedded = win.parent !== win
  const validParent = event => embedded && event.source === win.parent && (event.origin === 'null' || event.origin === 'file://')
  function receive(event) {
    if (!validParent(event)) return
    const message = event.data
    if (!message || typeof message !== 'object') return
    if (message.type === 'birdshot-review-capabilities') {
      writable = message.writable === true
      validated = message.validated !== false
      capability(writable)
    } else if (message.type === 'birdshot-review-validation') {
      validated = message.validated === true; capability(writable)
    } else if (message.type === 'birdshot-review-response' && typeof message.id === 'string' && pending.has(message.id)) {
      const request = pending.get(message.id)
      pending.delete(message.id); win.clearTimeout(request.timer)
      if (message.ok === true) request.resolve(message.result)
      else request.reject(new Error(typeof message.error === 'string' ? message.error : message.error?.message || '保存失败，请刷新'))
    } else if (message.type === 'birdshot-review-trash-progress') progress(message)
  }
  win.addEventListener('message', receive)
  const ready = () => { if (embedded && !disposed) win.parent.postMessage({ type: 'birdshot-review-ready' }, '*') }
  if (win.document?.readyState === 'complete') ready()
  else win.addEventListener('load', ready, { once: true })
  return {
    get writable() { return writable },
    get validated() { return validated },
    request(action, params = {}) {
      if (!writable || !embedded || disposed) return Promise.reject(new Error('浏览器只读；请在桌面中保存'))
      if (!validated && action !== 'cleanupCancel') return Promise.reject(new Error('缓存正在校验，尚不能保存或清理；内存遴选不会丢失'))
      const id = `review-${Date.now()}-${++sequence}`
      return new Promise((resolve, reject) => {
        const timer = win.setTimeout(() => { pending.delete(id); reject(new Error('桌面响应超时；请刷新读取真实保存状态')) }, 120000)
        pending.set(id, { resolve, reject, timer })
        win.parent.postMessage({ type: 'birdshot-review-request', id, action, params }, '*')
      })
    },
    mode(immersive) { if (embedded && !disposed) win.parent.postMessage({ type: 'birdshot-review-mode', immersive: Boolean(immersive) }, '*') },
    closeResponse(id, allow) { if (embedded && !disposed) win.parent.postMessage({ type: 'birdshot-review-close-response', id, allow }, '*') },
    dispose() {
      disposed = true; win.removeEventListener('message', receive); win.removeEventListener('load', ready)
      for (const request of pending.values()) { win.clearTimeout(request.timer); request.reject(new Error('审阅页面已关闭')) }
      pending.clear()
    }
  }
}
export class ReviewDraft {
  constructor() { this.pending = new Map(); this.touched = new Map(); this.history = []; this.sequence = 0 }
  get dirty() { return this.touched.size > 0 }
  get conflicts() {
    return [...this.pending.values()].filter(entry => entry.conflict).length +
      [...this.touched.values()].filter(entry => entry.conflict).length
  }
  accept(snapshot) {
    validateSnapshot(snapshot)
    if (this.base && snapshot.revision < this.base.revision) return this.working
    this.base = structuredClone(snapshot); this.working = structuredClone(snapshot)
    this.byId = new Map(); this.byIdentity = new Map()
    for (const group of this.working.groups) for (const frame of group.frames) {
      const identity = frame.identity || frame.id
      const row = { group, frame, identity }
      this.byId.set(frame.id, row); this.byIdentity.set(identity, row)
    }
    for (const [identity, entry] of this.pending) {
      const row = this.byIdentity.get(identity)
      entry.conflict = !row || row.group.id !== entry.groupId || row.group.revision !== entry.groupRevision ||
        row.frame.selected !== entry.baseSelected || Boolean(row.frame.manual) !== entry.baseManual
      if (row) { row.frame.selected = entry.selected; row.frame.manual = entry.manual; entry.frameId = row.frame.id }
    }
    for (const [id, entry] of this.touched) {
      const group = this.working.groups.find(group => group.id === id)
      entry.conflict = !group || group.revision !== entry.groupRevision
      if (group) {
        group.manualReviewed = true
        group.newFramesPending = group.frames.filter(frame => !entry.members.includes(frame.identity || frame.id)).length
      }
    }
    for (const group of this.working.groups) group.selectedCount = group.frames.filter(frame => frame.selected).length
    return this.working
  }
  edit(groupId, changes, restore = false, record = true) {
    const group = this.working?.groups.find(group => group.id === groupId)
    if (!group) return false
    const before = group.frames.map(frame => ({ frameId: frame.id, selected: frame.selected, manual: Boolean(frame.manual) }))
    let changed = false
    for (const change of changes) {
      const row = this.byId.get(change.frameId)
      if (!row || row.group.id !== groupId || typeof change.selected !== 'boolean') throw new Error('未登记的遴选照片')
      const old = this.pending.get(row.identity), manual = restore ? false : change.manual ?? true
      if (row.frame.selected === change.selected && (!(restore || change.manual !== undefined) ||
          Boolean(row.frame.manual) === manual) && !old?.conflict) continue
      const base = this.base.groups.find(group => group.id === groupId)?.frames.find(frame => (frame.identity || frame.id) === row.identity)
      if (!base) throw new Error('照片来源已变化，请刷新并重新核对')
      const entry = { groupId, groupRevision: group.revision, frameId: row.frame.id, identity: row.identity,
        baseSelected: old && !old.conflict ? old.baseSelected : base.selected,
        baseManual: old && !old.conflict ? old.baseManual : Boolean(base.manual),
        selected: change.selected, manual, sequence: ++this.sequence, conflict: false }
      this.pending.set(row.identity, entry)
      row.frame.selected = entry.selected; row.frame.manual = entry.manual; changed = true
    }
    if (!changed) return false
    this.touched.set(groupId, { groupRevision: group.revision,
      members: group.frames.map(frame => frame.identity || frame.id), sequence: ++this.sequence, conflict: false })
    group.manualReviewed = true; group.newFramesPending = 0
    group.selectedCount = group.frames.filter(frame => frame.selected).length
    if (record) { this.history.push({ groupId, before }); this.history = this.history.slice(-100) }
    return true
  }
  undo(groupId) {
    const index = this.history.findLastIndex(entry => entry.groupId === groupId)
    if (index < 0) return false
    const entry = this.history.splice(index, 1)[0]
    return this.edit(groupId, entry.before, false, false)
  }
  batch() {
    if (this.conflicts) throw new Error('草稿有冲突：请对相关照片重新保留/不选，或放弃未保存改动')
    const groups = [...this.touched].map(([groupId, group]) => ({ groupId, groupRevision: group.groupRevision,
      changes: [...this.pending.values()].filter(entry => entry.groupId === groupId).map(entry => ({
        frameId: entry.frameId, identity: entry.identity, selected: entry.selected, manual: entry.manual,
        baseSelected: entry.baseSelected, baseManual: entry.baseManual })) }))
    return { expectedRevision: this.base.revision, groups }
  }
  capture() { return { frames: new Map([...this.pending].map(([id, entry]) => [id, entry.sequence])),
    groups: new Map([...this.touched].map(([id, entry]) => [id, entry.sequence])) } }
  acknowledge(snapshot, sent) {
    const rows = new Map(snapshot.groups.flatMap(group => group.frames.map(frame => [frame.identity || frame.id, { frame, group }])))
    for (const [id, sequence] of sent.frames) {
      const entry = this.pending.get(id), row = rows.get(id)
      if (!entry) continue
      if (entry.sequence === sequence) this.pending.delete(id)
      else if (row && snapshot.revision >= this.base.revision) {
        entry.baseSelected = row.frame.selected; entry.baseManual = Boolean(row.frame.manual)
        entry.groupRevision = row.group.revision
      }
    }
    for (const [id, sequence] of sent.groups) if (this.touched.get(id)?.sequence === sequence) this.touched.delete(id)
    return this.accept(snapshot)
  }
  discard() { this.pending.clear(); this.touched.clear(); this.history = []; return this.accept(this.base) }
}
class ProgressiveImage {
  constructor(win, replace, status) {
    this.win = win; this.replace = replace; this.status = status; this.sequence = 0; this.level = 480; this.current = null
  }
  start(frame) {
    this.stop(); this.frame = frame; this.level = 480; this.current = null; this.failed = false
    this.status('正在加载 480 低清预览…')
    this.load(480)
  }
  stop() { this.sequence++; this.win.clearTimeout(this.timer); this.loading = false }
  async load(level) {
    if (this.loading || !validFrameId(this.frame?.id)) return
    const sequence = this.sequence, frame = this.frame
    this.loading = true
    const image = new this.win.Image()
    image.className = 'source-image'; image.alt = frame.sourceLabel || frame.id
    image.decoding = 'async'; image.draggable = false
    image.src = level === 'original' ? '/original/' + frame.id : previewURL(frame.id, level)
    try {
      await image.decode()
      if (sequence !== this.sequence) return
      this.loading = false
      const sourceRatio = (frame.width || 1) / (frame.height || 1), decodedRatio = image.naturalWidth / image.naturalHeight
      if (!image.naturalWidth || Math.abs(sourceRatio / decodedRatio - 1) > .015) {
        this.failed = true; this.status('预览尺寸与此帧 EXIF 坐标不符；不显示错误图像 / 轮廓，请刷新核对。'); return
      }
      this.current = image; this.level = level
      this.replace(image, frame)
      this.notify()
      if (this.needed) this.schedule(this.needed)
    } catch {
      if (sequence !== this.sequence) return
      this.loading = false; this.failed = true
      this.status(this.current ? `更高清预览未能解码，继续显示 ${this.level} 预览。` : '480 预览读取失败；未加载原文件，请刷新。')
    }
  }
  notify() {
    const image = this.current, frame = this.frame
    if (!image) return
    const original = this.level === 'original' && new URL(image.currentSrc || image.src, this.win.location.href).pathname === '/original/' + frame.id
    if (original) {
      const exact = image.naturalWidth === frame.width && image.naturalHeight === frame.height
      this.status(`${exact ? '原文件像素已核对' : '原文件尺寸不符，不能标为原生像素'} · 当前 ${image.naturalWidth}×${image.naturalHeight} / 帧 ${frame.width}×${frame.height} · 当前 src /original/${frame.id}`)
    } else {
      this.status(`${this.level} 预览 · 当前 ${image.naturalWidth}×${image.naturalHeight} / EXIF 帧 ${frame.width}×${frame.height} · 放大后逐级解码替换，不是原生像素`)
    }
  }
  schedule(needed) {
    this.needed = needed; this.win.clearTimeout(this.timer)
    if (!this.current || this.loading || this.failed) return
    const level = nextResolution(this.level, this.frame, needed)
    if (level) this.timer = this.win.setTimeout(() => this.load(level), 320)
  }
}
export function createReviewApp(doc = document, win = window) {
  const $ = id => doc.getElementById(id)
  const node = (tag, className = '', text = '') => {
    const element = doc.createElement(tag); element.className = className
    if (text !== '') element.textContent = String(text)
    return element
  }
  const svgNode = (tag, attributes = {}) => {
    const element = doc.createElementNS(NS, tag)
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value))
    return element
  }
  const state = { snapshot: null, gallery: { items: [] }, groupId: null, frameIndex: 0, detail: null,
    view: { scale: 1, cx: .5, cy: .5 }, multi: false, busy: false, loading: false, detailSequence: 0,
    selectedCrop: null, hoveredCrop: null, pointers: new Map(), wallFull: false, mask: true, pose: false,
    cleanup: null, cleanupSequence: 0, cleanupBusy: false, cleanupExit: false, referenceSequence: 0, helpPinned: false,
    ignoreFilmClickUntil: 0, saving: false, saveError: '', exitIntent: null, closeId: null }
  const draft = new ReviewDraft()
  function releasePointers() {
    const stage = $('stage')
    for (const id of [...state.pointers.keys()]) if (stage.hasPointerCapture(id)) stage.releasePointerCapture(id)
    state.pointers.clear()
  }
  const group = () => state.snapshot?.groups.find(group => group.id === state.groupId)
  const label = group => groupLabel(group, state.snapshot.groups.indexOf(group) + 1)
  const frame = () => {
    const summary = group()?.frames[state.frameIndex]
    if (!summary) return null
    const detail = state.detail?.frames?.find(frame => frame.id === summary.id)
    return { ...summary, ...detail, selected: summary.selected, automatic: summary.automatic, manual: summary.manual,
      sourceLabel: summary.sourceLabel || detail?.source?.primary?.relative || summary.id, instances: detail?.instances || [] }
  }
  let visibleGroups = []
  const visible = () => visibleGroups
  const bridge = createNativeBridge(win, {
    capability: async writable => {
      updateWritable()
      if (!writable) return
      if (!bridge.validated) { draftStatus(); return }
      try { applySnapshot(await bridge.request('state')); draftStatus() }
      catch (error) { setStatus(error.message, true) }
    },
    progress: message => {
      const completed = message.completed ?? message.current ?? message.processed
      $('trash-progress').textContent = `系统回收进度：${finite(completed) ? completed : '处理中'}${finite(message.total) ? ' / ' + message.total : ''}`
    }
  })
  function setStatus(message, error = false) {
    for (const id of ['save-status', 'immersive-status']) { $(id).textContent = message; $(id).dataset.error = String(error) }
  }
  function updateWritable() {
    const focused = doc.activeElement
    const disabled = !bridge.writable || state.cleanupBusy || !state.snapshot
    doc.querySelectorAll('[data-write]').forEach(button => { button.disabled = disabled })
    $('cleanup').disabled = disabled || state.saving || !bridge.validated
    $('reset-exit').disabled = disabled || state.saving || !bridge.validated
    for (const id of ['save-draft', 'save-immersive']) $(id).disabled = disabled || state.saving || !bridge.validated || !draft.dirty || draft.conflicts > 0
    $('discard-draft').disabled = disabled || state.saving || !draft.dirty
    doc.querySelectorAll('.frame-check input').forEach(input => { input.disabled = disabled || !state.multi })
    if (focused?.disabled && !$('immersive').hidden) $('stage').focus({ preventScroll: true })
  }
  function applySnapshot(snapshot) {
    const selectedFrame = frame()?.id
    state.snapshot = draft.accept(snapshot)
    if (group()) {
      const index = group().frames.findIndex(frame => frame.id === selectedFrame)
      state.frameIndex = index >= 0 ? index : Math.min(state.frameIndex, Math.max(0, group().frames.length - 1))
    }
    renderHome()
    if (state.groupId) { renderTitles(); renderFilmstrip(); renderWall(); renderNav() }
    updateWritable()
  }
  async function refresh() {
    if (state.loading || state.busy) return
    state.loading = true; $('refresh').disabled = true; updateWritable(); $('group-grid').setAttribute('aria-busy', 'true')
    try {
      const [snapshot, gallery] = await Promise.all([fetchJSON('/review.json'), fetchJSON('/gallery.json')])
      state.gallery = gallery
      applySnapshot(snapshot); renderDirectories()
      draftStatus()
    } catch (error) { setStatus(error.message, true) }
    finally { state.loading = false; $('refresh').disabled = false; $('group-grid').setAttribute('aria-busy', 'false'); updateWritable() }
  }
  let refreshTimer
  const requestRefresh = () => {
    win.clearTimeout(refreshTimer)
    refreshTimer = win.setTimeout(() => {
      if (state.loading || state.busy) { requestRefresh(); return }
      refresh()
    }, 250)
  }
  const refreshMessage = event => {
    if (win.parent !== win && event.source === win.parent &&
        ['null', 'file://'].includes(event.origin) && event.data?.type === 'birdshot-review-refresh') requestRefresh()
  }
  win.addEventListener('message', refreshMessage)
  const polling = win.setInterval(() => {
    if (state.gallery?.status === 'processing') requestRefresh()
  }, 5000)
  win.addEventListener('pagehide', () => {
    win.removeEventListener('message', refreshMessage); win.clearInterval(polling); win.clearTimeout(refreshTimer)
  }, { once: true })
  async function fetchJSON(url) {
    const response = await win.fetch(url, { credentials: 'same-origin', cache: 'no-store' })
    if (!response.ok) throw new Error(`读取失败（${response.status}），请刷新`)
    return response.json()
  }
  function cover(group, full = false) {
    if (!full && group.cover?.crop && group.cover.frameId && group.frames.some(frame => frame.id === group.cover.frameId)) {
      const item = state.gallery.items?.find(item => item.groupId === group.id && item.frameId === group.cover.frameId && item.crop === group.cover.crop)
      if (item?.birdVerification?.status === 'bird') return { url: mediaURL(group.cover.crop), crop: true }
    }
    const first = group.frames.find(frame => frame.id === group.cover?.frameId && frame.available !== false) ||
      group.frames.find(frame => frame.available !== false) || group.frames[0]
    return { url: first ? previewURL(first.id) : null, crop: false }
  }
  function image(url, label, lazy = true) {
    const img = node('img'); img.alt = label || '模拟 / 照片预览'; img.decoding = 'async'
    img.loading = lazy ? 'lazy' : 'eager'
    if (url) img.src = url
    return img
  }
  function groupBadges(group) {
    const badges = node('div', 'badges')
    const selected = group.frames.filter(frame => frame.selected).length
    if (selected) badges.append(node('span', 'badge', `已优选 ${selected}`))
    if (group.manualReviewed) badges.append(node('span', 'badge manual', '人工已审阅'))
    else badges.append(node('span', 'badge pending', '待审阅'))
    if (group.newFramesPending) badges.append(node('span', 'badge pending', `新增 ${group.newFramesPending} 待审`))
    if (!selected) badges.append(node('span', 'badge pending', '无保留'))
    if (draft.touched.has(group.id)) badges.append(node('span', 'badge pending', '未保存'))
    return badges
  }
  function renderHome() {
    if (!state.snapshot) return
    visibleGroups = filteredGroups(state.snapshot, state.gallery, {
      mode: $('navigation-mode').value, directory: $('navigation-group').value,
      search: $('search').value, status: $('status-filter').value })
    const entries = visible(), grid = $('group-grid')
    grid.replaceChildren()
    $('summary').textContent = `${state.snapshot.groups.length} 组 · ${state.snapshot.groups.reduce((sum, group) => sum + group.frames.length, 0)} 帧 · 按原始连拍顺序`
    $('filter-status').textContent = entries.length === state.snapshot.groups.length ? '' : `筛选命中 ${entries.length} 组；始终保留完整连拍，不拆组、不按技术名次重排。`
    if (!entries.length) { grid.append(node('p', 'empty-home', '没有匹配的连拍组。')); return }
    for (const { group, matchedFrames } of entries) {
      const card = node('article', 'group-card'), button = node('button', 'group-open')
      button.type = 'button'; button.dataset.groupId = group.id
      const representative = cover(group), picture = node('div', 'cover')
      if (representative.url) picture.append(image(representative.url, label(group)))
      else picture.append(node('p', 'empty-home', '无可用预览'))
      picture.append(node('span', 'tag', representative.crop ? '代表鸟体裁剪' : '原始帧 · 480 预览'))
      const copy = node('div', 'group-copy')
      copy.append(node('strong', '', label(group)), groupBadges(group),
        node('p', 'muted', `${group.frames.length} 帧${matchedFrames < group.frames.length ? ` · 筛选命中 ${matchedFrames} 帧（完整组）` : ''}`))
      if (group.capture?.time || group.capture?.placeName) copy.append(node('p', 'muted', [formatCaptureTime(group.capture.time), group.capture.placeName].filter(Boolean).join(' · ')))
      button.append(picture, copy); button.addEventListener('click', () => openGroup(group.id)); card.append(button); grid.append(card)
    }
  }
  function renderDirectories() {
    const mode = $('navigation-mode').value, select = $('navigation-group'), before = select.value
    select.replaceChildren()
    const all = node('option', '', '全部'); all.value = ''; select.append(all)
    for (const entry of state.gallery.navigation?.groups?.[mode] || []) {
      const option = node('option', '', entry.name); option.value = entry.id; select.append(option)
    }
    select.value = [...select.options].some(option => option.value === before) ? before : ''
    $('navigation-label').hidden = mode === 'all'
    if (mode !== 'all' && select.options.length === 1) setStatus('该目录尚无可关联数据；仍显示全部真实连拍。')
    renderHome()
  }
  async function openGroup(id) {
    if (!validGroupId(id) || !state.snapshot?.groups.some(group => group.id === id)) return
    if (state.groupId === id) return
    stopCanvasKeys()
    state.detailSequence++; const sequence = state.detailSequence
    state.groupId = id; state.frameIndex = 0; state.detail = null; state.multi = false; state.selectedCrop = null; state.hoveredCrop = null
    state.view = { scale: 1, cx: .5, cy: .5 }; releasePointers()
    state.helpPinned = false; closeDrawer(); $('immersive').hidden = false; doc.body.classList.add('reviewing'); bridge.mode(true)
    $('multiselect').setAttribute('aria-pressed', 'false')
    renderTitles(); renderFilmstrip(); renderWall(); renderNav(); showFrame()
    try {
      const detail = await fetchJSON('/gallery/group/' + id + '.json')
      if (sequence !== state.detailSequence || state.groupId !== id) return
      if (detail.id !== id || !Array.isArray(detail.frames)) throw new Error('连拍详情不匹配，请刷新')
      state.detail = detail
      renderCrops(); renderOverlay(); renderTransform()
      const current = frame()
      if (current && (progressive.frame?.width !== current.width || progressive.frame?.height !== current.height)) progressive.start(current)
    } catch (error) {
      if (sequence === state.detailSequence) setStatus(`${error.message}；保留全部原始帧浏览。`, true)
    }
  }
  function renderTitles() {
    const active = group(), current = frame()
    if (!active) return
    $('group-title').textContent = label(active)
    $('frame-title').textContent = current ? `${state.frameIndex + 1} / ${active.frames.length} · ${current.sourceLabel}${current.capture?.time ? ' · ' + formatCaptureTime(current.capture.time, true) : ''}${current.selected ? ' · 已保留' : ' · 未保留'}${active.manualReviewed ? ' · 人工已审阅' : ''}` : '此组没有可用帧'
  }
  const progressive = new ProgressiveImage(win, (img, current) => {
    if (frame()?.id !== current.id) return
    $('source-layer').querySelector('.source-image')?.remove()
    $('source-layer').prepend(img)
    $('source-overlay').hidden = false; renderTransform(); renderOverlay()
  }, message => { $('resolution-status').textContent = message })
  function showFrame() {
    const current = frame()
    progressive.stop(); $('source-layer').querySelector('.source-image')?.remove()
    $('source-overlay').replaceChildren(); $('leaders').replaceChildren()
    state.selectedCrop = null; state.hoveredCrop = null; releasePointers(); state.referenceSequence++
    renderTitles(); renderCrops(); renderNav()
    if (!current) { $('resolution-status').textContent = '没有可用帧'; return }
    $('download-original').href = '/original/' + current.id + '?download=1'
    renderTransform()
    progressive.start(current)
  }
  function renderNav() {
    const focused = doc.activeElement
    const entries = visible(), index = entries.findIndex(entry => entry.group.id === state.groupId)
    $('previous-group').disabled = index <= 0
    $('next-group').disabled = index < 0 || index >= entries.length - 1
    $('previous-frame').disabled = state.frameIndex <= 0
    $('next-frame').disabled = !group() || state.frameIndex >= group().frames.length - 1
    if (focused?.disabled && !$('immersive').hidden) $('stage').focus({ preventScroll: true })
  }
  function moveGroup(delta) {
    if (state.cleanupBusy) return
    const entries = visible(), index = entries.findIndex(entry => entry.group.id === state.groupId), entry = entries[index + delta]
    if (index >= 0 && entry) openGroup(entry.group.id)
  }
  function navigateFrame(index) {
    if (!group() || index < 0 || index >= group().frames.length || index === state.frameIndex) return
    state.frameIndex = index
    renderFilmstrip(); showFrame()
  }
  function moveFrame(delta) { navigateFrame(state.frameIndex + delta) }
  function renderTransform() {
    const current = frame()
    if (!current || $('immersive').hidden) return
    const stage = $('stage'), result = geometry(current, stage.clientWidth, stage.clientHeight, state.view), layer = $('source-layer')
    layer.style.width = result.width + 'px'; layer.style.height = result.height + 'px'
    layer.style.transform = `translate(${result.x}px,${result.y}px) scale(${result.factor})`
    layer.dataset.scale = String(state.view.scale); layer.dataset.centerX = String(state.view.cx); layer.dataset.centerY = String(state.view.cy)
    const needed = Math.max(result.width, result.height) * result.factor * Math.min(win.devicePixelRatio || 1, 3)
    progressive.schedule(needed)
    drawLeaders()
  }
  function selectedInstance() {
    const current = frame()
    return current ? frameInstances(state.groupId, current, state.gallery.items).find(instance => instance.crop === state.selectedCrop && instance.birdVerification?.status === 'bird') : null
  }
  function renderCrops() {
    const current = frame(), instances = current ? frameInstances(state.groupId, current, state.gallery.items) : []
    const birds = instances.filter(instance => instance.birdVerification?.status === 'bird')
    const unknown = instances.filter(instance => !['bird', 'nonBird'].includes(instance.birdVerification?.status))
    $('crop-grid').replaceChildren(); $('unknown-grid').replaceChildren()
    if (!birds.some(instance => instance.crop === state.selectedCrop)) state.selectedCrop = birds[0]?.crop || null
    function cropTile(instance, pending = false) {
      const button = node('button', 'crop-button')
      button.type = 'button'; button.dataset.frameId = current.id; button.dataset.instanceId = String(instance.id); button.dataset.crop = instance.crop
      button.classList.toggle('active', !pending && instance.crop === state.selectedCrop)
      button.append(image(mediaURL(instance.thumbnail || instance.crop), pending ? '未知 / 待确认裁剪' : '已确认鸟体裁剪'),
        node('span', '', pending ? '待确认，非已验鸟' : `鸟体 ${String(instance.id)}${instance.decision === 'keep' ? ' · 自动优选' : ' · 待审'}`))
      button.addEventListener('click', () => {
        if (pending) return
        state.selectedCrop = instance.crop; state.hoveredCrop = null
        $('crop-grid').querySelectorAll('.crop-button').forEach(node => node.classList.toggle('active', node.dataset.crop === instance.crop))
        renderSpecies(); renderOverlay(); drawLeaders()
      })
      if (!pending) {
        button.addEventListener('pointerenter', () => { state.hoveredCrop = instance.crop; renderOverlay(); drawLeaders() })
        button.addEventListener('pointerleave', () => { state.hoveredCrop = null; renderOverlay(); drawLeaders() })
        button.addEventListener('focus', () => { state.hoveredCrop = instance.crop; renderOverlay(); drawLeaders() })
        button.addEventListener('blur', () => { state.hoveredCrop = null; renderOverlay(); drawLeaders() })
      }
      return button
    }
    birds.forEach(instance => $('crop-grid').append(cropTile(instance)))
    if (!birds.length) $('crop-grid').append(node('p', 'muted', '无已确认鸟体；原图 / 全部帧仍可审阅。'))
    unknown.forEach(instance => $('unknown-grid').append(cropTile(instance, true)))
    $('unknown-section').hidden = !unknown.length; $('unknown-section').open = false
    $('unknown-label').textContent = `未知 / 待确认 ${unknown.length}（独立，不混入鸟体）`
    $('empty-frame').hidden = Boolean(birds.length)
    renderSpecies(); renderOverlay()
  }
  function renderOverlay() {
    const current = frame(), svg = $('source-overlay')
    svg.replaceChildren()
    if (!current || !finite(current.width) || !finite(current.height)) return
    svg.setAttribute('viewBox', `0 0 ${current.width} ${current.height}`)
    svg.dataset.frameId = current.id
    const focused = state.hoveredCrop || state.selectedCrop
    for (const instance of frameInstances(state.groupId, current, state.gallery.items).filter(instance => instance.birdVerification?.status === 'bird')) {
      if (state.mask && typeof instance.mask?.path === 'string' && instance.mask.path) {
        const path = svgNode('path', { d: instance.mask.path, stroke: '#76b8ff', fill: '#76b8ff', 'fill-rule': 'evenodd' })
        path.classList.toggle('focused', instance.crop === focused)
        path.dataset.frameId = current.id; path.dataset.instanceId = String(instance.id); path.dataset.crop = instance.crop
        svg.append(path)
      }
      if (state.pose) for (const point of instance.posePoints || instance.pose?.points || []) {
        if (!finite(point.x) || !finite(point.y) || !finite(point.confidence) || point.confidence < .65) continue
        svg.append(svgNode('circle', { cx: point.x, cy: point.y, r: Math.max(2, current.width / 400), fill: '#ffc794' }))
      }
    }
    svg.hidden = !progressive.current || progressive.failed && !progressive.current
    drawLeaders()
  }
  function drawLeaders() {
    const svg = $('leaders'); svg.replaceChildren()
    const current = frame()
    if (!current || !progressive.current || !$('source-layer').querySelector('.source-image')) return
    const body = $('viewer-body').getBoundingClientRect(), stage = $('stage').getBoundingClientRect()
    const result = geometry(current, stage.width, stage.height, state.view)
    svg.setAttribute('viewBox', `0 0 ${body.width} ${body.height}`)
    const focus = state.hoveredCrop || state.selectedCrop
    for (const instance of frameInstances(state.groupId, current, state.gallery.items).filter(instance => instance.birdVerification?.status === 'bird')) {
      const rect = instance.cropRect
      if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(finite) || rect[2] <= 0 || rect[3] <= 0) continue
      const tile = [...$('crop-grid').querySelectorAll('.crop-button')].find(tile => tile.dataset.frameId === current.id &&
        tile.dataset.instanceId === String(instance.id) && tile.dataset.crop === instance.crop)
      if (!tile || !tile.getClientRects().length) continue
      const target = tile.getBoundingClientRect(), drawer = $('review-drawer').getBoundingClientRect()
      if (target.top < drawer.top || target.bottom > drawer.bottom) continue
      const x = stage.left - body.left + result.x + (rect[0] + rect[2] / 2) * result.factor
      const y = stage.top - body.top + result.y + (rect[1] + rect[3] / 2) * result.factor
      if (x < stage.left - body.left || x > stage.right - body.left || y < 0 || y > body.height) continue
      const x2 = target.left - body.left + 4, y2 = target.top - body.top + target.height / 2
      const line = svgNode('line', { x1: x, y1: y, x2, y2 })
      line.dataset.frameId = current.id; line.dataset.instanceId = String(instance.id); line.dataset.crop = instance.crop
      if (instance.crop === focus) { line.style.strokeWidth = '2'; line.style.opacity = '1' }
      svg.append(line, svgNode('circle', { cx: x, cy: y, r: 3 }))
    }
  }
  function renderSpecies() {
    const instance = selectedInstance(), panel = $('top-three')
    panel.replaceChildren(); const sequence = ++state.referenceSequence
    $('technical').textContent = technicalSentence(instance)
    const candidates = topThree(instance?.species)
    if (!candidates.length) { panel.append(node('p', 'muted', instance ? '未提供有效鸟种候选。' : '选择已确认鸟体后显示 Top3。')); return }
    for (const candidate of candidates) {
      const card = node('article', 'species-candidate'), heading = node('div', 'candidate-heading')
      heading.append(node('strong', '', candidate.commonName || '中文名待补'), node('span', 'classification-score', probabilityLabel(candidate.probability)))
      card.append(heading)
      const reference = node('div', 'candidate-reference'), note = node('span', 'reference-label', '参考资料待加载')
      reference.append(note); card.append(reference); panel.append(card)
      if (typeof candidate.scientificName !== 'string' || !candidate.scientificName.trim()) { note.textContent = '未提供参考资料名称'; continue }
      fetchJSON('/species-reference?scientificName=' + encodeURIComponent(candidate.scientificName)).then(result => {
        if (sequence !== state.referenceSequence || !card.isConnected) return
        reference.replaceChildren()
        if (result.image?.url && /^\/species-media\/[a-f0-9]{64}\.jpg$/.test(result.image.url)) {
          const img = image(result.image.url, '公开物种参考图，非本次照片'); img.className = 'reference-picture'; reference.append(img)
        }
        const links = node('div')
        links.append(node('p', 'reference-label', '参考图 · 非本次分类确认'))
        const article = externalLink(result.article?.title || '百科', result.article?.url)
        if (article) links.append(article)
        else links.append(node('p', 'reference-label', result.message || '暂无公开参考资料'))
        reference.append(links)
        if (result.image?.sourceUrl) {
          const credit = node('div', 'reference-source')
          credit.append(node('span', '', `来源：${result.image.author || '作者未提供'} · `))
          const source = externalLink('原始图页', result.image.sourceUrl), license = externalLink(result.image.license || '许可', result.image.licenseUrl)
          if (source) credit.append(source)
          if (license) credit.append(doc.createTextNode(' · '), license)
          card.append(credit)
        }
      }).catch(() => { if (sequence === state.referenceSequence && card.isConnected) note.textContent = '参考资料暂不可用' })
    }
  }
  function externalLink(text, url) {
    if (typeof url !== 'string' || !/^https:\/\//.test(url)) return null
    const link = node('a', '', text); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link
  }
  function renderWall() {
    $('cross-group-grid').replaceChildren()
    for (const { group } of visible()) {
      const button = node('button', 'wall-card'), representative = cover(group, state.wallFull)
      button.type = 'button'; button.dataset.groupId = group.id; button.classList.toggle('active', group.id === state.groupId)
      if (representative.url) button.append(image(representative.url, label(group)))
      button.append(node('span', '', `${label(group)} · ${group.frames.length} 帧`))
      button.addEventListener('click', () => { openGroup(group.id); closeDrawer() })
      $('cross-group-grid').append(button)
    }
  }
  function renderFilmstrip() {
    const strip = $('filmstrip'), before = strip.scrollLeft
    strip.replaceChildren()
    for (const [index, current] of (group()?.frames || []).entries()) {
      const tile = node('div', 'frame-tile'); tile.dataset.frameId = current.id
      tile.classList.toggle('current', index === state.frameIndex); tile.classList.toggle('multi', state.multi)
      const button = node('button', 'frame-open'); button.type = 'button'; button.dataset.frameId = current.id
      button.setAttribute('aria-label', `第 ${index + 1} 帧 ${current.sourceLabel || current.id}`)
      button.append(image(previewURL(current.id), current.sourceLabel || current.id, false), node('span', '', current.sourceLabel || current.id))
      const label = node('label', 'frame-check'), checkbox = node('input')
      label.classList.toggle('selected', current.selected)
      checkbox.type = 'checkbox'; checkbox.checked = current.selected; checkbox.disabled = !state.multi || !bridge.writable || state.cleanupBusy
      checkbox.setAttribute('aria-label', `保留 ${current.sourceLabel || current.id}`)
      label.append(checkbox, doc.createTextNode('保留')); tile.append(button, label)
      if (current.manual) tile.append(node('span', 'frame-state', '人工'))
      else if (current.selected && current.automatic) tile.append(node('span', 'frame-state', '自动'))
      let timer = null, pointerStart = null, suppressUntil = 0
      button.addEventListener('pointerdown', event => {
        if (event.button !== 0 || event.isPrimary === false) return
        pointerStart = { x: event.clientX, y: event.clientY }
        timer = win.setTimeout(() => {
          suppressUntil = Date.now() + 800; state.ignoreFilmClickUntil = suppressUntil; timer = null
          state.multi = true; $('multiselect').setAttribute('aria-pressed', 'true')
          strip.querySelectorAll('.frame-tile').forEach(tile => tile.classList.add('multi'))
          updateWritable(); navigateFrame(index)
        }, 500)
      })
      const cancel = () => { win.clearTimeout(timer); timer = null }
      button.addEventListener('pointermove', event => {
        if (pointerStart && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 8) cancel()
      })
      button.addEventListener('pointerup', cancel); button.addEventListener('pointercancel', cancel); button.addEventListener('pointerleave', cancel)
      button.addEventListener('click', () => {
        if (Date.now() < suppressUntil || Date.now() < state.ignoreFilmClickUntil) return
        navigateFrame(index)
        if (state.multi) editChanges([{ frameId: current.id, selected: !group().frames.find(frame => frame.id === current.id).selected }])
      })
      label.addEventListener('click', event => event.stopPropagation())
      label.addEventListener('pointerdown', event => event.stopPropagation())
      checkbox.addEventListener('change', () => {
        const desired = checkbox.checked
        checkbox.checked = group().frames.find(frame => frame.id === current.id).selected
        navigateFrame(index); editChanges([{ frameId: current.id, selected: desired }])
      })
      strip.append(tile)
    }
    strip.scrollLeft = before
    const current = strip.querySelector('.current')
    if (current) {
      const left = current.offsetLeft - strip.offsetLeft, right = left + current.offsetWidth
      if (left < strip.scrollLeft) strip.scrollLeft = left
      else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = right - strip.clientWidth
    }
    updateWritable()
  }
  async function recover(error) {
    try { applySnapshot(bridge.writable ? await bridge.request('state') : await fetchJSON('/review.json')) }
    catch (failure) { state.saveError = `状态重读失败：${failure.message}。` }
    state.saveError += /revision|conflict|并发|版本|changed/i.test(error.message) ?
      '保存版本冲突：草稿未丢失，已合并未冲突项；冲突照片请重新保留/不选后再保存。' :
      `保存失败：${error.message}；内存遴选仍保留，可重试。`
    draftStatus()
  }
  function draftStatus() {
    setStatus(state.saveError || (!bridge.writable ? state.snapshot?.cached ?
      '上次已缓存内容 · 正在检查来源与模型（浏览器只读）' : '浏览器只读 · 请在桌面中遴选' :
      draft.dirty ? `未保存 · ${draft.touched.size} 组 / ${draft.pending.size} 张改动${draft.conflicts ? ` · ${draft.conflicts} 项冲突需核对` : ''} · 不会修改原照片` :
        '遴选状态已同步 · Space/W/S仅改内存，点击“保存遴选状态”后落盘；回收是独立操作'), Boolean(state.saveError))
    if (bridge.writable && !bridge.validated && !state.saveError) setStatus(`已缓存，正在检查来源与模型${draft.dirty ? ` · 未保存 ${draft.touched.size} 组改动` : ''} · 校验完成后才能保存/清理`)
    updateWritable()
  }
  function repaintChoices() {
    renderTitles()
    for (const tile of $('filmstrip').querySelectorAll('.frame-tile')) {
      const current = group()?.frames.find(frame => frame.id === tile.dataset.frameId)
      if (!current) continue
      tile.querySelector('input').checked = current.selected
      tile.querySelector('.frame-check').classList.toggle('selected', current.selected)
      let tag = tile.querySelector('.frame-state')
      if (!tag) { tag = node('span', 'frame-state'); tile.append(tag) }
      tag.textContent = current.manual ? draft.pending.has(current.identity || current.id) ? '人工 · 未保存' : '人工' :
        current.selected && current.automatic ? '自动' : ''
    }
    const button = [...$('group-grid').querySelectorAll('.group-open')].find(button => button.dataset.groupId === state.groupId)
    if (button && group()) button.querySelector('.badges').replaceWith(groupBadges(group()))
    draftStatus()
  }
  function editChanges(changes, action = 'set') {
    const active = group()
    if (!active || state.cleanupBusy) return
    if (!bridge.writable) { setStatus('浏览器只读 · 请在桌面中遴选', true); return }
    if (action === 'undo') draft.undo(active.id)
    else draft.edit(active.id, action === 'restoreAutomatic' ?
      active.frames.map(frame => ({ frameId: frame.id, selected: frame.automatic })) : changes, action === 'restoreAutomatic')
    state.snapshot = draft.working; repaintChoices()
  }
  async function saveDraft() {
    if (state.saving || state.cleanupBusy || !bridge.writable || !draft.dirty) return !draft.dirty
    let batch
    try { batch = draft.batch() }
    catch (error) { state.saveError = error.message; draftStatus(); return false }
    const sent = draft.capture()
    state.saving = true; state.saveError = ''; updateWritable(); setStatus('正在保存遴选状态…仍可切帧、切组和继续遴选')
    try {
      const snapshot = await bridge.request('save', batch)
      draft.acknowledge(snapshot, sent); applySnapshot(snapshot); draftStatus()
      return !draft.dirty
    } catch (error) { await recover(error); return false }
    finally { state.saving = false; updateWritable() }
  }
  async function preference(exitAction) {
    if (!bridge.writable || state.busy) return false
    state.busy = true; updateWritable()
    try { applySnapshot(await bridge.request('preferences', { expectedRevision: state.snapshot.revision, exitAction })); return true }
    catch (error) { await recover(error); return false }
    finally { state.busy = false; updateWritable() }
  }
  function toggleCurrent(selected) {
    const current = frame()
    if (current) editChanges([{ frameId: current.id, selected: selected ?? !current.selected }])
  }
  function leave() {
    stopCanvasKeys()
    state.detailSequence++; state.groupId = null; state.detail = null; releasePointers(); progressive.stop(); state.referenceSequence++
    $('source-layer').querySelector('.source-image')?.remove(); $('source-overlay').replaceChildren(); $('leaders').replaceChildren()
    $('immersive').hidden = true
    doc.body.classList.remove('reviewing'); bridge.mode(false)
    closeDrawer(); renderHome()
  }
  function finishExit() {
    if (state.exitIntent === 'close') bridge.closeResponse(state.closeId, true)
    else if (state.exitIntent === 'cleanup') beginCleanup()
    else if (state.exitIntent !== 'discard') leave()
    state.exitIntent = null; state.closeId = null
  }
  function requestExit(intent = 'exit', id = null) {
    stopCanvasKeys()
    if (state.cleanupBusy || state.saving || doc.querySelector('dialog[open]')) {
      if (intent === 'close') bridge.closeResponse(id, false)
      return
    }
    state.exitIntent = intent; state.closeId = id
    if (!draft.dirty) { finishExit(); return }
    $('exit-title').textContent = intent === 'cleanup' ? '清理前请先保存遴选' : '有未保存的遴选改动'
    $('exit-save').textContent = intent === 'cleanup' ? '保存后预览清理' : intent === 'discard' ? '保存改动' : '保存并退出'
    $('exit-discard').hidden = intent === 'cleanup'
    $('exit-dialog').showModal()
  }
  function closeDrawer() { $('review-drawer').classList.remove('open'); $('drawer-toggle').setAttribute('aria-expanded', 'false'); drawLeaders() }
  async function beginCleanup(exitAfter = false) {
    if (!bridge.writable || state.busy || state.cleanupBusy) return
    if (draft.dirty || state.saving) { requestExit('cleanup'); return }
    stopCanvasKeys()
    state.cleanupExit = exitAfter; state.cleanup = null
    $('include-raw').checked = false; $('include-zero').checked = false
    $('cleanup-cancel').textContent = '取消清理'
    $('cleanup-files').replaceChildren(); $('cleanup-skipped').replaceChildren(); $('trash-progress').textContent = ''
    $('cleanup-dialog').showModal(); await loadCleanupPreview()
  }
  async function cancelToken(token) {
    if (!token) return
    try { await bridge.request('cleanupCancel', { token }) } catch { /* Cancellation never executes a plan. */ }
  }
  async function loadCleanupPreview() {
    const sequence = ++state.cleanupSequence, old = state.cleanup
    state.cleanup = null; $('cleanup-execute').disabled = true; $('cleanup-summary').textContent = '正在读取本次清理预览…'
    $('cleanup-files').replaceChildren(); $('cleanup-skipped').replaceChildren()
    await cancelToken(old?.token)
    if (sequence !== state.cleanupSequence || !$('cleanup-dialog').open) return
    try {
      const preview = await bridge.request('cleanupPreview', { expectedRevision: state.snapshot.revision,
        includeRaw: $('include-raw').checked, includeZero: $('include-zero').checked })
      if (sequence !== state.cleanupSequence || !$('cleanup-dialog').open) { await cancelToken(preview.token); return }
      if (!preview.token || !Array.isArray(preview.files) || preview.revision !== state.snapshot.revision) throw new Error('清理预览版本变化，请刷新后重新确认')
      state.cleanup = preview
      $('cleanup-summary').textContent = `保留 ${preview.keepCount ?? 0} · 拟回收照片 ${preview.photoCount ?? 0} / RAW ${preview.rawCount ?? 0} · 跳过零保留 ${preview.zeroSkipped ?? 0} / 新增帧 ${preview.newFramesSkipped ?? 0} / 不稳定 ${preview.unstableSkipped ?? 0}。请核对下面的逐文件清单。`
      for (const file of preview.files) {
        const row = node('li', '', `${file.name} · ${file.kind} · ${file.groupId} / ${file.frameId}`)
        row.dataset.fileId = file.id; $('cleanup-files').append(row)
      }
      for (const skipped of preview.skipped || []) $('cleanup-skipped').append(node('li', '', typeof skipped === 'string' ? skipped : [skipped.name || skipped.groupId || skipped.frameId, skipped.reason || skipped.message].filter(Boolean).join(' · ')))
      if (!preview.files.length) $('cleanup-files').append(node('li', '', '本次没有可回收文件。'))
      $('cleanup-execute').disabled = !preview.files.length
    } catch (error) {
      if (sequence === state.cleanupSequence && $('cleanup-dialog').open) { $('cleanup-summary').textContent = error.message; await recover(error) }
    }
  }
  async function cancelCleanup() {
    if (state.cleanupBusy) return
    state.cleanupSequence++; const token = state.cleanup?.token; state.cleanup = null
    if ($('cleanup-dialog').open) $('cleanup-dialog').close()
    if ($('zero-dialog').open) $('zero-dialog').close()
    await cancelToken(token)
  }
  async function executeCleanup() {
    if (!state.cleanup || state.cleanupBusy || !bridge.writable) return
    if (state.cleanup.revision !== state.snapshot.revision) { $('cleanup-summary').textContent = '遴选已变化，请重新核对清单。'; await loadCleanupPreview(); return }
    state.cleanupBusy = true; $('cleanup-execute').disabled = true; $('cleanup-cancel').disabled = true
    $('include-raw').disabled = true; $('include-zero').disabled = true
    $('trash-progress').textContent = '等待系统确认；尚未回收。'
    try {
      const result = await bridge.request('cleanupExecute', { token: state.cleanup.token })
      if (result.snapshot) applySnapshot(result.snapshot)
      else applySnapshot(await bridge.request('state'))
      const failures = (result.results || []).filter(result => !result.success)
      $('trash-progress').textContent = result.cancelled ? '系统确认已取消；没有宣称回收成功。' :
        `回收成功 ${(result.results || []).filter(result => result.success).length}，失败 ${failures.length}${failures.length ? '：' + failures.map(result => result.error || result.id).join('；') : ''}`
      state.cleanup = null; $('cleanup-cancel').textContent = '关闭'
      if (!result.cancelled && !failures.length && state.cleanupExit) {
        $('cleanup-dialog').close(); leave()
      }
    } catch (error) { $('trash-progress').textContent = `回收未确认：${error.message}，请核对实际状态后重新预览。`; state.cleanup = null; await recover(error) }
    finally {
      state.cleanupBusy = false; $('cleanup-cancel').disabled = false; $('include-raw').disabled = false; $('include-zero').disabled = false
    }
  }
  function bind(id, callback, type = 'click') { $(id).addEventListener(type, callback) }
  bind('refresh', refresh)
  bind('navigation-mode', renderDirectories, 'change')
  bind('navigation-group', () => { renderHome(); renderWall(); renderNav() }, 'change')
  bind('status-filter', () => { renderHome(); renderWall(); renderNav() }, 'change')
  bind('search', () => { renderHome(); renderWall(); renderNav() }, 'input')
  bind('previous-group', () => moveGroup(-1)); bind('next-group', () => moveGroup(1))
  bind('previous-frame', () => moveFrame(-1)); bind('next-frame', () => moveFrame(1))
  bind('keep-group', () => editChanges(group().frames.map(frame => ({ frameId: frame.id, selected: true }))))
  bind('reject-group', () => editChanges(group().frames.map(frame => ({ frameId: frame.id, selected: false }))))
  bind('keep-frame', () => toggleCurrent(true)); bind('reject-frame', () => toggleCurrent(false))
  bind('undo', () => editChanges([], 'undo')); bind('restore', () => editChanges([], 'restoreAutomatic'))
  bind('save-draft', saveDraft); bind('save-immersive', saveDraft)
  bind('discard-draft', () => requestExit('discard'))
  bind('multiselect', () => { state.multi = !state.multi; $('multiselect').setAttribute('aria-pressed', String(state.multi)); renderFilmstrip() })
  bind('wall-toggle', () => {
    state.wallFull = !state.wallFull; $('wall-toggle').setAttribute('aria-pressed', String(state.wallFull))
    $('wall-toggle').textContent = state.wallFull ? '480 全图' : '代表裁剪'; renderWall()
  })
  bind('mask-toggle', () => { state.mask = !state.mask; $('mask-toggle').setAttribute('aria-pressed', String(state.mask)); renderOverlay() })
  bind('pose-toggle', () => { state.pose = !state.pose; $('pose-toggle').setAttribute('aria-pressed', String(state.pose)); renderOverlay() })
  bind('reset-zoom', () => { state.view = { scale: 1, cx: .5, cy: .5 }; renderTransform() })
  bind('drawer-toggle', () => {
    const open = $('review-drawer').classList.toggle('open'); $('drawer-toggle').setAttribute('aria-expanded', String(open)); drawLeaders()
  })
  bind('drawer-close', closeDrawer); bind('review-drawer', drawLeaders, 'scroll')
  bind('exit', () => requestExit())
  function cancelExit() {
    if (state.exitIntent === 'close') bridge.closeResponse(state.closeId, false)
    state.exitIntent = null; state.closeId = null
    $('exit-dialog').close()
  }
  bind('continue-review', cancelExit)
  $('exit-dialog').addEventListener('cancel', cancelExit)
  bind('exit-save', async () => {
    if (await saveDraft()) { $('exit-dialog').close(); finishExit() }
  })
  bind('exit-discard', () => {
    state.snapshot = draft.discard(); state.saveError = ''
    $('exit-dialog').close(); repaintChoices(); finishExit()
  })
  bind('cleanup', () => beginCleanup(false))
  bind('reset-exit', async () => { if (await preference('ask')) setStatus('已重置退出偏好：退出时询问，不直接回收。') })
  bind('cleanup-cancel', cancelCleanup); bind('cleanup-execute', executeCleanup)
  bind('include-raw', loadCleanupPreview, 'change')
  bind('include-zero', () => {
    if ($('include-zero').checked) { $('include-zero').checked = false; $('zero-dialog').showModal() }
    else loadCleanupPreview()
  }, 'change')
  bind('zero-confirm', () => { $('include-zero').checked = true; $('zero-dialog').close(); loadCleanupPreview() })
  bind('zero-cancel', () => { $('include-zero').checked = false; $('zero-dialog').close() })
  $('zero-dialog').addEventListener('cancel', () => { $('include-zero').checked = false })
  $('cleanup-dialog').addEventListener('cancel', event => { event.preventDefault(); cancelCleanup() })
  const held = new Map()
  let heldRaf = null, heldTime = 0
  function stopCanvasKeys() { held.clear(); if (heldRaf !== null) win.cancelAnimationFrame(heldRaf); heldRaf = null }
  function moveCanvas(actions, amount, elapsed = false) {
    const current = frame(), stage = $('stage')
    if (!current) return
    const zoom = Number(actions.has('zoomIn')) - Number(actions.has('zoomOut'))
    if (zoom) state.view = zoomAt(state.view, current, stage.clientWidth, stage.clientHeight,
      stage.clientWidth / 2, stage.clientHeight / 2, state.view.scale *
        (elapsed ? Math.exp(zoom * amount * .0007) : Math.pow(1.12, zoom)))
    const dx = Number(actions.has('right')) - Number(actions.has('left'))
    const dy = Number(actions.has('down')) - Number(actions.has('up'))
    const length = Math.max(1, Math.hypot(dx, dy)), pixels = elapsed ? amount * .24 : 32
    state.view = panView(state.view, current, stage.clientWidth, stage.clientHeight, dx * pixels / length, dy * pixels / length)
    renderTransform()
  }
  function repeatCanvas(now) {
    if ($('immersive').hidden || doc.querySelector('dialog[open]') || doc.visibilityState === 'hidden' ||
        doc.activeElement?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) {
      stopCanvasKeys(); return
    }
    const delta = Math.min(50, Math.max(0, now - heldTime)); heldTime = now
    const actions = new Set([...held.values()].filter(key => now - key.started >= 180).map(key => key.action))
    if (actions.size) moveCanvas(actions, delta, true)
    heldRaf = held.size ? win.requestAnimationFrame(repeatCanvas) : null
  }
  doc.addEventListener('keydown', event => {
    if ($('immersive').hidden || doc.querySelector('dialog[open]')) return
    const canvasAction = canvasShortcut(event)
    if (canvasAction && !state.cleanupBusy) {
      event.preventDefault()
      const key = event.code || event.key
      if (!event.repeat && !held.has(key)) {
        held.set(key, { action: canvasAction, started: win.performance.now() })
        moveCanvas(new Set([canvasAction]), 1)
        if (heldRaf === null) { heldTime = win.performance.now(); heldRaf = win.requestAnimationFrame(repeatCanvas) }
      }
      return
    }
    const action = shortcut(event)
    if (!action) return
    event.preventDefault()
    if (state.cleanupBusy) return
    const actions = { previousGroup: () => moveGroup(-1), nextGroup: () => moveGroup(1),
      previousFrame: () => moveFrame(-1), nextFrame: () => moveFrame(1),
      toggle: () => toggleCurrent(), keep: () => toggleCurrent(true), reject: () => toggleCurrent(false), exit: requestExit }
    actions[action]()
  })
  doc.addEventListener('keyup', event => {
    held.delete(event.code || event.key)
    if (event.key === 'Control') for (const [key, value] of held) if (value.action.startsWith('zoom')) held.delete(key)
    if (!held.size) stopCanvasKeys()
  })
  win.addEventListener('blur', () => { stopCanvasKeys(); releasePointers() })
  doc.addEventListener('visibilitychange', () => { if (doc.visibilityState === 'hidden') { stopCanvasKeys(); releasePointers() } })
  doc.addEventListener('focusin', event => {
    if (event.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),dialog')) stopCanvasKeys()
  })
  win.addEventListener('message', event => {
    if (win.parent !== win && event.source === win.parent && ['null', 'file://'].includes(event.origin) &&
        event.data?.type === 'birdshot-review-close-request' && typeof event.data.id === 'string') requestExit('close', event.data.id)
  })
  win.addEventListener('beforeunload', event => {
    if (draft.dirty) { event.preventDefault(); event.returnValue = '' }
  })
  const stage = $('stage')
  stage.addEventListener('wheel', event => {
    if (!event.ctrlKey || !frame() || event.target.closest('button,a')) return
    event.preventDefault()
    stage.focus({ preventScroll: true })
    const bounds = stage.getBoundingClientRect()
    state.view = zoomAt(state.view, frame(), bounds.width, bounds.height, event.clientX - bounds.left, event.clientY - bounds.top,
      state.view.scale * Math.exp(-event.deltaY * .003))
    renderTransform()
  }, { passive: false })
  stage.addEventListener('pointerdown', event => {
    if (!frame() || event.target.closest('button,a') || event.button !== 0) return
    stage.focus({ preventScroll: true })
    state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
    stage.setPointerCapture(event.pointerId)
  })
  stage.addEventListener('pointermove', event => {
    if (!state.pointers.has(event.pointerId) || !frame()) return
    const before = [...state.pointers.values()], old = state.pointers.get(event.pointerId)
    state.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
    const after = [...state.pointers.values()], bounds = stage.getBoundingClientRect()
    if (before.length >= 2) {
      const center = points => ({ x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2 })
      const distance = points => Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y)
      const c1 = center(before), c2 = center(after), ratio = distance(after) / Math.max(1, distance(before))
      state.view = zoomAt(state.view, frame(), bounds.width, bounds.height, c1.x - bounds.left, c1.y - bounds.top, state.view.scale * ratio)
      const result = geometry(frame(), bounds.width, bounds.height, state.view)
      state.view.cx -= (c2.x - c1.x) / (result.width * result.factor)
      state.view.cy -= (c2.y - c1.y) / (result.height * result.factor)
    } else {
      const result = geometry(frame(), bounds.width, bounds.height, state.view)
      state.view.cx -= (event.clientX - old.x) / (result.width * result.factor)
      state.view.cy -= (event.clientY - old.y) / (result.height * result.factor)
    }
    renderTransform()
  })
  const release = event => {
    state.pointers.delete(event.pointerId)
    if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId)
  }
  stage.addEventListener('pointerup', release); stage.addEventListener('pointercancel', release); stage.addEventListener('lostpointercapture', release)
  const resize = new win.ResizeObserver(renderTransform)
  resize.observe(stage); resize.observe($('viewer-body'))
  win.addEventListener('pagehide', () => { stopCanvasKeys(); progressive.stop(); bridge.dispose(); resize.disconnect() }, { once: true })
  refresh()
  return { state, draft, refresh, bridge, openGroup, leave }
}
if (typeof document !== 'undefined' && document.getElementById('review-app')) createReviewApp()
