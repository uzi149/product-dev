import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

import { apiFetch, cameraWithSegment, unique } from "./helpers";
import { E2E_API_URL as API, E2E_PYTHON, ROOT } from "./stack";

// The Events pages, the live toast, Overview and Settings (step 5.5). Events are seeded in the e2e
// database (tests/e2e_db.py) on a real stored segment, so their evidence plays and exports for real.

function db(...args: string[]): string {
  return execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", ...args], { cwd: ROOT }).toString().trim();
}

async function triggerWithSegment(): Promise<{ triggerId: string; segmentId: string; name: string; cameraName: string }> {
  const { cameraId, segmentId, name: cameraName } = await cameraWithSegment();
  const name = `Bag ${unique()}`;
  return { triggerId: db("seed-trigger", cameraId, "--name", name, "--enabled"), segmentId, name, cameraName };
}

test("events are listed for review and filtered", async ({ page }) => {
  const { triggerId, segmentId, name } = await triggerWithSegment();
  const fresh = db("seed-event", triggerId, segmentId);
  const confirmed = db("seed-event", triggerId, segmentId, "--status", "confirmed");

  await page.goto(`/events?trigger=${triggerId}`);
  const rows = page.getByRole("list", { name: "Events" }).getByRole("listitem");
  await expect(rows).toHaveCount(1); // new ones by default
  await expect(rows.first()).toContainText(name);
  await expect(rows.first()).toContainText("Verified by the VLM");
  await expect(rows.first().getByRole("link")).toHaveAttribute("href", `/events/${fresh}`);

  await page.getByRole("group", { name: "Status" }).getByRole("button", { name: "Confirmed" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByRole("link")).toHaveAttribute("href", `/events/${confirmed}`);
  await page.getByRole("group", { name: "Status" }).getByRole("button", { name: "All" }).click();
  await expect(rows).toHaveCount(2);
});

test("an event plays, is confirmed with a note, exports to a download and can be deleted", async ({ page }) => {
  test.setTimeout(150_000);
  const { triggerId, segmentId, name } = await triggerWithSegment();
  const eventId = db("seed-event", triggerId, segmentId);

  await page.goto(`/events/${eventId}`);
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByRole("region", { name: "Evidence" })).toContainText("Part 1 of 1");
  const event = page.getByRole("region", { name: "Event" });
  await expect(event).toContainText("place object");
  await expect(event).toContainText("New");

  await page.getByLabel("Note").fill("a real bag");
  const review = page.getByRole("region", { name: "Review" });
  await review.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(event).toContainText("Confirmed");
  await expect(review).toContainText("“a real bag”");
  // Confirmed takes the place of the buttons until the event goes back to review.
  await expect(review.getByRole("button", { name: "Confirmed", exact: true })).toBeDisabled();
  await expect(review.getByRole("button", { name: "Confirm", exact: true })).toHaveCount(0);
  await expect(review.getByRole("button", { name: "Dismiss", exact: true })).toHaveCount(0);
  await review.getByRole("button", { name: "Back to review" }).click();
  await expect(event).toContainText("New");
  await expect(review.getByRole("button", { name: "Confirm", exact: true })).toBeEnabled();
  await expect(review.getByRole("button", { name: "Confirmed", exact: true })).toHaveCount(0);

  // The export runs for real on the e2e worker (FFmpeg).
  const exportBox = page.getByRole("region", { name: "Export" }).getByRole("group", { name: "Original" });
  await exportBox.getByRole("button", { name: "Export" }).click();
  const download = exportBox.getByRole("link", { name: "Download" });
  await expect(download).toBeVisible({ timeout: 90_000 });
  await expect(exportBox.getByRole("status")).toHaveText("Ready to download");
  expect(await download.getAttribute("download")).toMatch(/^bag-.*\.mp4$/);
  const file = await apiFetch((await download.getAttribute("href"))!);
  expect(file.status).toBe(200);
  expect((await file.arrayBuffer()).byteLength).toBeGreaterThan(10_000);

  await page.getByRole("button", { name: "Delete event" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete event" }).click();
  await expect(page).toHaveURL(/\/events$/);
  expect((await apiFetch(`${API}/events/${eventId}`)).status).toBe(404);
});

test("a reported event pops up as a toast that opens it, and the badge counts it", async ({ page }) => {
  const { triggerId, segmentId, name, cameraName } = await triggerWithSegment();
  await page.goto("/settings");
  const badge = page.getByRole("link", { name: /^Events/ });
  await expect(page.getByRole("region", { name: "Triggers" })).toBeVisible(); // the page is up
  // From the API: the badge may not have loaded yet.
  const before = (await (await page.request.get(`${API}/events/counts`)).json()).new;

  const eventId = db("seed-event", triggerId, segmentId);
  // Give the page's WebSocket a moment to be connected, then send the notice.
  await page.waitForTimeout(1_000);
  db("notify", eventId);
  const toast = page.locator("[data-sonner-toast]").filter({ hasText: name });
  await expect(toast).toContainText(cameraName);
  await expect(badge).toContainText(String(before + 1));
  await toast.getByRole("button", { name: "View" }).click();
  await expect(page).toHaveURL(new RegExp(`/events/${eventId}$`));
});

test("Overview counts and Settings values", async ({ page }) => {
  const { triggerId, segmentId, name } = await triggerWithSegment();
  db("seed-event", triggerId, segmentId);
  // Through the page's request context: a Node fetch after the blocking db() call can reuse a
  // keep-alive connection the API already closed (ECONNRESET).
  await page.goto("/");
  const counts = await (await page.request.get(`${API}/events/counts`)).json();
  await expect(page.getByRole("link", { name: /Events to review/ })).toContainText(String(counts.new));
  await expect(page.getByRole("link", { name: /Active triggers/ })).toBeVisible();
  await expect(page.getByRole("region", { name: "Latest events" })).toContainText(name);
  await expect(page.getByRole("region", { name: "System" })).toContainText("Database");

  await page.goto("/settings");
  const vlm = page.getByRole("region", { name: "Vision-language model" });
  await expect(vlm).toContainText("API (127.0.0.1)"); // the e2e stack's VLM address, host only
  await expect(page.getByRole("region", { name: "Analysis" })).toContainText("12 s");
  await expect(page.getByRole("region", { name: "Triggers" })).toContainText("TRIGGERS_ENABLED");
  await expect(page.locator("body")).not.toContainText("e2e-no-model"); // never a key
});

test("marks are drawn over the evidence, switch off, and export drawn in", async ({ page }) => {
  // Step 5.7. The e2e stack has no model: the marks are seeded as the query worker leaves them.
  test.setTimeout(150_000);
  const { triggerId, segmentId } = await triggerWithSegment();
  const eventId = db("seed-event", triggerId, segmentId);
  const withMarks = page.getByRole("region", { name: "Export" }).getByRole("group", { name: "With marks" });

  // No marks yet: the marked export waits for them.
  await page.goto(`/events/${eventId}`);
  await expect(withMarks.getByRole("button", { name: "Export" })).toBeDisabled();
  await expect(withMarks.getByRole("status")).toHaveText("Available once the marks are made");

  db("seed-marks", eventId);
  await page.reload();
  const evidence = page.getByRole("region", { name: "Evidence" });
  const marks = evidence.getByLabel("Marks", { exact: true });
  await expect(marks).toContainText("2 people and objects marked; the ones it is about are highlighted");
  await expect(marks.getByRole("list", { name: "Legend" })).toContainText("person");
  const overlay = page.getByTestId("marks-overlay");
  await expect(overlay).toBeAttached();
  // Once the video plays, the canvas has the boxes on it.
  await expect
    .poll(
      () =>
        overlay.evaluate((canvas: HTMLCanvasElement) => {
          const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
          let drawn = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i] > 0) drawn++;
          return drawn;
        }),
      { timeout: 20_000 },
    )
    .toBeGreaterThan(100);

  await marks.getByRole("button", { name: "Marks on" }).click();
  await expect(overlay).not.toBeAttached();
  await expect(marks.getByRole("button", { name: "Marks off" })).toHaveAttribute("aria-pressed", "false");
  await page.reload(); // remembered in this browser
  await expect(page.getByRole("button", { name: "Marks off" })).toBeVisible();
  await page.getByRole("button", { name: "Marks off" }).click();
  await expect(page.getByTestId("marks-overlay")).toBeAttached();

  // The export with the marks drawn in runs for real on the e2e worker.
  await withMarks.getByRole("button", { name: "Export" }).click();
  const download = withMarks.getByRole("link", { name: "Download" });
  await expect(download).toBeVisible({ timeout: 90_000 });
  expect(await download.getAttribute("download")).toMatch(/_marked\.mp4$/);
  const file = await apiFetch((await download.getAttribute("href"))!);
  expect(file.status).toBe(200);
  expect((await file.arrayBuffer()).byteLength).toBeGreaterThan(10_000);
});

test("with the local model the event says marks are off", async ({ page }) => {
  const { triggerId, segmentId } = await triggerWithSegment();
  const eventId = db("seed-event", triggerId, segmentId);
  db("seed-marks", eventId, "--status", "skipped");
  await page.goto(`/events/${eventId}`);
  const marks = page.getByRole("region", { name: "Evidence" }).getByLabel("Marks", { exact: true });
  await expect(marks).toContainText("Marks are off with the local model.");
  await expect(marks.getByRole("button")).toHaveCount(0);
  await expect(page.getByTestId("marks-overlay")).not.toBeAttached();
});
