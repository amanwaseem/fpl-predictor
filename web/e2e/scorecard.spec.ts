import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// The committed scores, which the API serves projected: every figure on the
// Track record page should agree with them (#195).
type Week = { gameweek: number; mae: number; n: number };
type Scored = {
  role: string;
  per_gameweek: Week[];
  gameweeks_scored: number[];
  gameweeks_pending: number[];
  gameweeks_missed: number[];
  pooled: { all: { mae: number } | null };
};
const scores: { models: Record<string, Scored> } = JSON.parse(
  readFileSync(new URL("../../scores/cumulative.json", import.meta.url), "utf8"),
);
const models = Object.values(scores.models);
const main = models.find((m) => m.role === "main")!;
const baseline = models.find((m) => m.role === "baseline")!;

// Average error pooled over player rows, as the scorecard pools it.
const pooled = (weeks: Week[]) =>
  weeks.reduce((sum, w) => sum + w.mae * w.n, 0) / weeks.reduce((sum, w) => sum + w.n, 0);
const shared = main.per_gameweek.filter((w) =>
  baseline.per_gameweek.some((b) => b.gameweek === w.gameweek),
);
const formOn = (gw: number) => baseline.per_gameweek.find((b) => b.gameweek === gw)!;
const better =
  ((pooled(shared.map((w) => formOn(w.gameweek))) - pooled(shared)) /
    pooled(shared.map((w) => formOn(w.gameweek)))) *
  100;

// Weeks with nothing published: missed by a model, logged (scored or
// pending) by none.
const logged = new Set(models.flatMap((m) => [...m.gameweeks_scored, ...m.gameweeks_pending]));
const missed = [...new Set(models.flatMap((m) => m.gameweeks_missed))]
  .filter((gw) => !logged.has(gw))
  .sort((a, b) => a - b);

async function openScorecard(page: Page) {
  await page.goto("/track-record");
  await page.getByText("Every model and the full scorecard").click();
}

test("the track record leads with how far off our predictions are, against going by form", async ({
  page,
}) => {
  await page.goto("/track-record");
  const answer = page.getByText(/^On average our predictions are/);
  // Pooled from the weeks, as a promotion makes ours more than one model's.
  await expect(answer).toContainText(
    `${pooled(main.per_gameweek).toFixed(2)} points off per player`,
  );
  await expect(answer).toContainText(
    Math.abs(better) < 0.05
      ? "level with going by form"
      : `${Math.abs(better).toFixed(1)}% ${better > 0 ? "more" : "less"} accurate than going by form`,
  );
});

test("each week on the chart gives both figures and links to its predictions", async ({ page }) => {
  await page.goto("/track-record");
  const week = shared.at(-1)!;
  const link = page.getByRole("link", {
    name: `GW${week.gameweek}: our predictions ${week.mae.toFixed(2)}, going by form ${formOn(week.gameweek).mae.toFixed(2)} points off per player`,
  });
  await expect(link).toHaveAttribute("href", `/gameweeks/${week.gameweek}`);
});

test.describe("on a touch screen", () => {
  test.use({ hasTouch: true });

  test("the first tap on a week shows its figures, and the second opens it", async ({ page }) => {
    await page.goto("/track-record");
    const gameweek = shared.at(-1)!.gameweek;
    const link = page.getByRole("link", { name: new RegExp(`^GW${gameweek}:`) });
    await link.tap();
    await expect(page.locator(".chart-tip")).toContainText(`GW${gameweek}`);
    await expect(page).toHaveURL(/\/track-record$/);
    await link.tap();
    await expect(page).toHaveURL(new RegExp(`/gameweeks/${gameweek}$`));
  });
});

test("missed gameweeks are listed, not left out", async ({ page }) => {
  test.skip(missed.length === 0, "no gameweek has been missed");
  // The API works a miss out from which gameweeks have finished, and the
  // database in CI holds the log but no football data, so it finds none
  // there. The committed scores say which weeks were missed.
  await page.route("**/v1/scorecard", async (route) => {
    const response = await route.fetch();
    const card: { models: { model_version: string; gameweeks_missed: number[] }[] } =
      await response.json();
    for (const model of card.models)
      model.gameweeks_missed = scores.models[model.model_version]?.gameweeks_missed ?? [];
    await route.fulfill({ response, json: card });
  });
  await page.goto("/track-record");
  const weeks =
    missed.length > 1
      ? `gameweeks ${missed.slice(0, -1).join(", ")} and ${missed.at(-1)}`
      : `gameweek ${missed[0]}`;
  await expect(page.getByRole("article").filter({ hasText: "Misses included" })).toContainText(
    `Nothing was published before the deadline for ${weeks}`,
  );
});

// Against the committed log, projected: baseline-v1 has been scored since GW4.
test("the full scorecard shows every model beside the baseline", async ({ page }) => {
  await openScorecard(page);
  const pooledTable = page.getByRole("table", { name: "Pooled" });
  await expect(pooledTable.getByRole("rowheader", { name: "baseline-v1" })).toBeVisible();
  await expect(pooledTable.getByRole("rowheader", { name: "form-fixture-v1" })).toBeVisible();
});

test("each scored entry links to its history in the log", async ({ page }) => {
  await openScorecard(page);
  const byWeek = page.getByRole("table", { name: "By gameweek" });
  // The row's own link goes to the gameweek; the figure's, to the log.
  const gw4 = byWeek.getByRole("row", { name: /^GW4\b/ }).getByRole("link", { name: /^\d/ });
  await expect(gw4).toHaveAttribute(
    "href",
    "https://github.com/amanwaseem/fpl-predictor/commits/main/predictions/gw04_baseline-v1.csv",
  );
});

test("the home page leaves the record to Track record, in the menu", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /^Top picks for gameweek/ })).toBeVisible();
  await expect(page.getByLabel("Status")).toContainText("Season");
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByText(/average miss per player/)).toHaveCount(0);
  // Models are named on Track record only (#194): no model code or statistic here.
  await expect(page.getByRole("main")).not.toContainText(
    /baseline-v1|form-fixture-v1|gbm-v1|RMSE|Form & fixtures|Machine learning|Baseline/,
  );
  await page
    .getByRole("navigation", { name: "Main" })
    .getByRole("link", { name: "Track record" })
    .click();
  await expect(page).toHaveURL(/\/track-record$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Every prediction, scored");
});
