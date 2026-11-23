import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { PLAYER, stubPlayer } from "./fixtures";

// The newest gameweek of the newest season in the committed log, read from git
// rather than written in: every new entry moves it, and a test pinned to one
// number turns the next entry PR's ci red (#118, #134). /gameweeks shows the
// latest season in the database, and fpl-project loads every season's
// directory, so this follows the same layout: predictions/ for 2026/27,
// predictions-2027-28/ and so on after (fpl/log/contract.py season_directory).
// Against a deployment, it must be loaded from the same commit as this checkout.
const SEASON_DIR = /^predictions(?:-(\d{4})-(\d{2}))?$/;
const ENTRY_FILENAME = /^gw(\d{2})_([^_]+)\.csv$/; // fpl/log/contract.py ENTRY_FILENAME

function newestLoggedGameweek(): number {
  const root = new URL("../..", import.meta.url).pathname;
  const files = execFileSync("git", ["ls-files", "--", ":(glob)predictions*/*.csv"], {
    cwd: root,
    encoding: "utf8",
  }).split("\n");
  const bySeason = new Map<string, number[]>();
  for (const file of files) {
    const [dir, name, ...rest] = file.split("/");
    const season = SEASON_DIR.exec(dir ?? "");
    const entry = ENTRY_FILENAME.exec(name ?? "");
    if (!season || !entry || rest.length) continue;
    const key = season[1] ? `${season[1]}/${season[2]}` : ""; // "" sorts first: the first season
    bySeason.set(key, [...(bySeason.get(key) ?? []), Number(entry[1])]);
  }
  const newestSeason = [...bySeason.keys()].sort().at(-1);
  if (newestSeason === undefined)
    throw new Error(`No committed entries under predictions*/ in ${root}`);
  return Math.max(...bySeason.get(newestSeason)!);
}

test("/gameweeks opens the newest gameweek's entry", async ({ page }) => {
  await page.goto("/gameweeks");
  await expect(page).toHaveURL(new RegExp(`/gameweeks/${newestLoggedGameweek()}$`));
  await expect(page.getByRole("table", { name: "Predictions" }).getByRole("row")).not.toHaveCount(
    1,
  );
});

test("filters narrow the entry and sorting reorders it", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  const table = page.getByRole("table", { name: "Predictions" });
  await page.getByRole("group", { name: "Position" }).getByRole("button", { name: "GK" }).click();
  await expect(page).toHaveURL(/position=GKP/);
  await expect(table.locator("tbody tr").first()).toHaveAttribute("data-position", "GKP");
  await expect(table.locator('tbody tr:not([data-position="GKP"])')).toHaveCount(0);

  await table.getByRole("button", { name: "Price" }).click();
  await expect(page).toHaveURL(/sort=price/);
  // Polled: reading the prices once can catch the table before it re-sorts,
  // which failed about one run in five.
  // A failure names the first pair out of order, not just "expected true".
  await expect
    .poll(async () => {
      const prices = await table.locator("tbody td.price").allTextContents();
      const values = prices.map((p) => Number(p.replace(/[£m]/g, "")));
      if (values.length === 0) return "no prices";
      const i = values.findIndex((v, j) => j > 0 && values[j - 1] < v);
      return i === -1 ? "descending" : `£${values[i - 1]}m before £${values[i]}m`;
    })
    .toBe("descending");
});

// #197: arrows, short position tabs, filter chips, opponents, Actual only once scored.
test("the arrows step through the logged gameweeks and stop at the ends", async ({
  page,
  request,
}) => {
  const { models } = await (await request.get("/v1/models")).json();
  const logged = [
    ...new Set<number>(models.flatMap((m: { gameweeks_logged: number[] }) => m.gameweeks_logged)),
  ].sort((a, b) => a - b);
  await page.goto(`/gameweeks/${logged[0]}?position=MID`);
  await expect(page.getByRole("link", { name: `Gameweek ${logged[0]! - 1}` })).toHaveCount(0);
  await page.getByRole("link", { name: `Gameweek ${logged[1]}` }).click();
  await expect(page).toHaveURL(new RegExp(`/gameweeks/${logged[1]}\\?position=MID$`));
  await page.goto(`/gameweeks/${logged.at(-1)}`);
  await expect(page.getByRole("link", { name: `Gameweek ${logged.at(-1)! + 1}` })).toHaveCount(0);
  // No browser dropdowns are left on the page.
  await expect(page.locator("select")).toHaveCount(0);
});

test("the position tabs fit a phone, with nothing cut off", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/gameweeks/8");
  const tabs = page.getByRole("group", { name: "Position" });
  for (const name of ["All", "GK", "DEF", "MID", "FWD"]) {
    const box = (await tabs.getByRole("button", { name }).boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(390);
  }
});

