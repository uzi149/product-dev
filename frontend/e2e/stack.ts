import path from "node:path";

// Where the e2e tests run. Playwright starts its own backend (`python -m tests.e2e_stack`) with its
// own database, storage bucket and Redis database, so the tests never touch the development data.
export const E2E_API_URL = "http://127.0.0.1:8100";
export const E2E_UI_PORT = 3100;
// The simulated cameras (`docker compose --profile sim up -d`) are the only shared part.
export const E2E_RTSP_URL = process.env.E2E_RTSP_URL ?? "rtsp://127.0.0.1:8554";

export const ROOT = path.resolve(__dirname, "..", "..");
// The project's Python, which runs the e2e backend and tests/e2e_db.py.
export const E2E_PYTHON =
  process.env.E2E_PYTHON ??
  (process.platform === "win32"
    ? path.join(ROOT, ".venv", "Scripts", "python.exe")
    : path.join(ROOT, ".venv", "bin", "python"));
