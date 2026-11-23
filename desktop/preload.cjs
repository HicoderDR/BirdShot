// SPDX-License-Identifier: AGPL-3.0-only
const { contextBridge, ipcRenderer } = require('electron')

if (process.isMainFrame) {
  contextBridge.exposeInMainWorld('birdshotDesktop', Object.freeze({
    getState: () => ipcRenderer.invoke('birdshot:state'),
    checkForUpdates: () => ipcRenderer.invoke('birdshot:check-updates'),
    chooseSource: () => ipcRenderer.invoke('birdshot:choose-source'),
    attachExisting: () => ipcRenderer.invoke('birdshot:attach-existing'),
    chooseOutput: () => ipcRenderer.invoke('birdshot:choose-output'),
    removeSource: id => ipcRenderer.invoke('birdshot:remove-source', id),
    rescan: id => ipcRenderer.invoke('birdshot:rescan', id),
    configureService: value => ipcRenderer.invoke('birdshot:configure-service', value),
    onState: callback => {
      const handler = (_event, state) => callback(state)
      ipcRenderer.on('birdshot:state-updated', handler)
      return () => ipcRenderer.removeListener('birdshot:state-updated', handler)
    },
  }))
}
