// SPDX-License-Identifier: AGPL-3.0-only
import { packager } from '@electron/packager'
import { createPackage } from '@electron/asar'
import { cp, mkdir, writeFile, readFile, rm, rename } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const config = JSON.parse(await readFile(path.join(root, 'package.json')))
const version = config.desktopVersion
if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('desktopVersion must be an explicit semantic version')
const buildRoot = path.join(root, 'desktop-build', version)
const resources = path.join(buildRoot, 'resources')
const packagerTemporary = path.join(buildRoot, 'packager-temporary')
await mkdir(packagerTemporary, { recursive: true })
const python = process.env.BIRDSHOT_BUILD_PYTHON || path.join(root, '.tools', 'venv', 'Scripts', 'python.exe')
if (!existsSync(path.join(root, 'node_modules', 'electron', 'dist', 'electron.exe'))) {
  const download = spawnSync(python, [path.join(root, 'scripts', 'download_desktop_electron.py')], { cwd: root, stdio: 'inherit', shell: false })
  if (download.status !== 0) throw new Error('Electron binary download/checksum failed')
}
const prepare = spawnSync(python, [path.join(root, 'scripts', 'prepare_desktop_assets.py'), '--output', resources, '--zip-pure-python'],
  { cwd: root, stdio: 'inherit', shell: false })
if (prepare.status !== 0) throw new Error('Desktop runtime preparation failed')
const app = path.join(buildRoot, 'app')
await mkdir(app, { recursive: true })
await cp(path.join(root, 'desktop'), path.join(app, 'desktop'), { recursive: true })
await writeFile(path.join(app, 'package.json'), JSON.stringify({ name: 'birdshot-desktop', productName: 'BirdShot',
  version, main: 'desktop/main.mjs', type: 'module', license: 'AGPL-3.0-only' }, null, 2))
const destination = path.join(root, 'desktop-artifacts', `BirdShot-${version}-win32-x64`)
let outputs
async function copyVerifiedElectronDistribution() {
  console.warn('Electron Packager template rename was refused. Building from the verified official distribution without a template move.')
  const copied = spawnSync(python, ['-c', `
import hashlib,json,sys,zipfile
from pathlib import Path
archive=Path(sys.argv[1]); checks=json.loads(Path(sys.argv[2]).read_text())
with archive.open('rb') as stream:
 if hashlib.file_digest(stream,'sha256').hexdigest()!=checks[archive.name]:raise RuntimeError('Official Electron archive checksum mismatch')
destination=Path(chr(92)*2+'?'+chr(92)+sys.argv[3])
with zipfile.ZipFile(archive) as package:package.extractall(destination)
binary=destination/'electron.exe'
binary.replace(destination/'BirdShot.exe')
default=destination/'resources'/'default_app.asar'
default.unlink(missing_ok=True)
`, path.join(root, '.tools', 'electron-download', `electron-v${config.devDependencies.electron}-win32-x64.zip`),
  path.join(root, 'node_modules', 'electron', 'checksums.json'), destination],
  { cwd: root, stdio: 'inherit', shell: false })
  if (copied.status !== 0) throw new Error('Verified Electron distribution assembly failed')
  await createPackage(app, path.join(destination, 'resources', 'app.asar'))
  for (const name of ['backend', 'runtime', 'licenses']) {
    await cp(path.join(resources, name), path.join(destination, 'resources', name), { recursive: true })
  }
  return [destination]
}
if (process.argv.includes('--refresh') && existsSync(path.join(destination, 'BirdShot.exe'))) {
  const sourceManifest = JSON.parse(await readFile(path.join(resources, 'backend', 'assets-manifest.json')))
  const previousManifest = JSON.parse(await readFile(path.join(destination, 'resources', 'backend', 'assets-manifest.json')))
  if (sourceManifest.runtimeFingerprint !== previousManifest.runtimeFingerprint) {
    const targetRuntime = path.join(destination, 'resources', 'runtime')
    const stagedRuntime = path.join(destination, 'resources', 'runtime-refresh-staging')
    if (existsSync(stagedRuntime)) throw new Error('A runtime refresh was interrupted; inspect the exact runtime-refresh-staging directory before retrying')
    await cp(path.join(resources, 'runtime'), stagedRuntime, { recursive: true })
    await rm(targetRuntime, { recursive: true })
    await rename(stagedRuntime, targetRuntime)
  }
  await createPackage(app, path.join(destination, 'resources', 'app.asar'))
  for (const name of ['backend', 'licenses']) {
    await cp(path.join(resources, name), path.join(destination, 'resources', name), { recursive: true })
  }
  await cp(path.join(root, 'scripts', 'desktop_sitecustomize.py'),
    path.join(destination, 'resources', 'runtime', 'python', 'Lib', 'site-packages', 'sitecustomize.py'))
  outputs = [destination]
} else {
try { outputs = await packager({ dir: app, out: path.join(root, 'desktop-artifacts'),
  name: `BirdShot-${version}`, executableName: 'BirdShot',
  platform: 'win32', arch: 'x64', electronVersion: config.devDependencies.electron, appVersion: version,
  tmpdir: packagerTemporary,
  overwrite: true, asar: true, prune: false,
  electronZipDir: path.join(root, '.tools', 'electron-download'),
  extraResource: ['backend', 'runtime', 'licenses'].map(name => path.join(resources, name)),
  win32metadata: { CompanyName: 'BirdShot contributors', FileDescription: 'BirdShot local bird photo culling', ProductName: 'BirdShot' },
}) } catch (error) {
  if (process.platform !== 'win32' || error.code !== 'EPERM' || error.syscall !== 'rename') throw error
  console.warn('Packaging failed with a template rename error:', error.message)
  outputs = await copyVerifiedElectronDistribution()
}
}
console.log(JSON.stringify({ packaged: true, version, outputs }))
