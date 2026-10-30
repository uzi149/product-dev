import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

import { addSegment, cameraWithSegment, unique } from "./helpers";
import { E2E_API_URL as API, E2E_PYTHON, ROOT } from "./stack";

// The Investigate page (steps 4.5, 4.6). The e2e stack has no models, so a real query would find
// nothing: these tests seed queries in the e2e database (tests/e2e_db.py) on stored segments.

function seed(...args: string[]): string {
  return execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", "seed-query", ...args], { cwd: ROOT })
    .toString()
    .trim();
}

test("a finished query shows its stages, badges and plays the evidence with a gap", async ({ page }) => {
  const { cameraId, segmentId, name } = await cameraWithSegment();
  const later = await addSegment(cameraId, "2026-03-07T09:20:00Z");
  const queryId = seed(segmentId, later);

  await page.goto(`/investigate?q=${queryId}`);
  const progress = page.getByRole("list", { name: "Progress" });
  await expect(progress).toContainText("Searching indexed footage…");
  await expect(progress).toContainText("Found 2 possible matches");
  await expect(progress).toContainText("Verifying 2 sequences…");
  await expect(progress).toContainText("1 verified result");
  await expect(progress.locator('[data-status="done"]')).toHaveCount(4);

  const results = page.getByRole("list", { name: "Results" }).getByRole("listitem");
  await expect(results).toHaveCount(2);
  await expect(results.nth(0)).toContainText("Verified 90%");
  await expect(results.nth(0)).toContainText(name);
  await expect(results.nth(0)).toContainText("1 gap");
  await expect(results.nth(1)).toContainText("Couldn't be verified");

  // The first result plays: part 1, then the gap card, then part 2.
  const evidence = page.getByRole("region", { name: "Evidence" });
  // Above the player: the whole description and the VLM's reason.
  await expect(evidence).toContainText("DescriptionA test pattern moves across a grey picture.");
  await expect(evidence).toContainText("Why the VLM verified itA test pattern moves across a grey picture.");
  await expect(evidence).toContainText("Verified 90%");
  await expect(evidence).toContainText("Part 1 of 2");
  await expect(evidence.locator('[data-kind="gap"]')).toHaveCount(1);
  await expect(evidence.getByRole("status")).toContainText("5 minutes 12 seconds not shown", { timeout: 20_000 });
  await expect(evidence).toContainText("Part 2 of 2", { timeout: 10_000 });
  await expect(evidence).toContainText("End of the evidence", { timeout: 15_000 });

  // Another result plays when picked.
  await results.nth(1).getByRole("button").click();
  await expect(evidence).toContainText("Part 1 of 1");
  await expect(evidence).toContainText("DescriptionA grey picture.");
  await expect(evidence).toContainText(
    "Why it couldn't be verifiedcouldn't be verified: the VLM was not reachable",
  );
});

test("the understood chips show what the question was read as", async ({ page }) => {
  const { cameraId, name } = await cameraWithSegment();
  const pending = seed("--pending", cameraId);
  // The form's query is answered with the seeded one, so no real query is queued (no models).
  const sent: unknown[] = [];
  await page.route(`${API}/queries`, async (route) => {
    sent.push(route.request().postDataJSON());
    const state = await (await fetch(`${API}/queries/${pending}`)).json();
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(state) });
  });

  await page.goto("/investigate");
  await page.getByLabel("Question").fill(`a bag on ${name} yesterday`);
  await page.getByRole("button", { name: "Search", exact: true }).click();

  const chips = page.getByLabel("Understood");
  await expect(chips).toContainText(`Camera: ${name}`);
  await expect(chips).toContainText("Time: Yesterday");
  await expect(chips).toContainText("searching for");
  await expect(page).toHaveURL(new RegExp(`\\?q=${pending}$`));
  expect(sent[0]).toMatchObject({ query_text: `a bag on ${name} yesterday` });
  expect(typeof (sent[0] as { timezone: string }).timezone).toBe("string");

  // Removing the camera chip searches again with camera names left in the text.
  await chips.getByRole("button", { name: `Remove Camera: ${name}` }).click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1]).toMatchObject({ parse_cameras: false });

  // Nobody takes the pending query: the page says the query worker isn't running.
  await expect(page.getByRole("alert").filter({ hasText: "query worker" })).toContainText("isn't running", {
    timeout: 20_000,
  });
});

// Query history (step 4.6): the e2e database is shared by every test, so each test looks only
// for its own questions.

