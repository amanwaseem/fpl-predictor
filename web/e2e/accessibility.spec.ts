// Every page through axe (#176): the WCAG A and AA rules it can check
// automatically. Not the whole of accessibility, but the part a test can hold.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const PAGES = ["/", "/gameweeks", "/optimise", "/about", "/no-such-page"];

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

test("a player page has no automatically detectable accessibility problems", async ({
  page,
  request,
}) => {
  const { gameweeks } = await (await request.get("/v1/gameweeks")).json();
  const logged = gameweeks
    .filter((g: { models_logged: string[] }) => g.models_logged.length)
    .at(-1);
  const rows = await (
    await request.get(
      `/v1/gameweeks/${logged.gameweek}/predictions?model=${logged.models_logged[0]}&limit=1`,
    )
  ).json();
  await page.goto(`/players/${rows.items[0].player_id}`);
  await page.getByRole("heading", { level: 1 }).waitFor();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
});
