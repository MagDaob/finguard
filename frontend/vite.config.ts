import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        proxy: {
            '/v1': process.env.FINGUARD_API_TARGET ?? 'http://127.0.0.1:8000',
            '/health': process.env.FINGUARD_API_TARGET ?? 'http://127.0.0.1:8000',
        },
    },
    preview: {
        proxy: {
            '/v1': process.env.FINGUARD_API_TARGET ?? 'http://127.0.0.1:8000',
            '/health': process.env.FINGUARD_API_TARGET ?? 'http://127.0.0.1:8000',
        },
    },
    test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], restoreMocks: true },
})
