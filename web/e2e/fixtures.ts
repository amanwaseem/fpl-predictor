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
  price: 6.5,
  clubs: [{ team: "ARS", first_gameweek: 1, last_gameweek: null }],
  upcoming: [9, 10, 11, 12, 13].map((gameweek, i) => ({
    gameweek,
    kickoff_utc: new Date(Date.UTC(2026, 10, 10 + i * 7, 15)).toISOString(),
    opponent: ["LIV", "MCI", "CHE", "TOT", "NEW"][i]!,
    was_home: i % 2 === 0,
  })),
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

// A status for the week's state, stubbed whole so it holds whatever the real
// calendar says and no request is left in flight when a test ends. Before a
// deadline with the next predictions out, My team gives advice; between
// gameweeks it doesn't (#198).
export function fakeStatus(deadlineInHours: number, entries: string[]) {
  return {
    season: "2026/27",
    last_checked_utc: null,
    checked_age_seconds: null,
    snapshot_id: null,
    snapshot_completed_utc: null,
    data_age_seconds: null,
    latest_settled_gameweek: 10,
    next_gameweek: 12,
    next_deadline_utc: new Date(Date.now() + deadlineInHours * 3_600_000).toISOString(),
    next_gameweek_entries: entries,
  };
}

export async function stubPredictionsOut(page: Page) {
  await page.route("**/v1/status", (route) =>
    route.fulfill({ json: fakeStatus(20, ["gw12_baseline-v1.csv"]) }),
  );
}
