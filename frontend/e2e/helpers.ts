import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect } from "@playwright/test";

import { E2E_API_URL as API } from "./stack";

export const unique = () => `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

let videoPath = "";

/** 30 s with a moving pattern from 12 s to 16 s: one stored segment. Made once, with ffmpeg. */
function activityVideo(): string {
  if (videoPath) return videoPath;
  const folder = mkdtempSync(path.join(tmpdir(), "sentinel-e2e-segment-"));
  videoPath = path.join(folder, "clip.mp4");
  execFileSync("ffmpeg", [
    "-y", "-v", "error",
    "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=15:duration=30",
    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=30",
    "-filter_complex", "[0][1]overlay=x='60*(t-12)':y=120:enable='between(t,12,16)'",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "30", videoPath,
  ]);
  return videoPath;
}

/** A camera made through the API, without the UI. */
export async function createCamera(body: Record<string, unknown>): Promise<{ id: string; display_name: string }> {
  const response = await fetch(`${API}/cameras`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: unique(), source_type: "file", enabled: true, retention_days: 14, ...body }),
  });
  expect(response.status).toBe(201);
  return response.json();
}

/** Uploads the activity video to a camera and waits for its segment; returns the segment id. */
export async function addSegment(cameraId: string, startTime: string): Promise<string> {
  const before: { id: string }[] = await (await fetch(`${API}/segments?camera_id=${cameraId}`)).json();
  const form = new FormData();
  form.set("camera_id", cameraId);
  form.set("start_time", startTime);
  form.set("file", new Blob([readFileSync(activityVideo())], { type: "video/mp4" }), "clip.mp4");
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
    .toBe(before.length + 1);
  return segments.find((s) => !before.some((b) => b.id === s.id))!.id;
}

/** A file camera with one stored segment, made through the API; returns both ids. */
export async function cameraWithSegment(): Promise<{ cameraId: string; segmentId: string; name: string }> {
  const camera = await createCamera({});
  const segmentId = await addSegment(camera.id, "2026-03-07T09:05:00Z");
  return { cameraId: camera.id, segmentId, name: camera.display_name };
}
