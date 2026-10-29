import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test } from "@playwright/test";

import { E2E_API_URL as API, E2E_PYTHON, ROOT } from "./stack";

// The e2e stack runs with analysis off: segments stay "Analysis queued" and nothing fails by
// itself, so these tests set a segment's state in the e2e database (tests/e2e_db.py).

const unique = () => `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

let videoPath = "";

test.beforeAll(() => {
  // 30 s with a moving pattern from 12 s to 16 s: one stored segment.
  const folder = mkdtempSync(path.join(tmpdir(), "sentinel-e2e-retry-"));
  videoPath = path.join(folder, "clip.mp4");
  execFileSync("ffmpeg", [
    "-y", "-v", "error",
    "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=30",
    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=30",
    "-filter_complex", "[0][1]overlay=x='60*(t-12)':y=120:enable='between(t,12,16)'",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "30", videoPath,
  ]);
});

/** A file camera with one stored segment, made through the API; returns both ids. */
async function cameraWithSegment(): Promise<{ cameraId: string; segmentId: string }> {
  const created = await fetch(`${API}/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: unique(), source_type: "file", enabled: true, retention_days: 14 }),
  });
  expect(created.status).toBe(201);
  const cameraId: string = (await created.json()).id;

  const form = new FormData();
  form.set("camera_id", cameraId);
  form.set("start_time", "2026-03-07T09:05:00Z");
  form.set("file", new Blob([readFileSync(videoPath)], { type: "video/mp4" }), "clip.mp4");
  expect((await fetch(`${API}/videos/upload`, { method: "POST", body: form })).status).toBe(202);

  let segments: { id: string }[] = [];
  await expect
    .poll(
      async () => {
        segments = await (await fetch(`${API}/segments?camera_id=${cameraId}`)).json();
        return segments.length;
      },
      { timeout: 120_000 },
    )
    .toBe(1);
  return { cameraId, segmentId: segments[0].id };
}

function setStatus(segmentId: string, status: "failed" | "stuck") {
  execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", "segment-status", segmentId, status], { cwd: ROOT });
}

test("a failed segment is analysed again with its Retry button", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  setStatus(segmentId, "failed");

  await page.goto(`/cameras?camera=${cameraId}`);
  const panel = page.locator("section", { hasText: "Stored segments" });
  const row = panel.getByRole("listitem");
  await expect(row).toContainText("Analysis failed");
  await expect(panel.getByRole("button", { name: "Retry failed (1)" })).toBeVisible();

  await row.getByRole("button", { name: "Retry" }).click();
  await expect(row).toContainText("Analysis queued");
  await expect(row.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: /Retry failed/ })).toHaveCount(0);
});

test("Retry failed asks first, naming the count and where the frames go", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  setStatus(segmentId, "failed");

  await page.goto(`/cameras?camera=${cameraId}`);
  const panel = page.locator("section", { hasText: "Stored segments" });
  await panel.getByRole("button", { name: "Retry failed (1)" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Analyse 1 failed segment again?");
  await expect(dialog).toContainText(/OpenRouter|the VLM at|local model/);
  await dialog.getByRole("button", { name: "Analyse again" }).click();
  await expect(dialog).toBeHidden();
  await expect(panel.getByRole("listitem")).toContainText("Analysis queued");
});

test("a stuck segment says so and can be retried", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  setStatus(segmentId, "stuck");

  await page.goto(`/cameras?camera=${cameraId}`);
  const row = page.locator("section", { hasText: "Stored segments" }).getByRole("listitem");
  await expect(row).toContainText("Analysis stuck");
  await row.getByRole("button", { name: "Retry" }).click();
  await expect(row).toContainText("Analysis queued");
});