test("club, price and fitness filters combine, and the link keeps them", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  await page.getByRole("button", { name: "Filters" }).click();
  const clubs = page.getByRole("group", { name: "Clubs" });
  await clubs.getByRole("button", { name: "ARS" }).click();
  await clubs.getByRole("button", { name: "CHE" }).click();
  // The address changes in a transition, so the box is ticked a moment later.
  await page.getByLabel("Fit players only").click();
  await expect(page.getByLabel("Fit players only")).toBeChecked();
  await expect(page).toHaveURL(/team=ARS%2CCHE|team=ARS,CHE/);
  await expect(page).toHaveURL(/fit=1/);
  const rows = page.getByRole("table", { name: "Predictions" }).locator("tbody tr");
  await expect(rows.first()).toBeVisible();
  for (const team of await rows.evaluateAll((trs) => trs.map((tr) => tr.dataset.team)))
    expect(["ARS", "CHE"]).toContain(team);
  await expect(rows.locator(".pill")).toHaveCount(0);

  // A shared link brings the same view, the price included.
  await page.goto(`${new URL(page.url()).pathname}${new URL(page.url()).search}&max=6`);
  await expect(page.getByRole("button", { name: "Filters (3)" })).toBeVisible();
  const prices = await page
    .getByRole("table", { name: "Predictions" })
    .locator("tbody td.price")
    .allTextContents();
  for (const price of prices) expect(Number(price.replace(/[£m]/g, ""))).toBeLessThanOrEqual(6);
});

test("points per £m sorts by value for money", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  await page
    .getByRole("group", { name: "Sort by" })
    .getByRole("button", { name: "Points per £m" })
    .click();
  await expect(page).toHaveURL(/sort=value/);
  const table = page.getByRole("table", { name: "Predictions" });
  await expect(table.getByRole("columnheader", { name: /Per £m/ })).toBeVisible();
});

test("Actual shows only once a gameweek is scored", async ({ page, request }) => {
  const { models } = await (await request.get("/v1/models")).json();
  const all = models.flatMap((m: { gameweeks_logged: number[] }) => m.gameweeks_logged);
  const scored = new Set(models.flatMap((m: { gameweeks_scored: number[] }) => m.gameweeks_scored));
  const pending = all.find((gw: number) => !scored.has(gw));
  await page.goto(`/gameweeks/${[...scored][0]}`);
  const table = page.getByRole("table", { name: "Predictions" });
  await expect(table.getByRole("columnheader", { name: /Actual/ })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /Error/ })).toHaveCount(0);
  test.skip(pending === undefined, "every logged gameweek is scored");
  await page.goto(`/gameweeks/${pending}`);
  await expect(table.getByRole("columnheader", { name: /Predicted/ })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: /Actual/ })).toHaveCount(0);
});

test("every row shows its opponents, doubles and blanks included", async ({ page, request }) => {
  const detail = await (await request.get("/v1/gameweeks/8")).json();
  const fixture = (id: number, home: string, away: string, hour: number) => ({
    fixture_id: id,
    kickoff_utc: `2026-10-24T${String(hour).padStart(2, "0")}:00:00Z`,
    home,
    away,
    home_score: null,
    away_score: null,
    finished: false,
  });
  // Arsenal play twice, Chelsea once, and everyone else not at all.
  await page.route("**/v1/gameweeks/8", (route) =>
    route.fulfill({
      json: {
        ...detail,
        fixtures: [fixture(1, "ARS", "CHE", 12), fixture(2, "LIV", "ARS", 15)],
      },
    }),
  );
  await page.goto("/gameweeks/8?model=baseline-v1&team=ARS,CHE,LIV,MCI");
  const row = (team: string) =>
    page.locator(`table[aria-label="Predictions"] tbody tr[data-team="${team}"]`).first();
  await expect(row("ARS").locator(".opponent-col")).toHaveText("CHE (H), LIV (A)");
  await expect(row("CHE").locator(".opponent-col")).toHaveText("ARS (A)");
  await expect(row("MCI").locator(".opponent-col")).toHaveText("No match");
});

test("an entry shows its top fifty first, and more on asking", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  const rows = page.getByRole("table", { name: "Predictions" }).locator("tbody tr");
  await expect(rows).toHaveCount(50);
  await page.getByRole("button", { name: /Show more/ }).click();
  await expect(rows).toHaveCount(100);
  await expect(page).toHaveTitle(/^Gameweek 8 predictions · /);
});

test("back from a player returns to the rows the reader had expanded", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  const rows = page.getByRole("table", { name: "Predictions" }).locator("tbody tr");
  await page.getByRole("button", { name: /Show more/ }).click();
  await expect(rows).toHaveCount(100);
  await rows.nth(80).getByRole("link").click();
  await expect(page).toHaveURL(/\/players\//);
  await page.goBack();
  await expect(rows).toHaveCount(100);
});

test("one page's expanded rows do not carry over to another loaded in the same tab", async ({
  page,
}) => {
  // Both loaded directly: React Router gives each first entry the same key.
  await page.goto("/gameweeks/8?model=baseline-v1");
  const rows = page.getByRole("table", { name: "Predictions" }).locator("tbody tr");
  await page.getByRole("button", { name: /Show more/ }).click();
  await expect(rows).toHaveCount(100);
  await page.goto("/gameweeks/8?model=baseline-v1&position=MID");
  await expect(rows).toHaveCount(50);
});

