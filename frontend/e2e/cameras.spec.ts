import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

import { E2E_API_URL as API } from "./stack";

const unique = () => `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

let videoPath = "";
let stillPath = "";

test.beforeAll(async () => {
  try {
    const health = await fetch(`${API}/health`);
    expect((await health.json()).status).toBe("ok");
  } catch {
    throw new Error(
      `The e2e API is not answering at ${API}. Playwright starts it (python -m tests.e2e_stack); ` +
        "see data/e2e/logs/ and check that `docker compose --profile sim up -d` is running.",
    );
  }
  // A test video made with ffmpeg, so no video file is committed: a still grey scene for
  // 40 s with a moving pattern from 15 s to 20 s (gone at 21 s). Only 5-31 s (activity + 10 s
  // pre/post roll) should be stored.
  const folder = mkdtempSync(path.join(tmpdir(), "sentinel-e2e-"));
  videoPath = path.join(folder, "clip.mp4");
  execFileSync("ffmpeg", [
    "-y", "-v", "error",
    "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=40",
    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=40",
    "-filter_complex", "[0][1]overlay=x='60*(t-15)':y=120:enable='between(t,15,20)'",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "40", videoPath,
  ]);
  // 20 s of an empty grey scene with sensor-like noise: nothing happens, nothing is stored.
  stillPath = path.join(folder, "still.mp4");
  execFileSync("ffmpeg", [
    "-y", "-v", "error",
    "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=20,noise=alls=8:allf=t",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", stillPath,
  ]);
});

// RTSP cameras made here are disabled at the end, so the e2e ingestion stops trying to read them
// while the other tests run.
const rtspCameras: string[] = [];

test.afterAll(async () => {
  const cameras: { id: string; display_name: string }[] = await (await fetch(`${API}/cameras`)).json();
  for (const camera of cameras.filter((c) => rtspCameras.includes(c.display_name))) {
    await fetch(`${API}/cameras/${camera.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: false }),
    });
  }
});

async function addCamera(page: Page, name: string, rtspUrl?: string) {
  if (rtspUrl) rtspCameras.push(name);
  await page.goto("/cameras");
  // The camera list only loads after React has hydrated, so clicks before that would be lost.
  await expect(page.getByText("Loading cameras…")).toBeHidden();
  const dialog = page.getByRole("dialog");
  await page.getByRole("button", { name: "Add camera" }).first().click();
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Name").fill(name);
  if (rtspUrl) {
    await dialog.getByLabel("Type").selectOption("rtsp");
    await dialog.getByLabel("Stream URL").fill(rtspUrl);
  }
  await dialog.getByRole("button", { name: "Add camera" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("listitem").filter({ hasText: name })).toBeVisible();
}

test("the shell shows every screen in the sidebar and the API health", async ({ page }) => {
  await page.goto("/");
  const nav = page.locator("aside nav a");
  await expect(nav).toHaveText([
    "Overview", "Live cameras", "Events", "Investigate", "Triggers", "Cameras", "Settings",
  ]);
  await expect(page.locator("aside").getByText("Operational")).toBeVisible();

  for (const route of ["/events", "/investigate", "/triggers", "/settings"]) {
    await page.goto(route);
    await expect(page.getByText("Not built yet")).toBeVisible();
  }
});

test("add a camera, upload a video, and play the active part that was stored", async ({ page }) => {
  const name = unique();
  await addCamera(page, name);

  const upload = page.locator("section", { hasText: "Upload a video" });
  await upload.getByLabel("Video file").setInputFiles(videoPath);
  await upload.getByLabel(/Recording started/).fill("2026-03-07T09:05");
  await upload.getByRole("button", { name: "Upload", exact: true }).click();
  // Processed in the background by the worker.
  await expect(upload.getByRole("status")).toContainText("clip.mp4: 1 active part stored", {
    timeout: 120_000,
  });
  const recent = page.locator("section", { hasText: "Recent uploads" }).getByRole("listitem");
  await expect(recent).toHaveCount(1);
  await expect(recent).toContainText("done");

  const row = page.locator("section", { hasText: "Stored segments" }).getByRole("listitem");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("0:26");
  await expect(row).toContainText("640×360");
  await expect(row).toContainText("h264");
  await expect(row).toContainText("Mar 07, 2026"); // shown in local time, same day in any timezone offset <= 9h

  await row.getByRole("button", { name: "Play" }).click();
  const video = page.locator("video");
  await expect(video).toBeVisible();
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState >= 2 && v.videoWidth), {
      timeout: 30_000,
    })
    .toBe(640);

  // The timeline opens zoomed to the footage of its day: the stored part and the still parts.
  const timeline = page.getByRole("region", { name: "Timeline" });
  await expect(timeline.getByRole("button", { name: "Fit" })).toHaveAttribute("aria-pressed", "true");
  await expect(timeline.getByLabel("Day")).toHaveValue("2026-03-07");
  await expect(timeline.locator('[data-kind="segment"]')).toHaveCount(1);
  await expect(timeline.locator('[data-kind="static"]')).toHaveCount(2);
  await expect(timeline).toContainText("0:26 stored");
  await timeline.locator('[data-kind="static"]').first().click();
  await expect(timeline.getByRole("img", { name: /Reference picture/ })).toBeVisible();
  await timeline.locator('[data-kind="segment"]').click();
  await expect(timeline.locator("video")).toBeVisible();
});

