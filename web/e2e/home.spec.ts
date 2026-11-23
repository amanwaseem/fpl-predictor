// The front page knows what week it is (#196). The status and the current
// gameweek's fixtures are stubbed, so each state can be shown on demand.
import { expect, test, type Page } from "@playwright/test";

const HOUR = 3_600_000;
const iso = (offsetHours: number) => new Date(Date.now() + offsetHours * HOUR).toISOString();

async function stubWeek(
  page: Page,
  {
    entries,
    deadlineIn,
    kickoffAgo,
  }: { entries: string[]; deadlineIn: number; kickoffAgo: number },
) {
  const status = await (await page.request.get("/v1/status")).json();
  await page.route("**/v1/status", (route) =>
    route.fulfill({
      json: {
        ...status,
        next_gameweek: 12,
        next_deadline_utc: iso(deadlineIn),
        next_gameweek_entries: entries,
      },
    }),
  );
  // GW11's two matches: kicked off `kickoffAgo` hours ago and three hours later.
  await page.route("**/v1/gameweeks/11", (route) =>
    route.fulfill({
      json: {
        gameweek: 11,
        deadline_utc: iso(-kickoffAgo - 2),
        finished: false,
        data_checked: false,
        models_logged: [],
        fixtures: [
          {
            fixture_id: 1,
            kickoff_utc: iso(-kickoffAgo),
            home: "ARS",
            away: "CHE",
            home_score: null,
            away_score: null,
            finished: false,
          },
          {
            fixture_id: 2,
            kickoff_utc: iso(-kickoffAgo + 3),
            home: "LIV",
            away: "MCI",
            home_score: null,
            away_score: null,
            finished: false,
          },
        ],
      },
    }),
  );
}

// Filled buttons in the hero: one per state.
const filled = (page: Page) => page.locator(".hero-actions .button:not(.button-on-ink)");

test("before a deadline with predictions out, the page offers advice", async ({ page }) => {
  await stubWeek(page, { entries: ["gw12_baseline-v1.csv"], deadlineIn: 20, kickoffAgo: 100 });
  await page.goto("/");
  await expect(page.locator(".hero .lede")).toContainText("GW12 predictions are in. Deadline");
  await expect(filled(page)).toHaveCount(1);
  await expect(filled(page)).toHaveText("Get advice for my team");
});

test("while a gameweek is in play, the page says it is live", async ({ page }) => {
  await stubWeek(page, { entries: [], deadlineIn: 100, kickoffAgo: 1 });
  await page.goto("/");
  await expect(page.locator(".kicker")).toHaveText("Gameweek 11 is live");
  await expect(filled(page)).toHaveCount(1);
  await expect(filled(page)).toHaveText("Follow GW11 live");
});

test("between gameweeks, the page says when predictions arrive", async ({ page }) => {
  await stubWeek(page, { entries: [], deadlineIn: 72, kickoffAgo: 100 });
  await page.goto("/");
  const day = new Date(Date.now() + 42 * HOUR).toLocaleDateString("en-GB", { weekday: "long" });
  await expect(page.locator(".hero .lede")).toContainText(`GW12 predictions arrive ${day}`);
  await expect(filled(page)).toHaveCount(1);
  await expect(filled(page)).toHaveText("See how GW11 went");
});

test("a first visit gets three places to start", async ({ page }) => {
  await page.goto("/");
  const start = page.getByRole("region", { name: "Where to start" });
  for (const name of ["My team", "Predictions", "Live"])
    await expect(start.getByRole("link", { name: new RegExp(`^${name}`) })).toBeVisible();
});

test("a remembered team gets its advice on the front page", async ({ page }) => {
  await page.addInitScript(() =>
    window.localStorage.setItem("fpl-predictor:my-team", JSON.stringify({ team: 321 })),
  );
  await stubWeek(page, { entries: ["gw12_baseline-v1.csv"], deadlineIn: 20, kickoffAgo: 100 });
  await page.route("**/v1/entries/321/advice*", (route) =>
    route.fulfill({
      json: { plan: { transfers: [{}, {}], xi_gain: 5.3 } },
    }),
  );
  await page.goto("/");
  const card = page.getByRole("region", { name: "Your team" });
  await expect(card.getByRole("link")).toContainText("Your team · 321");
  await expect(card.getByRole("link")).toContainText("2 transfers suggested, +5.3 points");
  await expect(page.getByRole("region", { name: "Where to start" })).toHaveCount(0);
});

test("team news lists the doubts with their chance of playing", async ({ page }) => {
  const doubt = (id: number, name: string, chance: number | null, status: string) => ({
    player_id: id,
    player_code: null,
    web_name: name,
    team: "ARS",
    position: "GKP",
    status,
    chance_of_playing: chance,
    news: `${name} news`,
    news_added_utc: null,
    selected_by_percent: 10,
  });
  await page.route("**/v1/news", (route) =>
    route.fulfill({
      json: { items: [doubt(1, "Raya", 75, "d"), doubt(2, "Saka", null, "i")] },
    }),
  );
  await page.goto("/");
  const list = page.getByRole("list", { name: "Players in doubt" });
  await expect(list.getByRole("listitem").first()).toContainText("75%");
  await expect(list.getByRole("listitem").first()).toContainText("Raya news");
  await expect(list.getByRole("listitem").nth(1)).toContainText("Out");
});

test("top picks show who each player plays", async ({ page, request }) => {
  const { models } = await (await request.get("/v1/models")).json();
  const latest = Math.max(
    ...models.flatMap((m: { gameweeks_logged: number[] }) => m.gameweeks_logged),
  );
  const detail = await (await request.get(`/v1/gameweeks/${latest}`)).json();
  // CI's database has the log but no football data: give every club a match.
  const clubs = [
    "ARS",
    "AVL",
    "BOU",
    "BRE",
    "BHA",
    "CHE",
    "COV",
    "CRY",
    "EVE",
    "FUL",
    "HUL",
    "IPS",
    "LEE",
    "LIV",
    "MCI",
    "MUN",
    "NEW",
    "NFO",
    "SUN",
    "TOT",
    "WHU",
    "WOL",
    "BUR",
    "LEI",
    "SOU",
  ];
  const fixtures = clubs.map((home) => ({
    fixture_id: home.charCodeAt(0) * 100 + home.charCodeAt(1),
    kickoff_utc: detail.deadline_utc,
    home,
    away: "XYZ",
    home_score: null,
    away_score: null,
    finished: false,
  }));
  await page.route(`**/v1/gameweeks/${latest}`, (route) =>
    route.fulfill({ json: { ...detail, fixtures } }),
  );
  await page.goto("/");
  await expect(page.locator(".pick-list .opponent").first()).toHaveText("XYZ (H)");
});
