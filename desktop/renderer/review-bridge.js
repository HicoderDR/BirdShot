// SPDX-License-Identifier: AGPL-3.0-only
// Only the one registered loopback gallery iframe can call the bounded native API.
const reviewFrame = document.getElementById('gallery-frame')
const bridgeActions = new Set(['state', 'decide', 'preferences', 'cleanupPreview', 'cleanupExecute', 'cleanupCancel'])
function reviewOrigin() {
  try {
    const url = new URL(reviewFrame.src)
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port && !url.username && !url.password ? url.origin : null
  } catch { return null }
}
window.addEventListener('message', async event => {
  const origin = reviewOrigin()
  if (!origin || event.source !== reviewFrame.contentWindow || event.origin !== origin || !window.birdshotDesktop?.review) return
  const message = event.data
  if (message?.type === 'birdshot-review-ready') {
    reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-capabilities', writable: true }, origin)
    return
  }
  if (message?.type === 'birdshot-review-mode' && typeof message.immersive === 'boolean') {
    document.body.classList.toggle('immersive-review', message.immersive)
    return
  }
  if (message?.type !== 'birdshot-review-request' || typeof message.id !== 'string' ||
      !/^[A-Za-z0-9_-]{1,100}$/.test(message.id) || !bridgeActions.has(message.action)) return
  const source = event.source
  let result, error
  try { result = await window.birdshotDesktop.review(message.action, message.params || {}) }
  catch (failure) { error = failure.message }
  if (reviewOrigin() === origin && reviewFrame.contentWindow === source) source.postMessage({
    type: 'birdshot-review-response', id: message.id, ok: !error, ...(error ? { error } : { result }),
  }, origin)
})
reviewFrame.addEventListener('load', () => document.body.classList.remove('immersive-review'))
window.birdshotDesktop?.onReviewProgress?.(progress => {
  const origin = reviewOrigin()
  if (origin) reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-trash-progress', ...progress }, origin)
})
let latestView
window.birdshotDesktop?.onState?.(state => {
  if (state.viewRevision === latestView) return
  latestView = state.viewRevision
  const origin = reviewOrigin()
  if (origin) reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-refresh' }, origin)
})
