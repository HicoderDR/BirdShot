import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'BirdShot Field Lab',
        short_name: 'BirdShot',
        description: '在设备上实时检测、追踪并记录鸟类',
        theme_color: '#10211a',
        background_color: '#f3f0e7',
        display: 'standalone',
        orientation: 'portrait',
        icons: [
          {
            src: 'favicon.svg',
            sizes: 'any',
            type: 'image/svg+xml',
            purpose: 'any maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,wasm}'],
        maximumFileSizeToCacheInBytes: 30 * 1024 * 1024,
        runtimeCaching: [
          {
            urlPattern: /^https:\/\/(raw\.githubusercontent\.com\/nomi30701\/YOLO-ByteTrack-ONNX-Web|birdnet-team\.github\.io\/real-time-pwa|cdn\.jsdelivr\.net\/npm\/@tensorflow|huggingface\.co\/ozzyonfire\/bird-species-classifier)\//,
            handler: 'CacheFirst',
            options: {
              cacheName: 'birdshot-models',
              expiration: { maxEntries: 64, maxAgeSeconds: 60 * 60 * 24 * 90 },
            },
          },
        ],
      },
    }),
  ],
})
