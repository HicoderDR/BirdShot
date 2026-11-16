// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { externalReference, networkSettings, trustedSender } from '../desktop/security.mjs'

test('only the exact privileged local main frame may manage the library', () => {
  const frame = { url: 'file:///app/desktop/renderer/index.html' }, contents = { mainFrame: frame }
  assert.equal(trustedSender({ sender: contents, senderFrame: frame }, contents, frame.url), true)
  assert.equal(trustedSender({ sender: contents, senderFrame: { url: frame.url } }, contents, frame.url), false)
  assert.equal(trustedSender({ sender: contents, senderFrame: { url: 'http://127.0.0.1:5274/' } }, contents, frame.url), false)
})
test('external links are encyclopedia/attribution HTTPS only, not a command bridge', () => {
  assert.equal(externalReference('https://zh.wikipedia.org/wiki/八哥'), true)
  assert.equal(externalReference('https://www.inaturalist.org/photos/78846863'), true)
  for (const url of ['file:///C:/Windows/system.ini', 'javascript:alert(1)', 'https://evil.invalid/', 'https://user:pass@zh.wikipedia.org/wiki/Bird']) {
    assert.equal(externalReference(url), false)
  }
})
test('network settings cannot become arbitrary spawn arguments', () => {
  assert.deepEqual(networkSettings({ mode: 'local', port: 5274 }), { mode: 'local', port: 5274 })
  for (const value of [{ mode: 'shell', port: 5274 }, { mode: 'all', port: 80 }, { mode: 'lan', port: 5274, lanIp: '127.0.0.1; whoami' }]) {
    assert.throws(() => networkSettings(value))
  }
})
