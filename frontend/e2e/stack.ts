// Where the e2e tests run. Playwright starts its own backend (`python -m tests.e2e_stack`) with its
// own database, storage bucket and Redis database, so the tests never touch the development data.
export const E2E_API_URL = "http://127.0.0.1:8100";
export const E2E_UI_PORT = 3100;
// The simulated cameras (`docker compose --profile sim up -d`) are the only shared part.
export const E2E_RTSP_URL = process.env.E2E_RTSP_URL ?? "rtsp://127.0.0.1:8554";
