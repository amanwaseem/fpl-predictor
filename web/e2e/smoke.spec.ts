import { expect, test } from "@playwright/test";

test("the front page reads the API's status", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByLabel("Status")).toContainText("Season");
});

test("the open data is for developers: in the footer, not the main navigation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText([
    "Predictions",
    "Live",
    "My team",
    "Track record",
  ]);
  await expect(
    page.getByRole("contentinfo").getByRole("link", { name: "How it works" }),
  ).toBeVisible();
  await expect(
    page.getByRole("contentinfo").getByRole("link", { name: "Open data (for developers)" }),
  ).toHaveAttribute("href", "/docs");
});

test("the front page shows this gameweek's top picks by position", async ({ page }) => {
  await page.goto("/");
  for (const position of ["goalkeepers", "defenders", "midfielders", "forwards"]) {
    await expect(
      page.getByRole("list", { name: `Top ${position}` }).getByRole("listitem"),
    ).toHaveCount(3);
  }
});

test("a deep link is served the app, not a 404", async ({ page }) => {
  await page.goto("/no/such/page");
  await expect(page.getByRole("heading", { name: "Not found" })).toBeVisible();
});

test("the API stays the API", async ({ request }) => {
  const response = await request.get("/v1/no-such-route");
  expect(response.status()).toBe(404);
  expect(response.headers()["content-type"]).toContain("application/problem+json");
});

test("How it works answers managers' questions, and leaves the models to Track record", async ({
  page,
}) => {
  await page.goto("/about");
  for (const question of [
    "What does “5.2 points” mean?",
    "Where do I find my team ID?",
    "Do you need my password?",
    "When do new predictions come out?",
    "Why might the advice be wrong?",
    "Is this official FPL?",
  ])
    await expect(page.getByRole("heading", { name: question })).toBeVisible();
  await expect(
    page.getByRole("main").getByRole("link", { name: "The track record" }),
  ).toHaveAttribute("href", "/track-record");
});

test("the favicon and robots.txt are files, not the app", async ({ request }) => {
  const icon = await request.get("/favicon.svg");
  expect(icon.headers()["content-type"]).toContain("image/svg+xml");
  const robots = await request.get("/robots.txt");
  expect(robots.headers()["content-type"]).toContain("text/plain");
  expect(await robots.text()).toContain("User-agent");
});
