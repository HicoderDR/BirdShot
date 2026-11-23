// SPDX-License-Identifier: AGPL-3.0-only
import { readFile, writeFile, stat, readdir } from 'node:fs/promises'
import { createReadStream, existsSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { performance } from 'node:perf_hooks'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const version = JSON.parse(await readFile(path.join(root, 'package.json'))).desktopVersion
const directory = path.join(root, 'desktop-artifacts', `BirdShot-${version}-win32-x64`)
const compiler = process.env.BIRDSHOT_ISCC || path.join(root, '.tools', 'inno-setup', 'compiler', 'ISCC.exe')
if (!existsSync(compiler)) throw new Error('Install the verified official Inno Setup compiler; set BIRDSHOT_ISCC to its ISCC.exe')
if (!existsSync(path.join(directory, 'BirdShot.exe'))) throw new Error('Build the complete app with desktop:build before compiling the installer')
const source = JSON.parse(await readFile(path.join(directory, 'resources', 'licenses', 'source-files.json')))
let sourceCommit = null
const sourceRef = process.argv.find(value => value.startsWith('--source-ref='))?.slice('--source-ref='.length)
if (sourceRef) {
  const commit = spawnSync('git', ['rev-parse', sourceRef], { cwd: root, encoding: 'utf8', shell: false })
  if (commit.status !== 0 || !/^[a-f0-9]{40}$/.test(commit.stdout.trim())) throw new Error('Invalid installer source reference')
  sourceCommit = commit.stdout.trim()
}
const hashFile = async filename => {
  const digest = createHash('sha256')
  for await (const block of createReadStream(filename)) digest.update(block)
  return digest.digest('hex')
}
for (const item of source.files) {
  if (await hashFile(path.join(root, item.path)) !== item.sha256) throw new Error('Refresh the app bundle after source changes: ' + item.path)
  if (sourceCommit) {
    const expected = spawnSync('git', ['rev-parse', sourceCommit + ':' + item.path], { cwd: root, encoding: 'utf8', shell: false })
    const actual = spawnSync('git', ['hash-object', '--path', item.path, item.path], { cwd: root, encoding: 'utf8', shell: false })
    if (expected.status !== 0 || actual.status !== 0 || expected.stdout !== actual.stdout) throw new Error('Installer commit differs from source: ' + item.path)
  }
}
await writeFile(path.join(directory, 'BUILD.json'), JSON.stringify({ version, sourceCommit,
  sourceDigest: source.sourceDigest, runtimeIncluded: true, modelsIncluded: true, codeSigned: false,
  platform: 'win32-x64', dataSchemaVersion: 1, distribution: 'current-user-installer' }, null, 2))
const started = performance.now()
const result = spawnSync(compiler, ['/Qp', `/DAppVersion=${version}`, `/DAppDirectory=${directory}`,
  `/DOutputDirectory=${path.join(root, 'desktop-artifacts')}`, path.join(root, 'desktop', 'installer.iss')],
{ shell: false, stdio: 'inherit', cwd: root })
if (result.status !== 0) throw new Error('Inno Setup compilation failed')
const generationSeconds = (performance.now() - started) / 1000
const installer = path.join(root, 'desktop-artifacts', `BirdShot-${version}-Setup-x64.exe`)
let files = 0, expandedBytes = 0
async function count(folder) {
  for (const entry of await readdir(folder, { withFileTypes: true })) {
    const filename = path.join(folder, entry.name)
    if (entry.isDirectory()) await count(filename)
    else if (entry.isFile()) { files++; expandedBytes += (await stat(filename)).size }
    else throw new Error('Unregistered link in distributable: ' + filename)
  }
}
await count(directory)
const manifest = { version, platform: 'win32-x64', channel: 'stable', releaseTag: `birdshot-desktop-v${version}`,
  dataSchemaVersion: 1, minSupportedDataSchema: 1, filename: path.basename(installer),
  bytes: (await stat(installer)).size, sha256: await hashFile(installer), files, expandedBytes,
  sourceCommit, sourceDigest: source.sourceDigest, compression: 'Inno Setup zip/1, non-solid',
  generationSeconds: Math.round(generationSeconds * 1000) / 1000, installScope: 'current-user',
  codeSigned: false, modelsIncluded: true, runtimeIncluded: true, userDataRemovedOnUninstall: false }
await writeFile(installer.replace(/\.exe$/, '.manifest.json'), JSON.stringify(manifest, null, 2))
console.log(JSON.stringify(manifest))
