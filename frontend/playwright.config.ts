import { defineConfig } from "@playwright/test";

// End-to-end tests run against the real stack: `docker compose up -d`, migrations applied and the
// API running on 127.0.0.1:8000 (see the README). They are not part of CI.
// They start their own dev server on port 3100, so a busy port 3000 or a running `npm run dev` doesn't matter.
const PORT = 3100;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    // Uses the Chrome installed on the machine, so no browser download is needed.
    channel: "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `npx next dev --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    // Own build folder: Next.js allows only one dev server per folder, even on another port.
    env: { NEXT_DIST_DIR: ".next-e2e" },
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
