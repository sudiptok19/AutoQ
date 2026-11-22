import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { VitePWA } from 'vite-plugin-pwa'

// HTTPS=1 serves a self-signed cert so phones on the LAN get camera + service worker (secure context).
const https = process.env.HTTPS === '1'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    ...(https ? [basicSsl()] : []),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['mark.svg'],
      manifest: {
        name: 'AutoQ-Trace Dock Scanner',
        short_name: 'AutoQ Scan',
        description: 'Signed package verification and offline-provisional loading scans.',
        start_url: '/scanner',
        scope: '/',
        display: 'standalone',
        background_color: '#0a0c0f',
        theme_color: '#0a0c0f',
        icons: [{ src: '/mark.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        runtimeCaching: [{ urlPattern: /^https:\/\/fonts\.(googleapis|gstatic)\.com\/.*/, handler: 'CacheFirst', options: { cacheName: 'fonts', expiration: { maxEntries: 20 } } }],
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    host: true,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  test: { environment: 'node' },
} as any)
