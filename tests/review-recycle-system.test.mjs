// SPDX-License-Identifier: AGPL-3.0-only
// One owned temporary file only; never calls trashItem on a library/photo directory.
import assert from 'node:assert/strict'
import { _electron } from 'playwright'
import { mkdtemp, writeFile, readFile, access } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { randomUUID, createHash } from 'node:crypto'
import path from 'node:path'
import os from 'node:os'
import { fileURLToPath } from 'node:url'

assert.equal(process.platform, 'win32')
assert.equal(process.env.BIRDSHOT_TEST_RECYCLE_FIXTURE, '1', 'Opt in to recycling one owned temporary fixture')
const recovering = process.env.BIRDSHOT_RECOVER_OWNED_FIXTURE
if (recovering) assert.match(recovering, /^birdshot-public-fixture-[a-f0-9-]{36}\.jpg$/)
const root = recovering ? path.resolve(process.env.BIRDSHOT_RECOVER_OWNED_FOLDER || '') :
  await mkdtemp(path.join(os.tmpdir(), 'BirdShot-recycle-fixture-'))
if (recovering) assert.ok(path.dirname(root) === os.tmpdir() && path.basename(root).startsWith('BirdShot-recycle-fixture-'))
const name = recovering || 'birdshot-public-fixture-' + randomUUID() + '.jpg'
const file = path.join(root, name), content = Buffer.from('Controlled disposable fixture; no personal photo data')
if (!recovering) await writeFile(file, content)
const electron = path.resolve('node_modules', 'electron', 'dist', 'electron.exe')
const entry = fileURLToPath(new URL('./recycle-fixture-main.cjs', import.meta.url))
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const app = await _electron.launch({ executablePath: electron, args: [entry], env, timeout: 30000 })
let trashed = false
try {
  await app.firstWindow()
  if (!recovering) await app.evaluate(({ shell }, filename) => shell.trashItem(filename), file)
  trashed = true
  await assert.rejects(access(file))
  const escapedName = name.replaceAll("'", "''"), escapedFolder = root.replaceAll("'", "''")
  const script = `
$shell=New-Object -ComObject Shell.Application
$bin=$shell.NameSpace(10)
$matches=@($bin.Items() | Where-Object {
 ($_.Name -eq '${escapedName}' -or $_.Name -eq '${escapedName.replace(/\.jpg$/, '')}') -and $_.ExtendedProperty('System.Recycle.DeletedFrom') -eq '${escapedFolder}'
})
if($matches.Count -ne 1){throw 'The one owned recycled fixture could not be identified uniquely'}
$matches[0].InvokeVerb('undelete')
$until=(Get-Date).AddSeconds(15)
while(!(Test-Path -LiteralPath '${escapedFolder}\\${escapedName}') -and (Get-Date) -lt $until){Start-Sleep -Milliseconds 200}
if(!(Test-Path -LiteralPath '${escapedFolder}\\${escapedName}')){throw 'The owned fixture was recycled, but automatic restore did not complete'}
Write-Output 'OWNED_RECYCLE_FIXTURE_RESTORED'
`
  const result = await new Promise((resolve, reject) => {
    const child = spawn(path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
      ['-NoProfile', '-Command', script], { shell: false, windowsHide: true })
    let stdout = '', stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('exit', code => resolve({ code, stdout, stderr }))
  })
  assert.equal(result.code, 0, result.stderr)
  assert.match(result.stdout, /OWNED_RECYCLE_FIXTURE_RESTORED/)
  assert.deepEqual(await readFile(file), content)
  console.log(JSON.stringify({ actualSystemRecycleVerified: true, actualSystemRestoreVerified: true,
    restoredSha256: createHash('sha256').update(await readFile(file)).digest('hex'),
    privatePhotosReadOrRecycled: false }))
} finally {
  await app.close()
  if (trashed) {
    try { await access(file) } catch { console.error('Only the owned temporary fixture remains in the OS recycle bin:', name) }
  }
}
