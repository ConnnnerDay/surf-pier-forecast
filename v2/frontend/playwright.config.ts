import { defineConfig, devices } from '@playwright/test'

// Tests share one backend SQLite DB (reset fresh by e2e/start-backend.sh on
// every run) and a fixed set of pre-seeded beta-allowlist emails — see
// e2e/global-setup.ts and backend/scripts/seed_e2e.py. Running them in
// parallel workers would race on that shared state, so this suite is
// intentionally serial.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  globalSetup: './e2e/global-setup.ts',
  // Most tests are fast (local auth flows only). forecast.spec.ts hits a
  // live multi-source fetch and raises its own per-test timeout instead of
  // inflating this default for everything else.
  timeout: 30_000,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev -- --port 5173',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
    },
    {
      command: 'bash e2e/start-backend.sh',
      url: 'http://localhost:8000/health',
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Set PLAYWRIGHT_CHROMIUM_PATH to point at an already-installed
        // browser instead of Playwright's managed download (used in this
        // sandbox's dev environment; CI runs `npx playwright install
        // --with-deps chromium` instead and leaves this unset).
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
          : {},
      },
    },
  ],
})
