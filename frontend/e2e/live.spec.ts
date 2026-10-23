import { expect, test } from "@playwright/test";

// Needs the whole live stack: `docker compose --profile sim up -d` (the simulated cameras), the
// API, and live ingestion: `python -m services.ingestion`. Cameras made here are disabled at the
// end, so ingestion stops reading them.
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const RTSP = process.env.E2E_RTSP_URL ?? "rtsp://127.0.0.1:8554";
const unique = () => `e2e-live-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
// Ingestion reads the camera list every 30 s; then the stream has to connect.
const PICKED_UP_MS = 60_000;

const created: string[] = [];

async function addLiveCamera(name: string, path: string) {
  const response = await fetch(`${API}/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: name, source_type: "rtsp", source_uri: `${RTSP}/${path}` }),
  });
  expect(response.status).toBe(201);
  const camera = await response.json();
  created.push(camera.id);
  return camera as { id: string; live: { state: string } };
}

test.beforeAll(async () => {
  const probe = await addLiveCamera(unique(), "e2e-probe");
  if (probe.live.state === "not_running") {
    throw new Error(
      "Live ingestion is not running. Start it with `python -m services.ingestion`, and the " +
        "simulated cameras with `docker compose --profile sim up -d`.",
    );
  }
});

test.afterAll(async () => {
  for (const id of created) {
    await fetch(`${API}/cameras/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
  }
});

test("a camera on the simulated stream is live with a fresh picture", async ({ page }) => {
  test.setTimeout(PICKED_UP_MS + 60_000);
  const name = unique();
  await addLiveCamera(name, "cam-01");

  await page.goto("/live");
  const tile = page.getByRole("link", { name: new RegExp(`^${name}: `) });
  await expect(tile).toBeVisible();
  await expect(tile).toHaveAccessibleName(`${name}: Live`, { timeout: PICKED_UP_MS });
  const picture = tile.getByRole("img", { name: `Latest picture from ${name}` });
  await expect(picture).toBeVisible({ timeout: 20_000 });
  await expect
    .poll(() => picture.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth), {
      timeout: 20_000,
    })
    .toBeGreaterThan(0);
  await expect(tile).toContainText(/Updated \d+ s ago/);
});

test("a camera whose stream doesn't exist shows as offline", async ({ page }) => {
  test.setTimeout(PICKED_UP_MS + 60_000);
  const name = unique();
  await addLiveCamera(name, `missing-${Date.now()}`);

  await page.goto("/live");
  const tile = page.getByRole("link", { name: new RegExp(`^${name}: `) });
  await expect(tile).toHaveAccessibleName(`${name}: Offline`, { timeout: PICKED_UP_MS });
  await expect(tile).toContainText("Offline since");
  await expect(tile).toContainText("No picture yet");
});

test("a tile opens the camera's timeline", async ({ page }) => {
  const name = unique();
  const camera = await addLiveCamera(name, "cam-02");

  await page.goto("/live");
  await page.getByRole("link", { name: new RegExp(`^${name}: `) }).click();
  await expect(page).toHaveURL(new RegExp(`/cameras\\?camera=${camera.id}$`));
  const timeline = page.getByRole("region", { name: "Timeline" });
  await expect(timeline).toBeVisible();
  await expect(timeline.getByRole("button", { name: "1 h" })).toHaveAttribute("aria-pressed", "true");
  // A live camera has no upload card.
  await expect(page.getByText("Upload a video")).toBeHidden();
});
