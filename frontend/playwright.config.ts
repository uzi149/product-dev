import path from "node:path";

import { defineConfig } from "@playwright/test";

import { E2E_API_URL, E2E_PYTHON, E2E_UI_PORT } from "./e2e/stack";

const ROOT = path.resolve(__dirname, "..");

// End-to-end tests run against a real stack of their own: `docker compose --profile sim up -d`
// must be running (PostgreSQL, MinIO, Redis, MediaMTX and the simulated cameras). Playwright then
// starts an e2e backend (API, worker, live ingestion) on its own database, bucket and Redis
// database, emptied at every run, and a dev server on port 3100 that talks to it. The development
// data and a running `npm run dev` or API are not touched. Not part of CI.
const python = E2E_PYTHON;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${E2E_UI_PORT}`,
    // Uses the Chrome installed on the machine, so no browser download is needed.
    channel: "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // Resets the e2e database, bucket and Redis database, then starts the worker, live
      // ingestion and, last, the API; its logs are in data/e2e/logs/.
      command: `"${python}" -m tests.e2e_stack`,
      cwd: ROOT,
      url: `${E2E_API_URL}/health`,
      // Always a fresh one: a server already on this port would not have been reset.
      reuseExistingServer: false,
      timeout: 180_000,
      stdout: "pipe",
    },
    {
      command: `npx next dev --port ${E2E_UI_PORT}`,
      url: `http://localhost:${E2E_UI_PORT}`,
      // Own build folder: Next.js allows only one dev server per folder, even on another port.
      // The API address set here wins over the one in .env.local.
      env: { NEXT_DIST_DIR: ".next-e2e", NEXT_PUBLIC_API_URL: E2E_API_URL },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
