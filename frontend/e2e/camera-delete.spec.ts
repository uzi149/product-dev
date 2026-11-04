import { expect, test, type Page } from "@playwright/test";

import { cameraWithSegment, createCamera, unique } from "./helpers";
import { E2E_API_URL as API, E2E_RTSP_URL } from "./stack";

// Deleting and disabling cameras (step 3.6, ADR 0026).

/** The camera's card (a toast naming the camera is a list item too). */
const cardOf = (page: Page, name: string) =>
  page.getByRole("listitem").filter({ has: page.getByRole("button", { name: `Delete ${name}` }) });

test("a file camera is deleted with its footage after a confirmation", async ({ page }) => {
  const { cameraId, name } = await cameraWithSegment();

  await page.goto(`/cameras?camera=${cameraId}`);
  const card = cardOf(page, name);
  await card.getByRole("button", { name: `Delete ${name}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(`Delete ${name}?`);
  await expect(dialog).toContainText("1 segment, 0 analysed windows, 2 no-change periods and 1 upload");
  await dialog.getByRole("button", { name: "Delete camera" }).click();

  await expect(dialog).toBeHidden();
  await expect(card).toHaveCount(0);
  await expect(page).not.toHaveURL(/camera=/);
  expect((await fetch(`${API}/cameras/${cameraId}`)).status).toBe(404);
  expect(await (await fetch(`${API}/segments?camera_id=${cameraId}`)).json()).toEqual([]);
});

test("a recording camera is disabled first, then deleted", async ({ page }) => {
  const name = unique();
  const camera = await createCamera({ display_name: name, source_type: "rtsp", source_uri: `${E2E_RTSP_URL}/cam-01` });

  await page.goto(`/cameras?camera=${camera.id}`);
  const card = cardOf(page, name);
  await card.getByRole("button", { name: `Delete ${name}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Disable it first");
  await expect(dialog.getByRole("button", { name: "Delete camera" })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await card.getByRole("button", { name: "Disable" }).click();
  await expect(card).toContainText("disabled");
  await expect(card.getByRole("button", { name: "Enable" })).toBeVisible();

  // Live ingestion lets go of it at its next camera reload (5 s in the e2e stack).
  await card.getByRole("button", { name: `Delete ${name}` }).click();
  await expect(dialog.getByRole("button", { name: "Delete camera" })).toBeEnabled({ timeout: 30_000 });
  await dialog.getByRole("button", { name: "Delete camera" }).click();
  await expect(card).toHaveCount(0);
});
