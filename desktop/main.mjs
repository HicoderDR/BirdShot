// SPDX-License-Identifier: AGPL-3.0-only
import { app, BrowserWindow, dialog, ipcMain, shell, Menu } from 'electron'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { randomUUID } from 'node:crypto'
import { appendFile, mkdir, readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { trustedSender, externalReference, networkSettings } from './security.mjs'
import { checkUpdates } from './updates.mjs'
import { createReviewNative } from './review-native.mjs'
import { StartupState } from './startup-state.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const page = path.join(here, 'renderer', 'index.html')
const pageUrl = pathToFileURL(page).href
let window, backend, closing = false, sequence = 0, startupTimer
let closeReviewRequest
let reviewMode = false, previousPageZoom = 1
function configureMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    { role: 'fileMenu' }, { role: 'editMenu' },
    { label: '视图', submenu: [{ role: 'reload' }, { role: 'forceReload' }, { role: 'toggleDevTools' },
      { type: 'separator' }, ...(reviewMode ? [{ label: '图片缩放：Ctrl +/-', enabled: false }] :
        [{ role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }]), { role: 'togglefullscreen' }] },
    { role: 'windowMenu' },
  ]))
}
const pending = new Map()
const instanceId = randomUUID()
const startup = new StartupState(instanceId)
let lastState = startup.state
const reviewNative = createReviewNative({
  command,
  busy: () => lastState?.sources?.some(source => ['processing', 'queued'].includes(source.status)) === true,
  trashItem: filename => shell.trashItem(filename),
  progress: update => { if (window && !window.isDestroyed()) window.webContents.send('birdshot:review-progress', update) },
  confirm: async plan => {
    const raw = plan.rawCount || 0
    const choice = await dialog.showMessageBox(window, { type: 'warning',
      title: '将未保留的原片移到系统回收站', buttons: ['取消', '确认移到回收站'], defaultId: 0, cancelId: 0,
      message: `本次将回收 ${plan.photoCount || 0} 张主照片${raw ? `及 ${raw} 个配对RAW` : '，配对RAW不动'}。`,
      detail: `仅限此次清单中的人工已遴选组；保留 ${plan.keepCount || 0} 张。原文件可在系统回收站还原，无法回收的项会报告失败，不永久删除。\n\n` +
        plan.files.slice(0, 15).map(file => file.name).join('\n') + (plan.files.length > 15 ? '\n…其余请参照应用清单' : '') })
    if (choice.response !== 1) return false
    if (plan.includeZero) {
      const zero = await dialog.showMessageBox(window, { type: 'warning', title: '零保留组额外确认',
        buttons: ['取消', '我确认本次也回收零保留组'], defaultId: 0, cancelId: 0,
        message: '本次包含没有保留任何照片的人工组，组内所有列定主照片会进入回收站。',
        detail: '此确认只适用当前清单，不会被“记住退出选择”绕过。' })
      if (zero.response !== 1) return false
    }
    return true
  },
})
if (process.env.BIRDSHOT_USER_DATA) app.setPath('userData', path.resolve(process.env.BIRDSHOT_USER_DATA))
app.setName('BirdShot')
if (!app.requestSingleInstanceLock()) app.exit(0)
app.on('second-instance', () => { if (window && !window.isDestroyed()) { window.show(); window.focus() } })

function command(action, params = {}) {
  if (!backend || backend.exitCode !== null || !backend.stdin.writable) return Promise.reject(new Error('本地运行时未启动'))
  const id = String(++sequence)
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('本地操作超时；请检查运行状态与日志')) }, 120000)
    pending.set(id, { resolve, reject, timer })
    backend.stdin.write(JSON.stringify({ id, action, params }) + '\n', error => {
      if (error) { clearTimeout(timer); pending.delete(id); reject(error) }
    })
  })
}

function receive(message) {
  if (['controller-ready', 'ready', 'state'].includes(message.event)) {
    if (!startup.accept(message)) { console.error('Rejected a stale or mismatched backend state event'); return }
    lastState = startup.state
    if (startup.connected) clearTimeout(startupTimer)
  } else if (message.id) {
    const request = pending.get(String(message.id))
    if (request) {
      clearTimeout(request.timer); pending.delete(String(message.id))
      if (message.ok) {
        if (message.result?.version === 1 && message.result.instanceId === instanceId) lastState = message.result
        request.resolve(message.result)
      }
      else request.reject(new Error(typeof message.error === 'string' ? message.error : message.error?.message || '本地操作失败'))
    }
  }
  if (lastState && window && !window.isDestroyed()) window.webContents.send('birdshot:state-updated', lastState)
}

