import { defineConfig } from "vitest/config";

// Unit tests only; the Playwright specs in e2e/ run with `npm run e2e`.
export default defineConfig({ test: { include: ["lib/**/*.test.ts"] } });
