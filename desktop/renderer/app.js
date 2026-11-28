'use strict'

const $ = id => document.getElementById(id)
const api = window.birdshotDesktop
const modes = ['local', 'lan', 'all']
const labels = {
  idle: '等待处理', queued: '排队中', pending: '等待处理', scanning: '扫描中',
  processing: '处理中', running: '运行中', ready: '已就绪', completed: '已完成',
  error: '出错', failed: '失败', missing: '目录不可用', paused: '已暂停',
  starting: '启动中', stopped: '未启动', unavailable: '不可用',
}
let current = null
let busy = false
let serviceDirty = false
let frameUrl = ''
let unsubscribe
let stateEvents = 0

function node(tag, text, className) {
  const element = document.createElement(tag)
  if (text !== undefined) element.textContent = String(text)
  if (className) element.className = className
  return element
}

function errorText(error) {
  return error instanceof Error ? error.message : String(error || '未知错误')
}

function showError(message) {
  $('action-error').textContent = message
  $('action-error').hidden = !message
}

function setBusy(value) {
  busy = value
  for (const button of document.querySelectorAll('aside button, #refresh-state')) button.disabled = value || !api
}

async function action(method, ...args) {
  if (busy || !api) return
  showError('')
  setBusy(true)
  const eventsAtStart = stateEvents
  try {
    const next = await api[method](...args)
    if (eventsAtStart === stateEvents) render(next)
  } catch (error) {
    showError('操作未完成：' + errorText(error) + '。可重试；现有照片不会被删除。')
    if (!current) {
      message('无法读取本机照片库', '桌面状态读取失败：' + errorText(error) + '。请点击“刷新状态”重试。', true)
      $('runtime-status').textContent = '本机状态读取失败'
      $('sources-empty').textContent = '来源列表未能读取；不会自动添加或覆盖现有库。'
    }
  } finally {
    setBusy(false)
  }
}

function message(title, copy, error = false) {
  $('gallery-message').hidden = false
  $('gallery-message').setAttribute('role', error ? 'alert' : 'status')
  $('gallery-message-title').textContent = title
  $('gallery-message-copy').textContent = copy
}

function localGalleryUrl(value) {
  if (typeof value !== 'string' || !/^http:\/\/127\.0\.0\.1:\d+(?:\/|$)/.test(value)) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port && !url.username && !url.password ? url.href : null
  } catch { return null }
}