async function launchBackend() {
  if (backend && backend.exitCode === null) return
  clearTimeout(startupTimer)
  lastState = startup.reset(lastState)
  if (window && !window.isDestroyed()) window.webContents.send('birdshot:state-updated', lastState)
  const resource = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(here, '..')
  const python = app.isPackaged ? path.join(process.resourcesPath, 'runtime', 'python', 'python.exe') :
    process.env.BIRDSHOT_PYTHON || path.join(resource, '.tools', 'venv', 'Scripts', 'python.exe')
  const node = app.isPackaged ? path.join(process.resourcesPath, 'runtime', 'node', 'node.exe') :
    process.env.BIRDSHOT_NODE || path.join(resource, '.tools', 'node-v24.11.1-win-x64', 'node.exe')
  const data = app.getPath('userData')
  await mkdir(data, { recursive: true })
  const log = path.join(data, 'runtime.log')
  await appendFile(log, JSON.stringify({ event: 'desktop-startup', time: new Date().toISOString(), instanceId }) + '\n')
  if (!existsSync(python) || !existsSync(node)) throw new Error('包内Python/Node运行时缺失；请重新解压完整便携包')
  const env = { ...process.env, PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1',
    BIRDSHOT_MODEL_CACHE: app.isPackaged ? path.join(resource, 'models') : path.join(resource, '.tools', 'species-models') }
  // Recognition never needs these inherited service/account credentials.
  for (const key of Object.keys(env)) {
    if (/^(?:PYTHONHOME|PYTHONPATH|VIRTUAL_ENV|DONGNIAO_API_KEY|GH_TOKEN|GITHUB_TOKEN|COPILOT_GH_ACCOUNT_|HF_TOKEN|HUGGING_FACE_HUB_TOKEN|HUGGINGFACE_HUB_TOKEN|WANDB_API_KEY)/i.test(key)) delete env[key]
  }
  const child = spawn(python, [path.join(resource, 'scripts', 'desktop_backend.py'), '--data', data,
    '--resources', resource, '--node', node, '--instance', instanceId],
  { shell: false, windowsHide: true, cwd: data, env, stdio: ['pipe', 'pipe', 'pipe'] })
  backend = child
  const lines = createInterface({ input: child.stdout })
  lines.on('line', line => {
    if (backend !== child) return
    try { receive(JSON.parse(line)) }
    catch { appendFile(log, '运行时协议错误；请检查此包是否完整。\n').catch(() => {}) }
  })
  child.stderr.on('data', data => appendFile(log, data).catch(() => {}))
  child.on('error', error => {
    if (backend !== child) return
    backend = undefined
    clearTimeout(startupTimer)
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(error) }
    pending.clear()
    lastState = startup.fail('无法启动包内Python：' + error.message)
    window?.webContents.send('birdshot:state-updated', lastState)
    appendFile(log, JSON.stringify({ event: 'spawn-error', message: error.message }) + '\n').catch(console.error)
  })
  child.on('exit', (code, signal) => {
    if (backend !== child) return
    const error = new Error('本地运行时已退出（' + String(code ?? signal) + '），请重新打开App并检查日志')
    clearTimeout(startupTimer)
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(error) }
    pending.clear()
    if (!closing) {
      lastState = startup.fail(error.message)
      window?.webContents.send('birdshot:state-updated', lastState)
      appendFile(log, JSON.stringify({ event: 'backend-exit', code, signal }) + '\n').catch(console.error)
    }
  })
  startupTimer = setTimeout(() => {
    lastState = startup.warn()
    window?.webContents.send('birdshot:state-updated', lastState)
    appendFile(log, JSON.stringify({ event: 'startup-slow', terminal: false }) + '\n').catch(console.error)
  }, 60000)
}

function handler(channel, callback, requireRuntime = true) {
  ipcMain.handle(channel, async (event, ...args) => {
    if (!window || !trustedSender(event, window.webContents, pageUrl)) throw new Error('此操作仅限当前桌面管理窗口')
    if (requireRuntime && !startup.connected) throw new Error('本机控制通道仍在初始化，请查看启动阶段或重试启动；未重复运行任务')
    return callback(...args)
  })
}

async function choose(action, title) {
  const result = await dialog.showOpenDialog(window, { title, properties: ['openDirectory', 'dontAddToRecent'] })
  if (result.canceled || result.filePaths.length !== 1) return command('state')
  const selected = path.resolve(result.filePaths[0])
  await command(action, { path: selected })
  return command('state')
}

