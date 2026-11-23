// SPDX-License-Identifier: AGPL-3.0-only
import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { randomUUID } from 'node:crypto'
import { appendFile, mkdir, readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { trustedSender, externalReference, networkSettings } from './security.mjs'
import { checkUpdates } from './updates.mjs'

const here = path.dirname(fileURLToPath(import.meta.url))
const page = path.join(here, 'renderer', 'index.html')
const pageUrl = pathToFileURL(page).href
let window, backend, lastState, closing = false, sequence = 0, readyResolve, readyReject
const pending = new Map()
const instanceId = randomUUID()
const ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject })
ready.catch(() => {})
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
  if (message.event === 'ready') {
    if (message.instanceId !== instanceId) { readyReject(new Error('本地运行时实例身份不匹配')); return }
    lastState = message.state
    readyResolve(message.state)
  } else if (message.event === 'state') {
    lastState = message.state
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
  const resource = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.resolve(here, '..')
  const python = app.isPackaged ? path.join(process.resourcesPath, 'runtime', 'python', 'python.exe') :
    process.env.BIRDSHOT_PYTHON || path.join(resource, '.tools', 'venv', 'Scripts', 'python.exe')
  const node = app.isPackaged ? path.join(process.resourcesPath, 'runtime', 'node', 'node.exe') :
    process.env.BIRDSHOT_NODE || path.join(resource, '.tools', 'node-v24.11.1-win-x64', 'node.exe')
  const data = app.getPath('userData')
  await mkdir(data, { recursive: true })
  const log = path.join(data, 'runtime.log')
  if (!existsSync(python) || !existsSync(node)) throw new Error('包内Python/Node运行时缺失；请重新解压完整便携包')
  const env = { ...process.env, PYTHONNOUSERSITE: '1', PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1',
    BIRDSHOT_MODEL_CACHE: app.isPackaged ? path.join(resource, 'models') : path.join(resource, '.tools', 'species-models') }
  // Recognition never needs these inherited service/account credentials.
  for (const key of Object.keys(env)) {
    if (/^(?:PYTHONHOME|PYTHONPATH|VIRTUAL_ENV|DONGNIAO_API_KEY|GH_TOKEN|GITHUB_TOKEN|COPILOT_GH_ACCOUNT_|HF_TOKEN|HUGGING_FACE_HUB_TOKEN|HUGGINGFACE_HUB_TOKEN|WANDB_API_KEY)/i.test(key)) delete env[key]
  }
  backend = spawn(python, [path.join(resource, 'scripts', 'desktop_backend.py'), '--data', data,
    '--resources', resource, '--node', node, '--instance', instanceId],
  { shell: false, windowsHide: true, cwd: data, env, stdio: ['pipe', 'pipe', 'pipe'] })
  const lines = createInterface({ input: backend.stdout })
  lines.on('line', line => {
    try { receive(JSON.parse(line)) }
    catch { appendFile(log, '运行时协议错误；请检查此包是否完整。\n').catch(() => {}) }
  })
  backend.stderr.on('data', data => appendFile(log, data).catch(() => {}))
  backend.on('error', error => readyReject(new Error('无法启动包内Python：' + error.message)))
  backend.on('exit', (code, signal) => {
    const error = new Error('本地运行时已退出（' + String(code ?? signal) + '），请重新打开App并检查日志')
    readyReject(error)
    for (const request of pending.values()) { clearTimeout(request.timer); request.reject(error) }
    pending.clear()
    if (!closing) receive({ event: 'state', state: { ...lastState, runtime: { ready: false, error: error.message },
      service: { ...lastState?.service, status: 'error', error: error.message } } })
  })
  const timeout = setTimeout(() => readyReject(new Error('本地运行时初始化超时；模型校验或启动失败，请查看日志')), 60000)
  try { await ready } finally { clearTimeout(timeout) }
}

function handler(channel, callback, requireRuntime = true) {
  ipcMain.handle(channel, async (event, ...args) => {
    if (!window || !trustedSender(event, window.webContents, pageUrl)) throw new Error('此操作仅限当前桌面管理窗口')
    if (requireRuntime) await ready
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
  handler('birdshot:check-updates', async () => {
    const version = app.isPackaged ? app.getVersion() :
      JSON.parse(await readFile(path.resolve(here, '..', 'package.json'))).desktopVersion
    return checkUpdates(version, fetch, process.env.BIRDSHOT_OFFLINE === '1')
  }, false)
  handler('birdshot:state', () => command('state'))
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
  window.on('close', event => {
    if (closing) return
    event.preventDefault()
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
    lastState = { runtime: { ready: false, error: error.message }, sources: [], service: { status: 'error', error: error.message } }
    window.webContents.send('birdshot:state-updated', lastState)
    dialog.showErrorBox('BirdShot本地运行时启动失败', error.message)
  }
}).catch(error => { dialog.showErrorBox('BirdShot无法启动', error.message); app.quit() })
app.on('before-quit', event => {
  if (!closing && backend?.exitCode === null) { event.preventDefault(); closing = true; stop().finally(() => app.quit()) }
})
app.on('window-all-closed', () => { if (!closing) { closing = true; stop().finally(() => app.quit()) } })
