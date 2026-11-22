// Every page through axe (#176): the WCAG A and AA rules it can check
// automatically. Not the whole of accessibility, but the part a test can hold.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { PLAYER, stubPlayer } from "./fixtures";

const PAGES = ["/", "/gameweeks", "/optimise", "/track-record", "/about", "/no-such-page"];

for (const path of PAGES) {
  test(`${path} has no automatically detectable accessibility problems`, async ({ page }) => {
    await page.goto(path);
    await page.getByRole("heading", { level: 1 }).waitFor();
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
  });
}

test("a player page has no automatically detectable accessibility problems", async ({ page }) => {
  await stubPlayer(page);
  await page.goto(`/players/${PLAYER.player_id}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
});

// The scorecard tables are folded away on Track record, so axe sees them only open.
test("the full scorecard has no automatically detectable accessibility problems", async ({
  page,
}) => {
  await page.goto("/track-record");
  await page.getByText("Every model and the full scorecard").click();
  await page.getByRole("table", { name: "Pooled" }).waitFor();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
});