test("a video where nothing happens stores no footage", async ({ page }) => {
  const name = unique();
  await addCamera(page, name);

  const upload = page.locator("section", { hasText: "Upload a video" });
  await upload.getByLabel("Video file").setInputFiles(stillPath);
  await upload.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(upload.getByRole("status")).toContainText(
    "still.mp4: No activity found in this video",
    { timeout: 120_000 },
  );
  await expect(
    page.locator("section", { hasText: "Recent uploads" }).getByRole("listitem"),
  ).toContainText("done");
  await expect(page.getByText("No footage stored for this camera yet.")).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: name })).toContainText("0 segments");
});

test("an invalid upload shows the API's error and stores nothing", async ({ page }) => {
  const name = unique();
  await addCamera(page, name);

  const upload = page.locator("section", { hasText: "Upload a video" });
  await upload.getByLabel("Video file").setInputFiles({
    name: "notes.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("this is not a video"),
  });
  await upload.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText(/not a readable video/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("No footage stored for this camera yet.")).toBeVisible();
});

test("an RTSP camera hides its password and reports the connection test", async ({ page }) => {
  const name = unique();
  await addCamera(page, name, "rtsp://admin:s3cret@127.0.0.1:1/none");

  const card = page.getByRole("listitem").filter({ hasText: name });
  await expect(card).toContainText("rtsp://***@127.0.0.1:1/none");
  await expect(page.locator("body")).not.toContainText("s3cret");

  await card.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText(new RegExp(`${name}: `))).toBeVisible({ timeout: 20_000 });
});

test("editing keeps the stream URL write-only", async ({ page }) => {
  const name = unique();
  await addCamera(page, name, "rtsp://admin:s3cret@127.0.0.1:1/none");

  await page.getByRole("listitem").filter({ hasText: name }).getByRole("button", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Stream URL")).toHaveValue("");
  await expect(dialog.getByLabel("Stream URL")).toHaveAttribute("placeholder", /Unchanged/);
  await dialog.getByLabel("Keep footage (days)").fill("30");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: name })).toContainText("kept 30 days");
  await expect(page.getByRole("listitem").filter({ hasText: name })).toContainText(
    "rtsp://***@127.0.0.1:1/none",
  );
});

test("the UI says so when the API is not reachable", async ({ page }) => {
  await page.route(`${API}/**`, (route) => route.abort());
  await page.goto("/cameras");
  await expect(page.getByRole("alert").filter({ hasText: "The API is not reachable." })).toBeVisible();
});
