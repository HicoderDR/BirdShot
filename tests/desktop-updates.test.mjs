// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { checkUpdates, compareVersions, releasePage } from '../desktop/updates.mjs'
const release = (version, overrides = {}) => ({ tag_name: 'birdshot-desktop-v' + version,
  html_url: 'https://github.com/HicoderDR/BirdShot/releases/tag/birdshot-desktop-v' + version,
  assets: ['exe', 'manifest.json'].map(extension => ({ name: `BirdShot-${version}-Setup-x64.${extension}`, size: 123,
    browser_download_url: `https://github.com/HicoderDR/BirdShot/releases/download/birdshot-desktop-v${version}/BirdShot-${version}-Setup-x64.${extension}` })), ...overrides })
const metadata = { version: '0.4.0', platform: 'win32-x64', channel: 'stable', filename: 'BirdShot-0.4.0-Setup-x64.exe',
  bytes: 123, sha256: 'a'.repeat(64), dataSchemaVersion: 1, minSupportedDataSchema: 1, codeSigned: false }
const response = value => new Response(JSON.stringify(value), { status: 200 })
test('desktop version comparison is semantic and model releases are not updates', async () => {
  assert.equal(compareVersions('0.10.0', '0.9.9'), 1)
  assert.equal((await checkUpdates('0.3.0', async () => response([{ tag_name: 'v0.2.0-models' }]))).status, 'none')
  assert.equal((await checkUpdates('0.3.0', async () => new Response('', { status: 404 }))).status, 'none')
  assert.equal((await checkUpdates('0.3.0', async () => response([release('0.5.0', { draft: true }), release('0.4.0', { prerelease: true })]))).status, 'none')
})
test('only a marked stable release with matching manifest produces a page link, never execution', async () => {
  const result = await checkUpdates('0.3.0', async url => response(url.includes('api.github.com') ? [release('0.4.0')] : metadata))
  assert.equal(result.status, 'available')
  assert.equal(result.automaticInstallation, false)
  assert.equal(result.unsigned, true)
  assert.ok(releasePage(result.releaseUrl))
  assert.equal(releasePage('https://github.com/evil/repo/releases/tag/birdshot-desktop-v0.4.0'), false)
  assert.equal(releasePage('file:///installer.exe'), false)
})
test('future schema, mismatched manifest and network failure are explicit', async () => {
  assert.equal((await checkUpdates('0.3.0', async url => response(url.includes('api.github.com') ? [release('0.4.0')] :
    { ...metadata, dataSchemaVersion: 2, minSupportedDataSchema: 2 }))).status, 'incompatible')
  assert.equal((await checkUpdates('0.3.0', async url => response(url.includes('api.github.com') ? [release('0.4.0')] :
    { ...metadata, bytes: 999 }))).status, 'error')
  assert.equal((await checkUpdates('0.3.0', async () => { throw new Error('offline') })).status, 'error')
  assert.equal((await checkUpdates('0.3.0', async () => new Response('', { status: 429 }))).status, 'error')
  let calls = 0
  assert.equal((await checkUpdates('0.3.0', async () => { calls++; return response([]) }, true)).status, 'error')
  assert.equal(calls, 0)
})
