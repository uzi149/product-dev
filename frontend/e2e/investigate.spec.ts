import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

import { addSegment, cameraWithSegment } from "./helpers";
import { E2E_API_URL as API, E2E_PYTHON, ROOT } from "./stack";

// The Investigate page (step 4.5). The e2e stack has no models, so a real query would find
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
  await expect(evidence).toContainText("Part 1 of 2");
  await expect(evidence.locator('[data-kind="gap"]')).toHaveCount(1);
  await expect(evidence.getByRole("status")).toContainText("5 minutes 12 seconds not shown", { timeout: 20_000 });
  await expect(evidence).toContainText("Part 2 of 2", { timeout: 10_000 });
  await expect(evidence).toContainText("End of the evidence", { timeout: 15_000 });

  // Another result plays when picked.
  await results.nth(1).getByRole("button").click();
  await expect(evidence).toContainText("Part 1 of 1");
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
  await page.getByRole("button", { name: "Search" }).click();

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
