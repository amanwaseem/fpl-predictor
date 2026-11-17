// A player for pages that need one. The database in CI holds the log only,
// not football data, so player pages are stubbed.
import type { Page } from "@playwright/test";

export const PLAYER = {
  season: "2026/27",
  player_id: 42,
  player_code: 1042,
  web_name: "Tester",
  first_name: "Ada",
  second_name: "Tester",
  position: "MID",
  clubs: [{ team: "ARS", first_gameweek: 1, last_gameweek: null }],
  history: [
    {
      fixture_id: 1,
      gameweek: 8,
      opponent: "CHE",
      was_home: true,
      minutes: 90,
      total_points: 9,
      goals_scored: 1,
      assists: 1,
      clean_sheets: 0,
      bonus: 2,
      expected_goals: 0.6,
      expected_assists: 0.3,
    },
  ],
  predictions: [
    {
      gameweek: 8,
      model_version: "baseline-v1",
      predicted_points: 5.1,
      expected_minutes: 85,
      actual_points: 9,
      actual_minutes: 90,
    },
  ],
};

export async function stubPlayer(page: Page) {
  await page.route(`**/v1/players/${PLAYER.player_id}`, (route) => route.fulfill({ json: PLAYER }));
}
