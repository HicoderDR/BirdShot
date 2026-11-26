// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

test('review write bridge accepts only the exact registered loopback iframe', async () => {
  const handlers = new Map(), sent = [], called = []
  const child = { postMessage: (value, origin) => sent.push({ value, origin }) }
  const frame = { src: 'http://127.0.0.1:5274/', contentWindow: child, addEventListener: () => {} }
  const context = { URL, Set, console,
    document: { getElementById: () => frame, body: { classList: { toggle: () => {}, remove: () => {} } } },
    window: { addEventListener: (type, callback) => handlers.set(type, callback),
      birdshotDesktop: { review: async (...args) => { called.push(args); return { version: 1 } }, onReviewProgress: () => {} } } }
  vm.runInNewContext(await readFile(new URL('../desktop/renderer/review-bridge.js', import.meta.url), 'utf8'), context)
  const receive = handlers.get('message')
  const data = { type: 'birdshot-review-request', id: 'one', action: 'decide', params: { expectedRevision: 1 } }
  await receive({ source: {}, origin: 'http://127.0.0.1:5274', data })
  await receive({ source: child, origin: 'https://attacker.invalid', data })
  await receive({ source: child, origin: 'http://127.0.0.1:5274', data: { ...data, action: 'runShell' } })
  assert.equal(called.length, 0)
  await receive({ source: child, origin: 'http://127.0.0.1:5274', data })
  assert.equal(called.length, 1)
  assert.equal(sent[0].origin, 'http://127.0.0.1:5274')
  assert.equal(sent[0].value.id, 'one')
  frame.src = 'https://zh.wikipedia.org/wiki/Bird'
  await receive({ source: child, origin: 'https://zh.wikipedia.org', data })
  assert.equal(called.length, 1)
})