function renderSources(sources) {
  $('sources-empty').hidden = sources.length > 0
  $('sources-empty').textContent = '尚无照片来源。添加来源目录开始处理，或接入已有批次。'
  $('source-list').replaceChildren()
  for (const source of sources) {
    const card = node('li', undefined, 'source-card')
    card.dataset.sourceId = source.id
    card.append(node('h3', source.name || '未命名来源'), node('p', source.path, 'path'))
    card.append(node('p', labels[source.status] || '状态：' + source.status, 'source-status'))
    if (source.progress && typeof source.progress === 'object') {
      const { phase, message: progressMessage, totalPhotos, processedPhotos, totalGroups, completedGroups, cropCount } = source.progress
      const processed = source.progress.processed ?? processedPhotos
      const total = source.progress.total ?? totalPhotos
      const valid = Number.isFinite(processed) && processed >= 0 && Number.isFinite(total) && total >= processed
      const progress = node('progress')
      progress.setAttribute('aria-label', (source.name || '来源') + '原图检测提交进度')
      if (valid && total > 0) { progress.max = total; progress.value = processed }
      card.append(progress, node('p', [phase, valid ? processed + ' / ' + total : '进度等待更新', progressMessage].filter(Boolean).join(' · '), 'source-progress'))
      const counts = [
        Number.isInteger(processedPhotos) && Number.isInteger(totalPhotos) ? '原图 ' + processedPhotos + ' / ' + totalPhotos : '',
        Number.isInteger(completedGroups) && Number.isInteger(totalGroups) ? '组 ' + completedGroups + ' / ' + totalGroups : '',
        Number.isInteger(cropCount) ? '裁剪 ' + cropCount : '',
      ].filter(Boolean)
      if (counts.length) card.append(node('p', counts.join(' · '), 'source-progress'))
      if (Array.isArray(source.progress.stages)) {
        const stages = node('ol', undefined, 'source-progress')
        const statuses = { pending: '尚未开始', running: '正在处理', partial: '部分已完成', completed: '已完成', error: '失败' }
        const units = { photos: '张', groups: '组', crops: '个裁剪' }
        for (const stage of source.progress.stages) {
          const measured = Number.isInteger(stage.processed) && Number.isInteger(stage.total) && stage.total >= stage.processed
          stages.append(node('li', (stage.label || stage.id) + ' · ' + (statuses[stage.status] || stage.status) +
            (measured && stage.total > 0 ? ' · ' + stage.processed + ' / ' + stage.total + ' ' + (units[stage.unit] || '') : '') +
            (stage.error ? ' · ' + stage.error : '')))
        }
        card.append(stages)
      }
    }
    if (source.error) {
      const error = node('p', source.error, 'source-error')
      error.setAttribute('role', 'alert')
      card.append(error)
    }
    if (source.scanRequested) card.append(node('p', '已请求检查新增照片；当前快照安全完成后再检查一轮', 'source-progress'))
    for (const issue of source.sourceIssues || []) card.append(node('p', issue.path + ' · ' + issue.message, 'source-error'))
    for (const tail of source.historyTails || []) {
      card.append(node('p', 'history-tail · ' + (tail.reason || '准备历史尾组对比') +
        ' · 打开本组任一裁剪 → 鸟体对比，可查看历史缓存与新增照片。', 'source-progress'))
    }
    const buttons = node('div', undefined, 'source-buttons')
    const scan = node('button', ['error', 'failed', 'missing', 'paused'].includes(source.status) ? '恢复 / 检查新增照片' : '检查新增照片')
    scan.type = 'button'
    scan.addEventListener('click', () => action('rescan', source.id))
    const remove = node('button', '移除（仅解绑）')
    remove.type = 'button'
    remove.title = '仅从本机照片库解绑，不删除原图或处理结果'
    remove.addEventListener('click', () => action('removeSource', source.id))
    buttons.append(scan, remove)
    card.append(buttons)
    $('source-list').append(card)
  }
}

function updateSharing() {
  const shared = $('service-mode').value !== 'local'
  $('share-risk').hidden = !shared
  $('share-confirm-field').hidden = !shared
  $('lan-ip-field').hidden = !shared
}

