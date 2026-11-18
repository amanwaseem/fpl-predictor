// The Content-Security-Policy the API sends (#178) lets every page load all
// it needs. Chrome reports each thing a policy blocks on the console.
import { expect, test, type Page } from "@playwright/test";
import { PLAYER, stubPlayer } from "./fixtures";

const PAGES = ["/", "/gameweeks", "/optimise", "/about", `/players/${PLAYER.player_id}`];

function violations(page: Page) {
  const seen: string[] = [];
  page.on("console", (message) => {
    if (message.text().includes("Content Security Policy")) seen.push(message.text());
  });
  return seen;
}

for (const path of PAGES) {
  test(`${path} breaks none of the site's Content-Security-Policy`, async ({ page }) => {
    const seen = violations(page);
    await stubPlayer(page);
    const response = await page.goto(path);
    expect(response?.headers()["content-security-policy"]).toContain("default-src 'self'");
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.waitForLoadState("networkidle");
    expect(seen).toEqual([]);
  });
}

test("the API docs load under their own policy", async ({ page }) => {
  const seen = violations(page);
  await page.goto("/docs");
  await expect(page.locator(".swagger-ui .title")).toContainText("fpl-predictor");
  await page.waitForLoadState("networkidle");
  expect(seen).toEqual([]);
});
