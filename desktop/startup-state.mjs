// SPDX-License-Identifier: AGPL-3.0-only
// Transport readiness is not model/service readiness, and a warning is not terminal.
export class StartupState {
  constructor(instanceId) { this.instanceId = instanceId; this.reset() }
  reset(previous) {
    this.connected = false
    this.state = { version: 1, instanceId: this.instanceId, sources: previous?.sources || [],
      outputRoot: previous?.outputRoot || '',
      runtime: { ready: false, models: (previous?.runtime?.models || []).map(model => ({ ...model, ready: false, verified: false })) },
      service: { ...(previous?.service || { port: 5274, mode: 'local' }), status: 'starting', localUrl: null, lanUrls: [] },
      startup: { status: 'initializing', phase: '启动包内运行时', message: '正在连接本机控制通道；尚未开始新的照片处理。' } }
    return this.state
  }
  accept(message) {
    if (!message || !['controller-ready', 'ready', 'state'].includes(message.event) ||
        message.state?.version !== 1 || message.state.instanceId !== this.instanceId ||
        ['controller-ready', 'ready'].includes(message.event) && message.instanceId !== this.instanceId) return false
    if (['controller-ready', 'ready'].includes(message.event)) this.connected = true
    this.state = message.state
    return true
  }
  warn() {
    if (this.state.startup?.status !== 'initializing') return this.state
    this.state = { ...this.state, startup: { ...this.state.startup,
      warning: '初始化耗时较长，仍在检查本机资源/读取旧图库；状态可继续查询，不会重复启动后台。' } }
    return this.state
  }
  fail(message) {
    this.connected = false
    this.state = { ...this.state, runtime: { ...this.state.runtime, ready: false },
      service: { ...this.state.service, status: 'error', error: message, localUrl: null, lanUrls: [] },
      startup: { status: 'error', phase: '本机运行时启动失败', error: message } }
    return this.state
  }
}