function renderService(service) {
  if (!serviceDirty) {
    $('service-mode').value = modes.includes(service.mode) ? service.mode : 'local'
    $('service-port').value = Number.isInteger(service.port) ? service.port : 5274
    updateSharing()
  }
  $('service-status').textContent = '服务：' + (labels[service.status] || service.status) + (service.error ? ' · ' + service.error : '')
  $('service-status').setAttribute('role', service.error ? 'alert' : 'status')
  $('apply-service').textContent = ['ready', 'running'].includes(service.status) ? '应用网络设置' : '应用并启动服务'
  const suggestion = service.suggestedPort
  $('apply-suggested-port').hidden = !Number.isInteger(suggestion) || suggestion < 1024 || suggestion > 65535
  $('apply-suggested-port').textContent = '端口冲突？应用建议端口 ' + suggestion
  $('service-addresses').replaceChildren()
  const ready = ['ready', 'running'].includes(service.status)
  if (ready) {
    if (service.localUrl) $('service-addresses').append(node('p', '本机：' + service.localUrl))
    if (service.mode !== 'local') for (const url of service.lanUrls || []) $('service-addresses').append(node('p', '共享：' + url))
  }
  const url = ready ? localGalleryUrl(service.localUrl) : null
  $('reload-gallery').disabled = !url
  if (url) {
    if (frameUrl !== url) {
      frameUrl = url
      $('gallery-frame').hidden = true
      message('正在载入图库', '正在连接本机报告服务；若长时间未显示，请检查服务状态并重新加载图库。')
      $('gallery-frame').src = url
      if (location.protocol === 'file:') {
        let bridge = $('gallery-refresh-bridge')
        if (!bridge) {
          bridge = node('iframe')
          bridge.id = 'gallery-refresh-bridge'
          bridge.hidden = true
          bridge.title = '仅传递图库刷新通知'
          document.body.append(bridge)
        }
        bridge.src = new URL('/gallery-refresh-bridge', url).href
      }
    }
  } else {
    frameUrl = ''
    $('gallery-frame').removeAttribute('src')
    $('gallery-frame').hidden = true
    if (ready) message('图库地址无效', '主进程未提供合法的 127.0.0.1 本机图库地址；未打开其他地址。', true)
    else if (service.error) message('本机服务未能就绪', service.error + '。请展开网络服务设置；若端口被占用，可明确应用建议端口。', true)
    else if (!current.sources.length) message('欢迎使用 BirdShot', '添加来源目录或接入旧批次。图库需要本机服务就绪后显示，可在网络服务设置中启动。')
    else message('本机服务尚未就绪', '照片库已保留。请等待服务启动，或展开网络服务设置应用并启动服务。')
  }
}

function gallerySourcesChanged(previous, sources) {
  const known = new Map(previous.map(source => [source.id, source]))
  return known.size !== sources.length || sources.some(source => {
    const before = known.get(source.id)
    return !before || source.status === 'completed' && (before.status !== 'completed' || before.job !== source.job)
  })
}

function reloadGallery() {
  if (!frameUrl) return
  $('gallery-frame').hidden = true
  message('正在重新载入图库', '仅重新读取已有图库，不会重新推理或改写技术排序。')
  $('gallery-frame').src = frameUrl
}

function refreshGalleryIndex() {
  if (!frameUrl) return
  const frame = $('gallery-frame')
  if (frame.getAttribute('src') !== frameUrl) return
  const target = location.protocol === 'file:' ? $('gallery-refresh-bridge') : frame
  target?.contentWindow?.postMessage({ type: 'birdshot:refresh-index' }, new URL(frameUrl).origin)
}

function render(next) {
  if (!next || next.version !== 1 || !Array.isArray(next.sources) || !next.runtime || !next.service) throw new Error('桌面库状态格式无效')
  const previous = current
  const previousFrameUrl = frameUrl
  current = next
  const startup = next.startup
  const startupPhases = { config: '读取配置', sources: '检查照片来源', models: '校验本地模型',
    'review-state': '读取审阅状态', library: '读取已有批次', 'library-groups': '整理连拍组',
    'library-publish': '发布已有图库', review: '建立审阅索引', 'review-groups': '检查审阅连拍组',
    'review-photos': '检查原片关联', 'review-publish': '发布审阅索引', service: '启动本机图库服务',
    ready: '启动完成' }
  $('startup-status').textContent = startup ? [startupPhases[startup.phase] || startup.phase, startup.message, startup.warning, startup.error,
    Number.isInteger(startup.processed) && Number.isInteger(startup.total) ? `${startup.processed} / ${startup.total}` : ''].filter(Boolean).join(' · ') : ''
  $('startup-status').hidden = !startup || startup.status === 'ready'
  $('startup-status').setAttribute('role', startup?.status === 'error' ? 'alert' : 'status')
  $('retry-startup').disabled = startup?.status === 'initializing'
  $('runtime-status').textContent = next.runtime.ready ? '本地运行环境已就绪' :
    startup?.status === 'initializing' ? '本地运行环境正在初始化，可查看阶段或刷新状态' : '本地运行环境尚未就绪；请查看具体错误与日志'
  $('output-root').textContent = next.outputRoot || '尚未选择输出目录；请使用系统选择器设置。'
  $('model-list').replaceChildren()
  for (const model of next.runtime.models || []) {
    $('model-list').append(node('li', typeof model === 'string' ? model : (model.name || model.id || '模型') + ' · ' + (model.error || labels[model.status] || (model.ready ? '已就绪' : '待检查'))))
  }
  if (!$('model-list').childElementCount) $('model-list').append(node('li', '尚无模型状态；本地处理前需准备运行环境。'))
  renderSources(next.sources)
  renderService(next.service)
  window.dispatchEvent(new CustomEvent('birdshot-state', { detail: next }))
  if (previous && frameUrl === previousFrameUrl &&
      (previous.viewRevision !== next.viewRevision || gallerySourcesChanged(previous.sources, next.sources) ||
       next.sources.some(source => source.progress?.publishedRevision !== previous.sources.find(old => old.id === source.id)?.progress?.publishedRevision))) refreshGalleryIndex()
  setBusy(busy)
}

