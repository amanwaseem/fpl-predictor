import { expect, test } from "@playwright/test";

// Against the committed log, projected: baseline-v1 has been scored since GW4.
test("the scorecard shows every model beside the baseline", async ({ page }) => {
  await page.goto("/");
  const pooled = page.getByRole("table", { name: "Pooled" });
  await expect(pooled.getByRole("rowheader", { name: "baseline-v1" })).toBeVisible();
  await expect(pooled.getByRole("rowheader", { name: "form-fixture-v1" })).toBeVisible();
});

test("each scored entry links to its history in the log", async ({ page }) => {
  await page.goto("/");
  const byWeek = page.getByRole("table", { name: "By gameweek" });
  // The row's own link goes to the gameweek; the figure's, to the log.
  const gw4 = byWeek.getByRole("row", { name: /^GW4\b/ }).getByRole("link", { name: /^\d/ });
  await expect(gw4).toHaveAttribute(
    "href",
    "https://github.com/amanwaseem/fpl-predictor/commits/main/predictions/gw04_baseline-v1.csv",
  );
});
