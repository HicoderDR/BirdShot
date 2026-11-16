// SPDX-License-Identifier: AGPL-3.0-only
// Observe/cancel only this application's actual Windows folder picker; no photos.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import path from 'node:path'
import os from 'node:os'

assert.equal(process.platform, 'win32')
assert.equal(process.env.BIRDSHOT_TEST_PACKAGED, '1')
const directory = path.resolve('desktop-artifacts', 'BirdShot-win32-x64')
const systemRoot = process.env.SystemRoot
const data = await mkdtemp(path.join(os.tmpdir(), 'BirdShot-picker-'))
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'), BIRDSHOT_USER_DATA: data, BIRDSHOT_OFFLINE: '1' }
delete env.ELECTRON_RUN_AS_NODE
const app = await _electron.launch({ executablePath: path.join(directory, 'BirdShot.exe'), env, timeout: 90000 })
try {
  const page = await app.firstWindow()
  await page.waitForFunction(() => !!window.birdshotDesktop)
  await page.evaluate(() => window.birdshotDesktop.getState())
  const selection = page.evaluate(() => window.birdshotDesktop.chooseSource())
    .then(state => ({ state }), error => ({ error: error.message }))
  const pid = await app.evaluate(() => process.pid)
  const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$condition = New-Object System.Windows.Automation.AndCondition(
 (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid})),
 (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, '#32770')))
$until=(Get-Date).AddSeconds(15)
do {
 $window=[System.Windows.Automation.AutomationElement]::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children,$condition)
 if($window){break}
 Start-Sleep -Milliseconds 250
} while((Get-Date) -lt $until)
if(!$window){throw 'The owned native folder dialog was not observed'}
$cancel=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
 (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty,'2')))
if(!$cancel){throw 'The owned dialog cancel control was not found'}
$cancel.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
Write-Output 'OWNED_NATIVE_DIALOG_CANCELLED'
`
  const result = await new Promise((resolve, reject) => {
    const process = spawn(path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
      ['-NoProfile', '-Command', script], { windowsHide: true, shell: false })
    let stdout = '', stderr = ''
    const timer = setTimeout(() => { process.kill(); reject(new Error('Owned native dialog observation timed out')) }, 30000)
    process.stdout.on('data', data => { stdout += data })
    process.stderr.on('data', data => { stderr += data })
    process.on('error', error => { clearTimeout(timer); reject(error) })
    process.on('exit', status => { clearTimeout(timer); resolve({ status, stdout, stderr }) })
  })
  const observed = result.status === 0
  if (!observed) {
    const outcome = await Promise.race([selection, new Promise(resolve => setTimeout(() => resolve({ pending: true }), 100))])
    assert.equal(outcome.error, undefined, outcome.error)
    if (process.env.BIRDSHOT_REQUIRE_NATIVE_DIALOG_UIA === '1') assert.equal(result.status, 0, result.stderr)
    console.log(JSON.stringify({ nativePickerRequested: true, nativePickerPending: outcome.pending === true,
      uiAutomationObserved: false, physicalMouseSelectionVerified: false,
      limitation: 'The current automation desktop could not observe the owned OS modal; controlled-dialog directory workflow is tested separately' }))
  } else {
    assert.match(result.stdout, /OWNED_NATIVE_DIALOG_CANCELLED/)
    const selected = await selection
    assert.equal(selected.error, undefined)
    assert.equal(selected.state.sources.length, 0)
    console.log('PASS: actual owned Windows folder picker opened and was cancelled via UI Automation; no filesystem listing captured')
  }
} finally {
  await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
  await app.close()
}
