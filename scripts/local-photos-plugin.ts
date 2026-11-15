// SPDX-License-Identifier: AGPL-3.0-only
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'

type LocalManifest = { groups: { photos: { name: string }[] }[] }

export function localPhotosPlugin(): Plugin {
  function handle(request: IncomingMessage, response: ServerResponse, next: () => void) {
    if (!request.url?.startsWith('/culling/local/')) return next()
    const remote = request.socket.remoteAddress
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remote ?? '')) {
      response.writeHead(403).end('Local photos are accessible only on this computer.')
      return
    }
    const host = request.headers.host ?? ''
    const origin = request.headers.origin
    if (!/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/i.test(host)
      || (origin && origin !== `http://${host}` && origin !== `https://${host}`)) {
      response.writeHead(403).end('Local photos require a loopback host and same-origin requests.')
      return
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.writeHead(405, { Allow: 'GET, HEAD' }).end('Read-only endpoint.')
      return
    }
    const directory = process.env.BIRDSHOT_PHOTO_DIR
    const manifestPath = resolve('.tools', 'local-manifest.json')
    if (!directory || !existsSync(manifestPath)) {
      response.writeHead(404).end('Local photo directory is not configured.')
      return
    }
    response.setHeader('Cache-Control', 'no-store')
    response.setHeader('Cross-Origin-Resource-Policy', 'same-origin')
    response.setHeader('X-Content-Type-Options', 'nosniff')
    try {
      const rawManifest = readFileSync(manifestPath, 'utf8')
      const manifest: LocalManifest = JSON.parse(rawManifest)
      const route = new URL(request.url, 'http://localhost').pathname
      if (route === '/culling/local/manifest.json') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' }).end(rawManifest)
        return
      }
      const name = decodeURIComponent(route.slice('/culling/local/photo/'.length))
      if (!route.startsWith('/culling/local/photo/') || basename(name) !== name
        || !/\.(jpe?g|png)$/i.test(name)
        || !manifest.groups.some((g) => g.photos.some((p) => p.name === name))) {
        response.writeHead(404).end('Photo is not in the configured allow-list.')
        return
      }
      const path = join(resolve(directory), name)
      const size = statSync(path).size
      response.writeHead(200, { 'Content-Type': /\.png$/i.test(name) ? 'image/png' : 'image/jpeg', 'Content-Length': size })
      if (request.method === 'HEAD') { response.end(); return }
      const stream = createReadStream(path)
      stream.on('error', (error) => {
        console.error('Failed to read configured local photo:', error)
        response.destroy(error)
      })
      response.on('close', () => stream.destroy())
      stream.pipe(response)
    } catch (error) {
      console.error('Local photo request failed:', error)
      if (!response.headersSent) response.writeHead(500).end('Failed to read the local photo manifest or original.')
      else response.destroy()
    }
  }
  return {
    name: 'birdshot-readonly-local-photos',
    configureServer(server) { server.middlewares.use(handle) },
    configurePreviewServer(server) { server.middlewares.use(handle) },
  }
}
