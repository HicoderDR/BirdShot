// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test'
import assert from 'node:assert/strict'
import { StartupState } from '../desktop/startup-state.mjs'
const state = (id, status) => ({ version: 1, instanceId: id, startup: { status, phase: 'test' },
  runtime: { ready: status === 'ready' }, service: { status: status === 'ready' ? 'ready' : 'starting' }, sources: [{ id: 'old-source' }] })
test('a slow initialization warning never permanently rejects a late matching ready', () => {
  const session = new StartupState('one')
  assert.equal(session.state.runtime.ready, false)
  session.warn()
  assert.match(session.state.startup.warning, /初始化/)
  assert.equal(session.accept({ event: 'controller-ready', instanceId: 'one', state: state('one', 'initializing') }), true)
  assert.equal(session.connected, true)
  assert.equal(session.state.runtime.ready, false)
  assert.equal(session.accept({ event: 'ready', instanceId: 'one', state: state('one', 'ready') }), true)
  assert.equal(session.state.runtime.ready, true)
})
test('failed then restarted initialization retains sources and rejects stale identities', () => {
  const session = new StartupState('one')
  session.accept({ event: 'controller-ready', instanceId: 'one', state: state('one', 'ready') })
  session.fail('controlled exit')
  assert.equal(session.connected, false)
  session.reset(session.state)
  assert.equal(session.state.sources.length, 1)
  assert.equal(session.accept({ event: 'ready', instanceId: 'two', state: state('two', 'ready') }), false)
  assert.equal(session.state.runtime.ready, false)
  session.accept({ event: 'controller-ready', instanceId: 'one', state: state('one', 'initializing') })
  assert.equal(session.connected, true)
})
