// SPDX-License-Identifier: AGPL-3.0-only
// Explicit isolated install/uninstall test. Never use an existing user installation.
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, mkdir, readFile, writeFile, access } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { _electron } from 'playwright'
import path from 'node:path'
import os from 'node:os'
import { performance } from 'node:perf_hooks'

assert.equal(process.platform, 'win32')
assert.equal(process.env.BIRDSHOT_TEST_INSTALLER, '1')
const root = path.resolve('.')
const version = JSON.parse(await readFile(path.join(root, 'package.json'))).desktopVersion
const installer = path.join(root, 'desktop-artifacts', `BirdShot-${version}-Setup-x64.exe`)
const testRoot = await mkdtemp(path.join(os.tmpdir(), 'BirdShot 安装 性能 '))
const installation = path.join(testRoot, '当前用户 安装目录')
const data = path.join(testRoot, '持久 用户库')
await mkdir(data)
const marker = path.join(data, 'USER_LIBRARY_DO_NOT_DELETE.txt')
await writeFile(marker, 'User data must survive install, upgrade and uninstall')
const systemRoot = process.env.SystemRoot
const powershell = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
const key = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{7FA5B648-94A8-4A1C-A9BE-E812A611D83D}_is1'
async function run(executable, args, extra = {}) {
  const started = performance.now()
  const outcome = await new Promise((resolve, reject) => {
    const child = spawn(executable, args, { shell: false, windowsHide: true, cwd: root, ...extra })
    let stdout = '', stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('exit', code => resolve({ code, stdout, stderr }))
  })
  return { ...outcome, seconds: (performance.now() - started) / 1000 }
}
const registry = await run(powershell, ['-NoProfile', '-Command',
  `if(Test-Path -LiteralPath '${key}'){exit 9}; $admin=(New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);if($admin){exit 10}`])
assert.equal(registry.code, 0, 'Do not test uninstall over an existing BirdShot installation or in an elevated account')
const setupArgs = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', `/DIR=${installation}`]
if (process.env.BIRDSHOT_INSTALL_LOG !== '0') setupArgs.push(`/LOG=${path.join(testRoot, 'install.log')}`)
const proof = { version, currentUserNonAdministrator: true, isolatedInstallation: true,
  sameMachineLocalVolume: true, diagnosticLogEnabled: process.env.BIRDSHOT_INSTALL_LOG !== '0',
  cacheNote: 'One run each; Windows file caches not cleared, no antivirus/policy changes' }
