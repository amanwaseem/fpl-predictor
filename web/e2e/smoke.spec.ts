import { expect, test } from "@playwright/test";

test("the home page reads the API's status", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByLabel("Status")).toContainText("Season");
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
