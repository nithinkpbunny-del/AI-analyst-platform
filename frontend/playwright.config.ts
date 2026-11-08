import { defineConfig } from '@playwright/test'

// Visual snapshots must be taken in the same environment every time, so run Playwright through
// `make e2e` (the official Playwright Docker image), never directly on macOS.
const desktop = { width: 1280, height: 800 }
const phone = { width: 375, height: 812 }

export default defineConfig({
  testDir: './e2e',
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.002, animations: 'disabled' } },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    reducedMotion: 'reduce',
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
  },
  projects: [
    { name: 'desktop-light', use: { viewport: desktop, colorScheme: 'light' } },
    { name: 'desktop-dark', use: { viewport: desktop, colorScheme: 'dark' } },
    {
      name: 'mobile-light',
      use: { viewport: phone, colorScheme: 'light', isMobile: true, hasTouch: true },
    },
    {
      name: 'mobile-dark',
      use: { viewport: phone, colorScheme: 'dark', isMobile: true, hasTouch: true },
    },
  ],
  webServer: {
    command: 'npm run build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