let installed = false
try {
  const first = await run(installer, setupArgs)
  assert.equal(first.code, 0, first.stderr)
  installed = true
  proof.coldInstallSeconds = Number(first.seconds.toFixed(3))
  await access(path.join(installation, 'BirdShot.exe'))
  await access(path.join(installation, 'unins000.exe'))
  const registered = await run(powershell, ['-NoProfile', '-Command',
    `$entry=Get-ItemProperty -LiteralPath '${key}';Write-Output ($entry|Select-Object DisplayName,DisplayVersion|ConvertTo-Json -Compress);if($entry.DisplayName -ne 'BirdShot' -or $entry.DisplayVersion -ne '${version}'){exit 1};if(!(Test-Path -LiteralPath (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\BirdShot\\BirdShot.lnk'))){exit 2};Write-Output 'PER_USER_UNINSTALL_AND_START_MENU_OK'`])
  assert.equal(registered.code, 0, registered.stderr + registered.stdout)
  proof.startMenuAndStandardUninstallVerified = true
  console.log(JSON.stringify({ phase: 'installed', seconds: proof.coldInstallSeconds, root: testRoot }))
  const env = { ...process.env, BIRDSHOT_TEST_PACKAGED: '1', BIRDSHOT_TEST_INSTALLED: '1',
    BIRDSHOT_DESKTOP_DIRECTORY: installation, BIRDSHOT_USER_DATA: data }
  env.BIRDSHOT_VALIDATION_ROOT = await mkdtemp(path.join(testRoot, '真鸟 流程 '))
  delete env.BIRDSHOT_REUSE_VALIDATION
  const flow = await run(process.execPath, [path.join(root, 'tests', 'desktop-packaged.test.mjs')], { env })
  assert.equal(flow.code, 0, flow.stderr + '\n' + flow.stdout)
  proof.applicationFlow = flow.stdout
  const libraryFile = path.join(env.BIRDSHOT_VALIDATION_ROOT, '干净 用户配置', 'desktop-library.json')
  const libraryBefore = await readFile(libraryFile)
  const originalFiles = [path.join(env.BIRDSHOT_VALIDATION_ROOT, '来源甲', '同名 照片', '连续 鸟 1.jpg'),
    path.join(env.BIRDSHOT_VALIDATION_ROOT, '来源甲', '同名 照片', '连续 鸟 2.jpg'),
    path.join(env.BIRDSHOT_VALIDATION_ROOT, '来源乙', '同名 照片', '公开 双鸟.jpg')]
  const digest = value => createHash('sha256').update(value).digest('hex')
  const originalsBefore = await Promise.all(originalFiles.map(async file => digest(await readFile(file))))
  if (process.env.BIRDSHOT_LEGACY_VALIDATION_DATA) {
    const legacy = process.env.BIRDSHOT_LEGACY_VALIDATION_DATA
    const before = await readFile(path.join(legacy, 'desktop-library.json'))
    const previous = JSON.parse(before)
    assert.equal(previous.version, 1)
    const app = await _electron.launch({ executablePath: path.join(installation, 'BirdShot.exe'),
      env: { ...process.env, PATH: path.join(systemRoot, 'System32'), BIRDSHOT_OFFLINE: '1',
        BIRDSHOT_USER_DATA: legacy }, timeout: 90000 })
    try {
      const page = await app.firstWindow()
      await page.waitForFunction(() => !!window.birdshotDesktop)
      const state = await page.evaluate(() => window.birdshotDesktop.getState())
      assert.deepEqual(state.sources.map(source => [source.id, source.path, source.job]),
        previous.sources.map(source => [source.id, source.path, source.job]))
      assert.equal(state.outputRoot, previous.outputRoot)
      assert.equal(state.service.port, previous.service.port)
      assert.equal(state.service.mode, previous.service.mode)
      const updated = await page.evaluate(() => window.birdshotDesktop.checkForUpdates())
      assert.ok(['none', 'upToDate', 'available', 'error'].includes(updated.status))
      assert.equal(updated.automaticInstallation, false)
    } finally {
      await app.evaluate(({ dialog }) => { dialog.showMessageBoxSync = () => 0 })
      await app.close()
    }
    proof.schema1LegacySourcesOutputAndServicePreserved = true
  }
  const upgraded = await run(installer, setupArgs)
  assert.equal(upgraded.code, 0, upgraded.stderr)
  proof.reinstallSeconds = Number(upgraded.seconds.toFixed(3))
  assert.equal(await readFile(marker, 'utf8'), 'User data must survive install, upgrade and uninstall')
  const extraUserFile = path.join(installation, 'user-created-sentinel.txt')
  await writeFile(extraUserFile, 'Not installed by BirdShot')
  const removed = await run(path.join(installation, 'unins000.exe'), ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
    `/LOG=${path.join(testRoot, 'uninstall.log')}`])
  assert.equal(removed.code, 0, removed.stderr)
  installed = false
  proof.uninstallSeconds = Number(removed.seconds.toFixed(3))
  await assert.rejects(access(path.join(installation, 'BirdShot.exe')))
  assert.equal(await readFile(marker, 'utf8'), 'User data must survive install, upgrade and uninstall')
  assert.equal(await readFile(extraUserFile, 'utf8'), 'Not installed by BirdShot')
  assert.deepEqual(await readFile(libraryFile), libraryBefore, 'Uninstall must preserve actual populated library state')
  assert.deepEqual(await Promise.all(originalFiles.map(async file => digest(await readFile(file)))), originalsBefore)
  const removedRegistry = await run(powershell, ['-NoProfile', '-Command', `if(Test-Path -LiteralPath '${key}'){exit 1}`])
  assert.equal(removedRegistry.code, 0)
  proof.uninstalledAppOnly = true
  proof.userDataAndUnregisteredFileRetained = true
  proof.passed = true
  await writeFile(path.join(root, 'desktop-artifacts', `BirdShot-${version}-installer-validation.json`), JSON.stringify(proof, null, 2))
  console.log(JSON.stringify(proof))
} finally {
  if (installed) {
    const result = await run(path.join(installation, 'unins000.exe'), ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
    if (result.code !== 0) console.error('Isolated test installation remains:', installation)
  }
}
