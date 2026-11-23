// Live points (#199), with the API's live route stubbed: FPL's data changes
// by the minute, and CI's database has no football data.
import { expect, test, type Page } from "@playwright/test";
import { fakeStatus } from "./fixtures";

type Item = { player_id: number; web_name: string; predicted_points: number };

// GW11 is the gameweek just gone: the status names GW12 as next.
async function stubLive(page: Page, { inPlay }: { inPlay: boolean }) {
  await page.route("**/v1/status", (route) => route.fulfill({ json: fakeStatus(72, []) }));
  const { models } = await (await page.request.get("/v1/models")).json();
  // The page shows the main model's prediction, as every page does (#194).
  type Model = { model_version: string; role: string; gameweeks_logged: number[] };
  const logged = (models as Model[]).filter((m) => m.gameweeks_logged.includes(11));
  const model = logged.find((m) => m.role === "main") ?? logged[0]!;
  const entry = await (
    await page.request.get(`/v1/gameweeks/11/predictions?model=${model.model_version}&limit=1000`)
  ).json();
  const [a, b, c] = entry.items as Item[];
  const requests: URL[] = [];
  await page.route("**/v1/live/11*", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const team = url.searchParams.get("team");
    return route.fulfill({
      json: {
        season: "2026/27",
        gameweek: 11,
        in_play: inPlay,
        fixtures: [
          {
            fixture_id: 1,
            kickoff_utc: "2026-11-21T12:30:00Z",
            home: "MCI",
            away: "FUL",
            home_score: 1,
            away_score: 1,
            state: "finished",
            minutes: 90,
          },
          {
            fixture_id: 2,
            kickoff_utc: "2026-11-22T16:30:00Z",
            home: "LIV",
            away: "MUN",
            home_score: 2,
            away_score: 0,
            state: inPlay ? "live" : "finished",
            minutes: inPlay ? 63 : 90,
          },
        ],
        players: [
          // a: far above our prediction; b: far below; c: about right.
          { player_id: a!.player_id, minutes: 90, total_points: 15, bonus: 3 },
          { player_id: b!.player_id, minutes: 90, total_points: 0, bonus: 0 },
          {
            player_id: c!.player_id,
            minutes: 45,
            total_points: Math.round(c!.predicted_points),
            bonus: 0,
          },
        ],
        team: team
          ? {
              fpl_team_id: Number(team),
              active_chip: null,
              transfers_cost: 0,
              points: 30,
              picks: [
                {
                  player_id: a!.player_id,
                  position: 1,
                  multiplier: 2,
                  is_captain: true,
                  is_vice_captain: false,
                },
                {
                  player_id: b!.player_id,
                  position: 2,
                  multiplier: 1,
                  is_captain: false,
                  is_vice_captain: true,
                },
                {
                  player_id: c!.player_id,
                  position: 12,
                  multiplier: 0,
                  is_captain: false,
                  is_vice_captain: false,
                },
              ],
            }
          : null,
      },
    });
  });
  return { a: a!, b: b!, c: c!, requests };
}

test("the scores say FT, the minute, or the kick-off", async ({ page }) => {
  await stubLive(page, { inPlay: true });
  await page.goto("/live");
  const matches = page.getByRole("list", { name: "Matches" }).getByRole("listitem");
  await expect(matches.first()).toContainText("1–1");
  await expect(matches.first()).toContainText("FT");
  await expect(matches.nth(1)).toContainText("63′");
  await expect(page.getByText("Live now")).toBeVisible();
  await expect(page.getByText(/provisional until FPL confirms them/)).toBeVisible();
});

test("everyone is sorted by points, or by the biggest surprises", async ({ page }) => {
  const { a, b } = await stubLive(page, { inPlay: false });
  await page.goto("/live");
  const rows = page.getByRole("table", { name: "Live points" }).locator("tbody tr");
  await expect(rows.first()).toContainText(a.web_name);
  await page.getByRole("button", { name: "Biggest surprises" }).click();
  // a is 15 against a prediction under 15, b is 0 against his: the larger gap first.
  const first = Math.abs(15 - a.predicted_points) >= Math.abs(0 - b.predicted_points) ? a : b;
  await expect(rows.first()).toContainText(first.web_name);
});

test("a remembered team shows its live total against our prediction", async ({ page }) => {
  const { a, b } = await stubLive(page, { inPlay: true });
  await page.addInitScript(() =>
    window.localStorage.setItem("fpl-predictor:my-team", JSON.stringify({ team: 321 })),
  );
  await page.goto("/live");
  const total = page.getByLabel("Your total");
  await expect(total).toContainText("30");
  // Captain a doubled, b once, c on the bench.
  const ours = a.predicted_points * 2 + b.predicted_points;
  await expect(total).toContainText(ours.toFixed(1));
  await expect(page.getByRole("list", { name: "Your players" }).getByRole("listitem")).toHaveCount(
    3,
  );
});

test("the page asks again each minute while a match is on, and not otherwise", async ({ page }) => {
  await page.clock.install();
  const playing = await stubLive(page, { inPlay: true });
  await page.goto("/live");
  await expect(page.getByText("Live now")).toBeVisible();
  const before = playing.requests.length;
  await page.clock.fastForward(61_000);
  await expect.poll(() => playing.requests.length).toBeGreaterThan(before);
});

test("with every match over, nothing more is asked", async ({ page }) => {
  await page.clock.install();
  const over = await stubLive(page, { inPlay: false });
  await page.goto("/live");
  await expect(page.getByRole("list", { name: "Matches" })).toBeVisible();
  const before = over.requests.length;
  await page.clock.fastForward(130_000);
  await page.waitForTimeout(300);
  expect(over.requests.length).toBe(before);
});