test("a filter a link carries that no control can show is ignored", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1&position=GK&team=XYZ&sort=status");
  const rows = page.getByRole("table", { name: "Predictions" }).locator("tbody tr");
  await expect(rows).toHaveCount(50);
  await expect(page.getByText("No players match.")).toHaveCount(0);
});

test("a player's page shows our prediction beside what happened", async ({ page }) => {
  // The database in CI holds the log only, not football data: the player is stubbed.
  await stubPlayer(page);
  await page.goto("/players/42");
  await expect(page.getByRole("heading", { name: "Ada Tester" })).toBeVisible();
  const gw8 = page.getByRole("row", { name: /GW8/ });
  await expect(gw8).toContainText("CHE (H)");
  await expect(gw8).toContainText("5.10");
});

test("a player's page shows the model that was main that week, after a promotion", async ({
  page,
}) => {
  // #194: form-fixture-v1 was main to GW11 and retired; gbm-v1, the challenger
  // from GW9, is main from GW12. What users saw before a deadline stays.
  const range = (from: number, to: number) =>
    Array.from({ length: to - from + 1 }, (_, i) => from + i);
  const model = (version: string, logged: number[], role: string, retired?: number) => ({
    model_version: version,
    first_gameweek: logged[0],
    gameweeks_logged: logged,
    gameweeks_scored: [],
    gameweeks_missed: [],
    role,
    retired_after: retired ?? null,
    retired_from: retired ? "main" : null,
  });
  await page.route("**/v1/models", (route) =>
    route.fulfill({
      json: {
        season: "2026/27",
        models: [
          model("baseline-v1", range(4, 12), "baseline"),
          model("form-fixture-v1", range(6, 11), "retired", 11),
          model("gbm-v1", range(9, 12), "main"),
        ],
      },
    }),
  );
  const predicted = (gameweek: number, model_version: string, predicted_points: number) => ({
    gameweek,
    model_version,
    predicted_points,
    expected_minutes: 80,
    actual_points: null,
    actual_minutes: null,
  });
  await page.route(`**/v1/players/${PLAYER.player_id}`, (route) =>
    route.fulfill({
      json: {
        ...PLAYER,
        history: [],
        predictions: [
          predicted(5, "baseline-v1", 1.5),
          predicted(10, "baseline-v1", 2.5),
          predicted(10, "form-fixture-v1", 3.5),
          predicted(10, "gbm-v1", 4.5),
          predicted(12, "baseline-v1", 5.5),
          predicted(12, "gbm-v1", 6.5),
        ],
      },
    }),
  );
  await page.goto(`/players/${PLAYER.player_id}`);
  await expect(page.getByRole("row", { name: /GW5\b/ })).toContainText("1.50");
  await expect(page.getByRole("row", { name: /GW10\b/ })).toContainText("3.50");
  await expect(page.getByRole("row", { name: /GW12\b/ })).toContainText("6.50");
});

test("an unknown player is the API's problem, worded as the API words it", async ({ page }) => {
  await page.goto("/players/9999");
  await expect(page.getByRole("alert")).toContainText("No player 9999");
});

test("a gameweek or player that is not a number is not found, without asking the API", async ({
  page,
}) => {
  for (const path of ["/gameweeks/abc?model=baseline-v1", "/players/abc"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  }
});

test("a model with no entry that gameweek falls back to one that has", async ({ page }) => {
  await page.goto("/gameweeks/4?model=form-fixture-v1");
  await expect(page.getByText(/main model wasn.t running yet in gameweek 4/)).toBeVisible();
  await expect(page.getByRole("table", { name: "Predictions" })).toBeVisible();
});

// #194: users get one set of predictions and plain words; the models are
// compared on Track record. A link may still ask for one by ?model=.
const INSIDER = /baseline-v1|form-fixture-v1|gbm-v1|Snapshot|RMSE/;

test("Predictions offers no model choice, and names no model code or snapshot", async ({
  page,
}) => {
  await page.goto("/gameweeks/8");
  await expect(page.getByRole("table", { name: "Predictions" })).toBeVisible();
  await expect(page.getByLabel("Model")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(INSIDER);
  await page.getByText("Published before the deadline").click();
  await expect(page.getByRole("link", { name: /committed to the public log/ })).toBeVisible();
});

test("a link to another model still opens it, and says so", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  await expect(page.getByText(/not our main ones/)).toBeVisible();
  await page.getByRole("link", { name: "See our main predictions" }).click();
  await expect(page).not.toHaveURL(/model=/);
  await expect(page.getByText(/not our main ones/)).toHaveCount(0);
});

test("My team and a player's page offer no model choice either", async ({ page }) => {
  await page.goto("/optimise");
  await expect(page.getByRole("button", { name: "Get my advice" })).toBeVisible();
  await expect(page.getByLabel("Model")).toHaveCount(0);
  expect(await page.locator("main").innerText()).not.toMatch(INSIDER);

  await stubPlayer(page);
  await page.goto("/players/42");
  await expect(page.getByRole("heading", { name: "Ada Tester" })).toBeVisible();
  expect(await page.locator("main").innerText()).not.toMatch(INSIDER);
});