function registerIpc() {
  handler('birdshot:review-mode', value => {
    if (typeof value !== 'boolean') throw new Error('审阅模式无效')
    if (value === reviewMode) return
    reviewMode = value
    if (value) { previousPageZoom = window.webContents.getZoomFactor(); window.webContents.setZoomFactor(1) }
    else window.webContents.setZoomFactor(previousPageZoom)
    configureMenu()
  }, false)
  handler('birdshot:review-close-response', (id, allow) => {
    if (closeReviewRequest?.id !== id || typeof allow !== 'boolean') throw new Error('关闭确认已过期')
    clearTimeout(closeReviewRequest.timer)
    const resolve = closeReviewRequest.resolve; closeReviewRequest = null; resolve(allow)
  }, false)
  handler('birdshot:retry-startup', async () => {
    if (startup.connected) return command('retry_startup').then(() => command('state'))
    if (!backend || backend.exitCode !== null) {
      try { await launchBackend() }
      catch (error) {
        lastState = startup.fail(error.message)
        window?.webContents.send('birdshot:state-updated', lastState)
        throw error
      }
    }
    else lastState = startup.warn()
    return lastState
  }, false)
  handler('birdshot:review', (action, params) => reviewNative(action, params))
  handler('birdshot:check-updates', async () => {
    const version = app.isPackaged ? app.getVersion() :
      JSON.parse(await readFile(path.resolve(here, '..', 'package.json'))).desktopVersion
    return checkUpdates(version, fetch, process.env.BIRDSHOT_OFFLINE === '1')
  }, false)
  handler('birdshot:state', () => startup.connected ? command('state') : lastState, false)
  handler('birdshot:choose-source', () => choose('add_source', '添加照片文件夹（原片只读）'))
  handler('birdshot:attach-existing', () => choose('attach_job', '加载已有BirdShot批次输出（不重跑模型）'))
  handler('birdshot:choose-output', () => choose('set_output', '选择派生输出目录（不能在原片内）'))
  for (const [channel, action] of [['remove-source', 'remove_source'], ['rescan', 'rescan']]) {
    handler('birdshot:' + channel, id => {
      if (typeof id !== 'string' || id.length > 100 || !/^[A-Za-z0-9_-]+$/.test(id)) throw new Error('库标识无效')
      return command(action, { id }).then(() => command('state'))
    })
  }
  handler('birdshot:configure-service', value => command('configure_service', networkSettings(value)).then(() => command('state')))
}

function protect(contents) {
  contents.setWindowOpenHandler(({ url }) => {
    if (externalReference(url)) shell.openExternal(url).catch(error => dialog.showErrorBox('无法打开外部百科', error.message))
    return { action: 'deny' }
  })
  contents.on('will-navigate', (event, url) => { if (url !== pageUrl) event.preventDefault() })
  contents.on('will-attach-webview', event => event.preventDefault())
}
function confirmReviewClose() {
  return new Promise(resolve => {
    const id = randomUUID()
    const timer = setTimeout(() => {
      closeReviewRequest = null
      dialog.showErrorBox('尚未退出', '审阅页面未确认草稿状态。请先保存或明确放弃未保存改动，再退出。')
      resolve(false)
    }, 15000)
    closeReviewRequest = { id, timer, resolve }
    window.webContents.send('birdshot:review-close-request', id)
  })
}

async function stop() {
  if (!backend || backend.exitCode !== null) return
  const exited = new Promise(resolve => backend.once('exit', resolve))
  try { await Promise.race([command('shutdown'), new Promise(resolve => setTimeout(resolve, 5000))]) }
  catch (error) { console.error('Backend shutdown failed; terminating only its owned PID tree:', error.message) }
  if (backend.exitCode === null) await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000))])
  if (backend.exitCode === null) {
    if (process.platform === 'win32') {
      await new Promise(resolve => {
        const killer = spawn(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'),
          ['/PID', String(backend.pid), '/T', '/F'], { shell: false, windowsHide: true })
        killer.on('error', resolve); killer.on('exit', resolve)
      })
    } else backend.kill('SIGTERM')
  }
}

app.on('web-contents-created', (_event, contents) => {
  contents.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
})
app.whenReady().then(async () => {
  registerIpc()
  window = new BrowserWindow({ width: 1440, height: 980, minWidth: 850, minHeight: 620, title: 'BirdShot',
    webPreferences: { preload: path.join(here, 'preload.cjs'), contextIsolation: true, nodeIntegration: false,
      nodeIntegrationInSubFrames: false, sandbox: true, webSecurity: true, webviewTag: false } })
  protect(window.webContents)
  configureMenu()
  window.on('close', async event => {
    if (closing) return
    event.preventDefault()
    if (closeReviewRequest) return
    if (reviewNative.isRunning()) {
      dialog.showMessageBox(window, { type: 'info', message: '回收操作尚未结束。',
        detail: '请在审阅界面取消剩余项目或等待完成，再退出App。已完成的项目不会被重复操作。' })
      return
    }
    if (!await confirmReviewClose()) return
    if (lastState?.service?.mode !== 'local' && lastState?.service?.status === 'ready') {
      const choice = dialog.showMessageBoxSync(window, { type: 'question', buttons: ['退出并停止共享', '取消'],
        defaultId: 1, cancelId: 1, message: '关闭BirdShot将停止本App启动的共享服务，其他浏览器会断开。' })
      if (choice !== 0) return
    }
    closing = true
    stop().finally(() => app.quit())
  })
  await window.loadFile(page)
  if (closing) return
  try { await launchBackend() }
  catch (error) {
    lastState = startup.fail(error.message)
    window.webContents.send('birdshot:state-updated', lastState)
    dialog.showErrorBox('BirdShot本地运行时启动失败', error.message)
  }
}).catch(error => { dialog.showErrorBox('BirdShot无法启动', error.message); app.quit() })
app.on('before-quit', event => {
  if (!closing && window && !window.isDestroyed()) { event.preventDefault(); window.close() }
  else if (!closing && backend?.exitCode === null) { event.preventDefault(); closing = true; stop().finally(() => app.quit()) }
})
app.on('window-all-closed', () => { if (!closing) { closing = true; stop().finally(() => app.quit()) } })
