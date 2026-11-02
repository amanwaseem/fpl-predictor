import { expect, test } from "@playwright/test";

// Against the committed log, projected: GW8 is the newest gameweek with entries.
test("/gameweeks opens the newest gameweek's entry", async ({ page }) => {
  await page.goto("/gameweeks");
  await expect(page).toHaveURL(/\/gameweeks\/8$/);
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
