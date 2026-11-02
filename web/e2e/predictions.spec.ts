import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

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
  if (newestSeason === undefined) throw new Error(`No committed entries under predictions*/ in ${root}`);
  return Math.max(...bySeason.get(newestSeason)!);
}

test("/gameweeks opens the newest gameweek's entry", async ({ page }) => {
  await page.goto("/gameweeks");
  await expect(page).toHaveURL(new RegExp(`/gameweeks/${newestLoggedGameweek()}$`));
  await expect(page.getByRole("table", { name: "Predictions" }).getByRole("row")).not.toHaveCount(1);
});

test("filters narrow the entry and sorting reorders it", async ({ page }) => {
  await page.goto("/gameweeks/8?model=baseline-v1");
  const table = page.getByRole("table", { name: "Predictions" });
  await page.getByLabel("Position").selectOption("GKP");
  await expect(table.locator("tbody td:nth-child(3)").first()).toHaveText("GKP");
  await expect(table.locator("tbody td:nth-child(3)").filter({ hasNotText: "GKP" })).toHaveCount(0);

  await table.getByRole("button", { name: "Price" }).click();
  const prices = await table.locator("tbody td:nth-child(4)").allTextContents();
  const values = prices.map((p) => Number(p.replace(/[£m]/g, "")));
  expect(values).toEqual([...values].sort((a, b) => b - a));
});

test("a player's page shows each model's prediction beside what happened", async ({ page }) => {
  // The database in CI holds the log only, not football data: the player is stubbed.
  await page.route("**/v1/players/42", (route) =>
    route.fulfill({
      json: {
        season: "2026/27",
        player_id: 42,
        web_name: "Tester",
        first_name: "Ada",
        second_name: "Tester",
        position: "MID",
        clubs: [{ team: "ARS", first_gameweek: 1, last_gameweek: null }],
        history: [
          {
            fixture_id: 1, gameweek: 8, opponent: "CHE", was_home: true, minutes: 90,
            total_points: 9, goals_scored: 1, assists: 1, clean_sheets: 0, bonus: 2,
            expected_goals: 0.6, expected_assists: 0.3,
          },
        ],
        predictions: [
          { gameweek: 8, model_version: "baseline-v1", predicted_points: 5.1,
            expected_minutes: 85, actual_points: 9, actual_minutes: 90 },
        ],
      },
    }),
  );
  await page.goto("/players/42");
  await expect(page.getByRole("heading", { name: "Ada Tester" })).toBeVisible();
  const gw8 = page.getByRole("row", { name: /GW8/ });
  await expect(gw8).toContainText("CHE (H)");
  await expect(gw8).toContainText("5.10");
});

test("an unknown player is the API's problem, worded as the API words it", async ({ page }) => {
  await page.goto("/players/9999");
  await expect(page.getByRole("alert")).toContainText("No player 9999");
});

test("a gameweek or player that is not a number is not found, without asking the API", async ({ page }) => {
  for (const path of ["/gameweeks/abc?model=baseline-v1", "/players/abc"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
  }
});

test("a model with no entry that gameweek falls back to one that has", async ({ page }) => {
  await page.goto("/gameweeks/4?model=form-fixture-v1");
  await expect(page.getByLabel("Model")).toHaveValue("baseline-v1");
  await expect(page.getByRole("table", { name: "Predictions" })).toBeVisible();
});
