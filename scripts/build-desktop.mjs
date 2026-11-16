// SPDX-License-Identifier: AGPL-3.0-only
import { packager } from '@electron/packager'
import { createPackage } from '@electron/asar'
import { cp, mkdir, writeFile, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const python = process.env.BIRDSHOT_BUILD_PYTHON || path.join(root, '.tools', 'venv', 'Scripts', 'python.exe')
if (!existsSync(path.join(root, 'node_modules', 'electron', 'dist', 'electron.exe'))) {
  const download = spawnSync(python, [path.join(root, 'scripts', 'download_desktop_electron.py')], { cwd: root, stdio: 'inherit', shell: false })
  if (download.status !== 0) throw new Error('Electron binary download/checksum failed')
}
const prepare = spawnSync(python, [path.join(root, 'scripts', 'prepare_desktop_assets.py')], { cwd: root, stdio: 'inherit', shell: false })
if (prepare.status !== 0) throw new Error('Desktop runtime preparation failed')
const app = path.join(root, 'desktop-build', 'app')
await mkdir(app, { recursive: true })
await cp(path.join(root, 'desktop'), path.join(app, 'desktop'), { recursive: true })
const version = '0.2.0'
await writeFile(path.join(app, 'package.json'), JSON.stringify({ name: 'birdshot-desktop', productName: 'BirdShot',
  version, main: 'desktop/main.mjs', type: 'module', license: 'AGPL-3.0-only' }, null, 2))
const config = JSON.parse(await readFile(path.join(root, 'package.json')))
const destination = path.join(root, 'desktop-artifacts', 'BirdShot-win32-x64')
let outputs
if (process.argv.includes('--refresh') && existsSync(path.join(destination, 'BirdShot.exe'))) {
  await createPackage(app, path.join(destination, 'resources', 'app.asar'))
  for (const name of ['backend', 'licenses']) {
    await cp(path.join(root, 'desktop-build', 'resources', name), path.join(destination, 'resources', name), { recursive: true })
  }
  await cp(path.join(root, 'scripts', 'desktop_sitecustomize.py'),
    path.join(destination, 'resources', 'runtime', 'python', 'Lib', 'site-packages', 'sitecustomize.py'))
  outputs = [destination]
} else outputs = await packager({ dir: app, out: path.join(root, 'desktop-artifacts'), name: 'BirdShot',
  platform: 'win32', arch: 'x64', electronVersion: config.devDependencies.electron, appVersion: version,
  overwrite: true, asar: true, prune: false,
  electronZipDir: path.join(root, '.tools', 'electron-download'),
  extraResource: ['backend', 'runtime', 'licenses'].map(name => path.join(root, 'desktop-build', 'resources', name)),
  win32metadata: { CompanyName: 'BirdShot contributors', FileDescription: 'BirdShot local bird photo culling', ProductName: 'BirdShot' },
})
console.log(JSON.stringify({ packaged: true, version, outputs }))
