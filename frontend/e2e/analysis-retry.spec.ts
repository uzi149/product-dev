import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

import { cameraWithSegment } from "./helpers";
import { E2E_PYTHON, ROOT } from "./stack";

// The e2e stack runs with analysis off: segments stay "Analysis queued" and nothing fails by
// itself, so these tests set a segment's state in the e2e database (tests/e2e_db.py).

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
