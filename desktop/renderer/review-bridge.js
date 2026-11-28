// SPDX-License-Identifier: AGPL-3.0-only
// Only the one registered loopback gallery iframe can call the bounded native API.
const reviewFrame = document.getElementById('gallery-frame')
const bridgeActions = new Set(['state', 'save', 'decide', 'preferences', 'cleanupPreview', 'cleanupExecute', 'cleanupCancel'])
let closingRequest, reviewReady = false
let latestState, validated
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
  if (message?.type === 'birdshot-review-close-response' && message.id === closingRequest && typeof message.allow === 'boolean') {
    closingRequest = null
    window.birdshotDesktop?.reviewCloseResponse?.(message.id, message.allow)
    return
  }
  if (message?.type === 'birdshot-review-ready') {
    reviewReady = true
    validated = latestState?.runtime?.ready === true && latestState?.service?.status === 'ready'
    reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-capabilities', writable: true, validated }, origin)
    return
  }
  if (message?.type === 'birdshot-review-mode' && typeof message.immersive === 'boolean') {
    document.body.classList.toggle('immersive-review', message.immersive)
    window.birdshotDesktop?.setReviewMode?.(message.immersive)
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
window.birdshotDesktop?.onReviewCloseRequest?.(id => {
  const origin = reviewOrigin()
  if (!origin || !reviewReady) { window.birdshotDesktop.reviewCloseResponse(id, true); return }
  closingRequest = id
  reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-close-request', id }, origin)
})
reviewFrame.addEventListener('load', () => {
  reviewReady = false
  document.body.classList.remove('immersive-review')
  window.birdshotDesktop?.setReviewMode?.(false)
})
window.birdshotDesktop?.onReviewProgress?.(progress => {
  const origin = reviewOrigin()
  if (origin) reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-trash-progress', ...progress }, origin)
})
let latestView
window.addEventListener('birdshot-state', event => {
  const state = event.detail
  if (!state) return
  latestState = state
  const nextValidated = state.runtime?.ready === true && state.service?.status === 'ready'
  if (validated !== nextValidated) {
    validated = nextValidated
    const origin = reviewOrigin()
    if (origin) reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-validation', validated }, origin)
  }
  if (state.viewRevision === latestView) return
  latestView = state.viewRevision
  const origin = reviewOrigin()
  if (origin) reviewFrame.contentWindow.postMessage({ type: 'birdshot-review-refresh' }, origin)
})
