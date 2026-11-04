import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

import { apiFetch, cameraWithSegment, createCamera, unique } from "./helpers";
import { E2E_API_URL as API, E2E_PYTHON, ROOT } from "./stack";

// The Triggers pages (step 5.5). The e2e stack has no VLM: a rule saved here can't be compiled
// (the query worker fails at once, tests/e2e_stack.py), so compiled triggers, their tests and
// events are seeded in the e2e database (tests/e2e_db.py).

function db(...args: string[]): string {
  return execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", ...args], { cwd: ROOT }).toString().trim();
}

test("a new trigger is saved disabled; a rule that can't be compiled can't be tested or enabled", async ({ page }) => {
  const camera = await createCamera({});
  const name = `Bag ${unique()}`;
  await page.goto("/triggers/new");
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Report when…").fill("Report when someone places an object and leaves it behind");
  const create = page.getByRole("button", { name: "Create trigger" });
  await expect(create).toBeDisabled(); // no camera yet
  await page.getByRole("group", { name: "Trigger cameras" }).getByRole("button", { name: camera.display_name }).click();
  await create.click();

  await expect(page).toHaveURL(/\/triggers\/[0-9a-f-]{36}$/);
  // No VLM in the e2e stack: the compile fails, and says so.
  const understood = page.getByLabel("Understood as");
  await expect(understood).toContainText("Couldn't compile", { timeout: 30_000 });
  await expect(understood.getByRole("button", { name: "Compile again" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Enable" })).toBeDisabled();
  await expect(page.getByLabel("Test and enable")).toContainText("must be compiled");

  await page.goto("/triggers");
  const card = page.getByRole("listitem").filter({ hasText: name });
  await expect(card).toContainText("Couldn't compile");
  await expect(card).toContainText(camera.display_name);
});

test("a compiled trigger shows what it understood, its test results, and can be enabled", async ({ page }) => {
  const { cameraId, segmentId, name: cameraName } = await cameraWithSegment();
  const name = `Bag ${unique()}`;
  const triggerId = db("seed-trigger", cameraId, "--name", name);
  const seededTest = db("seed-trigger-test", triggerId, segmentId);
  // Running a test needs the embedding models: the request is answered with the seeded one.
  let asked: unknown = null;
  await page.route(`${API}/triggers/${triggerId}/test`, async (route) => {
    asked = route.request().postDataJSON();
    const body = await (await apiFetch(`${API}/triggers/${triggerId}/tests/${seededTest}`)).json();
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto(`/triggers/${triggerId}`);
  await expect(page.getByLabel("Understood as")).toContainText(
    "place object → leave, or leave object behind, within 30 s",
  );
  await expect(page.getByLabel("Understood as")).toContainText("Needs: person, portable object");
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue(name);

  // The latest test is shown at once; a new one can be run.
  const result = page.getByRole("region", { name: "Test result" });
  await expect(result.getByRole("status")).toHaveText(
    "1 would report, 1 rejected by the VLM in 2 h of stored footage",
  );
  await page.getByRole("group", { name: "Test on" }).getByRole("button", { name: "Last 24 hours" }).click();
  await page.getByRole("button", { name: "Run test" }).click();
  await expect.poll(() => asked).toEqual({ source: "last_24h", upload_id: null, start: null, end: null });
  const matches = result.getByRole("list", { name: "Would-be events" }).getByRole("listitem");
  await expect(matches).toHaveCount(2);
  await expect(matches.nth(0)).toContainText("Would report");
  await expect(matches.nth(1)).toContainText("Rejected by the VLM");
  await expect(matches.nth(1)).toContainText("The person takes the bag along.");
  await matches.nth(0).getByRole("button").click();
  await expect(result).toContainText("Part 1 of 1");

  // Settings are saved; verification can be switched off.
  await page.getByLabel("Cooldown (minutes)").fill("5");
  await page.getByRole("checkbox").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(async () => (await (await apiFetch(`${API}/triggers/${triggerId}`)).json()).cooldown_seconds).toBe(300);
  const saved = await (await apiFetch(`${API}/triggers/${triggerId}`)).json();
  expect(saved.compiled.requires_vlm_verification).toBe(false);

  await page.getByRole("button", { name: "Enable" }).click();
  await expect(page.getByRole("button", { name: "Disable" })).toBeVisible();
  await page.goto("/triggers");
  const card = page.getByRole("listitem").filter({ hasText: name });
  await expect(card).toContainText("Enabled");
  await expect(card).toContainText(cameraName);
  await expect(card).toContainText("No VLM verification");
});

test("deleting a trigger says how many events go with it", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  const name = `Bag ${unique()}`;
  const triggerId = db("seed-trigger", cameraId, "--name", name, "--enabled");
  const eventId = db("seed-event", triggerId, segmentId);

  await page.goto("/triggers");
  const card = page.getByRole("listitem").filter({ hasText: name });
  await expect(card).toContainText("1 event, 1 new");
  await card.getByRole("button", { name: `Delete ${name}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Its event and their exports will be deleted too");
  await dialog.getByRole("button", { name: "Delete trigger" }).click();
  await expect(card).toHaveCount(0);
  expect((await apiFetch(`${API}/events/${eventId}`)).status).toBe(404);
});
