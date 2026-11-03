import { expect, test } from "@playwright/test";

test("the front page reads the API's status", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByLabel("Status")).toContainText("Season");
});

test("the API is for developers: in the footer, not the main navigation", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link")).toHaveText([
    "Home",
    "Predictions",
    "My team",
  ]);
  await expect(page.getByRole("contentinfo").getByRole("link", { name: /API/ })).toHaveAttribute("href", "/docs");
});

test("the front page shows this gameweek's top picks by position", async ({ page }) => {
  await page.goto("/");
  for (const position of ["goalkeepers", "defenders", "midfielders", "forwards"]) {
    await expect(page.getByRole("list", { name: `Top ${position}` }).getByRole("listitem")).toHaveCount(3);
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
