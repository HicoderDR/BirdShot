// SPDX-License-Identifier: AGPL-3.0-only
// Isolated test host, not a BirdShot production entry point.
const { app, BrowserWindow } = require('electron')
app.whenReady().then(() => {
  const window = new BrowserWindow({ show: false, webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true } })
  return window.loadURL('data:text/html,<title>BirdShot recycle fixture</title>')
})
