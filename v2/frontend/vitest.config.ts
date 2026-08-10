import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // e2e/ holds Playwright specs (run via `npm run test:e2e`), not Vitest
    // ones — Vitest's default include pattern would otherwise pick them up
    // too and fail trying to run Playwright's test() outside its runner.
    exclude: ['**/node_modules/**', '**/e2e/**'],
  },
})
