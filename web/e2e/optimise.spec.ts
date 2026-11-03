import { expect, test } from "@playwright/test";

test("the best squad from scratch is fifteen players, solved from the log", async ({ page }) => {
  await page.goto("/optimise");
  await page.getByRole("button", { name: /Pick the best squad/ }).click();
  const squad = page.getByRole("group", { name: "Best squad" });
  await expect(squad.locator(".shirt")).toHaveCount(15);
  await expect(squad.locator("[data-captain]")).toHaveCount(1);
  await expect(squad.locator("[data-vice]")).toHaveCount(1);
});

// The advice route reads the manager's team from the live FPL API, which CI
// does not call: it is stubbed, around a squad the real optimiser picked.
test("advice for a team shows its transfers and XI", async ({ page, request }) => {
  const real = await (await request.post("/v1/optimise", { data: { model: "baseline-v1" } })).json();
  const [out, into] = [real.squad.bench[0], real.squad.xi[0]];
  await page.route("**/v1/entries/123/advice*", (route) =>
    route.fulfill({
      json: {
        fpl_team_id: 123,
        season: real.season,
        gameweek: real.gameweek,
        model_version: real.model_version,
        deadline_utc: real.deadline_utc,
        deadline_passed: false,
        picks_gameweek: real.gameweek - 1,
        cached: false,
        estimates: {
          selling_prices: "Estimated from purchase prices.",
          free_transfers: "Estimated as 1.",
        },
        squad: real.squad,
        plan: {
          transfers: [{ out, into, selling_price: out.price }],
          free_transfers: 1,
          hits: 0,
          hit_points: 0,
          gain: 1.5,
          xi_gain: 1.5,
          bank: 0.5,
          bank_after: 0.1,
          bench_weight: 0.1,
          over_cap: {},
        },
      },
    }),
  );
  await page.goto("/optimise");
  await page.getByLabel("FPL team ID").fill("123");
  await page.getByRole("button", { name: "Get advice" }).click();
  await expect(page).toHaveURL(/team=123/);
  await expect(page.getByRole("list", { name: "Transfers" })).toContainText(into.web_name);
  await expect(page.getByRole("group", { name: "Advised squad" })).toBeVisible();
  await expect(page.getByText("Estimated from purchase prices.")).toBeVisible();
});

test("an upstream failure is shown as the API words it", async ({ page }) => {
  await page.route("**/v1/entries/77/advice*", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/problem+json",
      json: {
        type: "about:blank",
        title: "Bad Gateway",
        status: 502,
        detail: "The FPL API did not answer.",
        instance: "/v1/entries/77/advice",
      },
    }),
  );
  await page.goto("/optimise?team=77");
  await expect(page.getByRole("alert")).toContainText("The FPL API did not answer.");
});

test("the form follows the URL, back and forward included", async ({ page }) => {
  for (const id of ["11", "22"]) {
    await page.route(`**/v1/entries/${id}/advice*`, (route) =>
      route.fulfill({ status: 404, contentType: "application/problem+json",
        json: { type: "about:blank", title: "Not Found", status: 404, detail: `No team ${id}.` } }),
    );
  }
  await page.goto("/optimise?team=11");
  await page.getByLabel("FPL team ID").fill("22");
  await page.getByRole("button", { name: "Get advice" }).click();
  await expect(page.getByRole("alert")).toContainText("No team 22.");
  await page.goBack();
  await expect(page.getByLabel("FPL team ID")).toHaveValue("11");
  await expect(page.getByRole("alert")).toContainText("No team 11.");
});