test("past searches are listed, open without searching again and come back after leaving", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  const later = await addSegment(cameraId, "2026-03-07T09:20:00Z");
  const tag = unique();
  const older = `${tag} older search`;
  const newer = `${tag} newer search`;
  const olderId = seed("--text", older, segmentId, later);
  seed("--text", newer, segmentId, later);

  // Nothing opened in this browser yet: an empty page with the list open.
  await page.goto("/investigate");
  const history = page.getByRole("region", { name: "Recent searches" });
  const toggle = history.getByRole("button", { name: /^Recent searches/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  const lines = history.getByRole("listitem");
  await expect(lines.filter({ hasText: newer })).toContainText("1 verified, 1 possible");
  const texts = await lines.allTextContents();
  const at = (text: string) => texts.findIndex((t) => t.includes(text));
  expect(at(newer)).toBeGreaterThanOrEqual(0);
  expect(at(newer)).toBeLessThan(at(older)); // newest first

  // Opening one shows its stored results; the list folds away.
  await history.getByRole("button", { name: new RegExp(`^${older}`) }).click();
  await expect(page).toHaveURL(new RegExp(`\\?q=${olderId}$`));
  await expect(page.getByRole("list", { name: "Results" }).getByRole("listitem")).toHaveCount(2);
  await expect(toggle).toHaveAttribute("aria-expanded", "false");

  // Leaving the page and coming back through the sidebar shows the same query again.
  await page.getByRole("link", { name: "Cameras", exact: true }).click();
  await expect(page).toHaveURL(/\/cameras/);
  await page.getByRole("link", { name: "Investigate" }).click();
  await expect(page).toHaveURL(new RegExp(`\\?q=${olderId}$`));
  await expect(page.getByRole("list", { name: "Results" }).getByRole("listitem")).toHaveCount(2);

  // New search empties the page, and it stays empty next time.
  await page.getByRole("button", { name: "New search" }).click();
  await expect(page).toHaveURL(/\/investigate$/);
  await expect(page.getByRole("list", { name: "Results" })).toHaveCount(0);
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await page.goto("/investigate");
  await expect(page.getByRole("list", { name: "Results" })).toHaveCount(0);
});

test("Run again opens a new query; deleting a search removes it and clears the page", async ({ page }) => {
  const { cameraId, segmentId } = await cameraWithSegment();
  const later = await addSegment(cameraId, "2026-03-07T09:20:00Z");
  const tag = unique();
  const first = `${tag} first search`;
  const again = `${tag} asked again`;
  const firstId = seed("--text", first, segmentId, later);
  const againId = seed("--pending", "--text", again, cameraId);
  // The re-run is answered with the seeded pending query, so no real query is queued (no models).
  let reruns = 0;
  await page.route(`${API}/queries/${firstId}/rerun`, async (route) => {
    reruns += 1;
    const state = await (await fetch(`${API}/queries/${againId}`)).json();
    await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify(state) });
  });

  await page.goto(`/investigate?q=${firstId}`);
  await expect(page.getByRole("list", { name: "Results" }).getByRole("listitem")).toHaveCount(2);
  await page.getByRole("button", { name: "Run again", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`\\?q=${againId}$`));
  expect(reruns).toBe(1);

  // Delete the earlier search from the list: it goes, with its results.
  const history = page.getByRole("region", { name: "Recent searches" });
  await history.getByRole("button", { name: /^Recent searches/ }).click();
  await history.getByRole("button", { name: `Delete: ${first}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(`${first} and its 2 results will be deleted`);
  await dialog.getByRole("button", { name: "Delete search" }).click();
  await expect(history.getByRole("listitem").filter({ hasText: first })).toHaveCount(0);
  expect((await fetch(`${API}/queries/${firstId}`)).status).toBe(404);
  await expect(page).toHaveURL(new RegExp(`\\?q=${againId}$`)); // the open query stays

  // Deleting the open search clears the page.
  await history.getByRole("button", { name: `Delete: ${again}` }).click();
  await dialog.getByRole("button", { name: "Delete search" }).click();
  await expect(history.getByRole("listitem").filter({ hasText: again })).toHaveCount(0);
  await expect(page).toHaveURL(/\/investigate$/);
  await expect(page.getByLabel("Understood")).toHaveCount(0);
});

test("the recent searches can be searched, and Show more loads the next page", async ({ page }) => {
  const tag = unique();
  execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", "seed-history", `${tag} parking lot`, "25"], { cwd: ROOT });
  execFileSync(E2E_PYTHON, ["-m", "tests.e2e_db", "seed-history", `${tag} front door`, "1"], { cwd: ROOT });

  await page.goto("/investigate");
  const history = page.getByRole("region", { name: "Recent searches" });
  const box = history.getByRole("searchbox", { name: "Search the recent searches" });
  const lines = history.getByRole("listitem");

  // Every word, in any order and any case.
  await box.fill(`LOT ${tag}`);
  await expect(history.getByRole("button", { name: /^Recent searches/ })).toContainText("(25 matching)");
  await expect(lines).toHaveCount(20);
  await expect(lines.first()).toContainText(`${tag} parking lot 25`); // newest first
  await history.getByRole("button", { name: "Show more" }).click();
  await expect(lines).toHaveCount(25);
  await expect(lines.last()).toContainText(`${tag} parking lot 01`);
  await expect(history.getByRole("button", { name: "Show more" })).toHaveCount(0);

  await box.fill(`door ${tag}`);
  await expect(lines).toHaveCount(1);
  await expect(lines.first()).toContainText(`${tag} front door 01`);

  await box.fill(`${tag} bicycle`);
  await expect(lines).toHaveCount(0);
  await expect(history).toContainText(`No searches with`);
});