async function configure(port) {
  const mode = $('service-mode').value
  if (!modes.includes(mode) || !Number.isInteger(port) || port < 1024 || port > 65535) {
    showError('请输入 1024–65535 之间的整数端口。')
    return
  }
  if (mode !== 'local' && !$('share-confirm').checked) {
    showError('开启共享前，请阅读风险说明并勾选确认。')
    return
  }
  const lanIp = $('service-lan-ip').value.trim()
  if (mode !== 'local' && lanIp && (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(lanIp) || lanIp.split('.').some(part => Number(part) > 255))) {
    showError('指定地址应为本机的局域网 IPv4；也可留空由服务检测。')
    return
  }
  serviceDirty = false
  await action('configureService', { mode, port, ...(mode !== 'local' && lanIp ? { lanIp } : {}) })
  $('share-confirm').checked = false
}

$('choose-source').addEventListener('click', () => action('chooseSource'))
$('attach-existing').addEventListener('click', () => action('attachExisting'))
$('choose-output').addEventListener('click', () => action('chooseOutput'))
$('refresh-state').addEventListener('click', () => action('getState'))
$('retry-startup').addEventListener('click', () => action('retryStartup'))
$('service-form').addEventListener('submit', event => { event.preventDefault(); configure(Number($('service-port').value)) })
$('apply-suggested-port').addEventListener('click', () => {
  if (current?.service.suggestedPort) {
    $('service-port').value = current.service.suggestedPort
    configure(current.service.suggestedPort)
  }
})
$('service-form').addEventListener('input', () => { serviceDirty = true })
$('service-mode').addEventListener('change', () => { serviceDirty = true; $('share-confirm').checked = false; updateSharing() })
$('reload-gallery').addEventListener('click', reloadGallery)
$('gallery-frame').addEventListener('load', () => {
  if (!frameUrl || $('gallery-frame').getAttribute('src') !== frameUrl) return
  $('gallery-message').hidden = true
  $('gallery-frame').hidden = false
})
$('gallery-frame').addEventListener('error', () => {
  $('gallery-frame').hidden = true
  message('图库载入失败', '请检查网络服务状态，然后点击“重新加载图库”。', true)
})
window.addEventListener('pagehide', () => unsubscribe?.())

if (!api) {
  setBusy(false)
  showError('桌面管理接口不可用。请从 BirdShot 桌面应用打开；共享图库不提供管理能力。')
  message('未连接桌面应用', '无法访问系统目录选择器或本机照片库。请从桌面应用重新打开。', true)
  $('sources-empty').textContent = '未连接桌面管理接口。'
  $('runtime-status').textContent = '桌面接口不可用'
} else {
  try {
    unsubscribe = api.onState(next => {
      stateEvents += 1
      try { render(next) } catch (error) { showError(errorText(error)) }
    })
    action('getState')
  } catch (error) {
    showError('桌面状态订阅失败：' + errorText(error))
    action('getState')
  }
}
