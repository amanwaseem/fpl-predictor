// The README's screenshots of the site, light and dark: the home page, and
// the best squad from scratch on the pitch. They are not committed: the
// README shows them from the site-screenshots release. After a visible
// change to the site, retake them and replace the release's copies, from the
// repository root:
//
//   npm --prefix web run screenshots                                  # the live site
//   BASE_URL=http://127.0.0.1:8000 npm --prefix web run screenshots   # a local build
//   gh release upload site-screenshots scratch/screenshots/*.png --clobber
//
// The first time, make the release instead, kept out of the Latest slot so
// the model's release keeps it:
//
//   gh release create site-screenshots --latest=false \
//     --title "Site screenshots (README)" --notes "Shown by the README." \
//     scratch/screenshots/*.png
//
// Written to scratch/screenshots/<name>-<scheme>.png, which git ignores.
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "https://fpl-predictor-aman.fly.dev";
const OUT = fileURLToPath(new URL("../../scratch/screenshots/", import.meta.url));

const SHOTS = [
  { name: "home", path: "/" },
  {
    name: "squad",
    path: "/optimise",
    async ready(page) {
      await page.getByRole("button", { name: /Pick the best squad/ }).click();
      const squad = page.getByRole("group", { name: "Best squad" });
      await squad.locator(".shirt").nth(14).waitFor();
      await squad.scrollIntoViewIfNeeded();
      return squad;
    },
  },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
try {
  for (const colorScheme of ["light", "dark"]) {
    // In UK time, as FPL's deadlines are announced, whoever takes them.
    const page = await browser.newPage({
      viewport: { width: 1280, height: 800 },
      deviceScaleFactor: 1,
      colorScheme,
      locale: "en-GB",
      timezoneId: "Europe/London",
    });
    for (const shot of SHOTS) {
      await page.goto(new URL(shot.path, BASE_URL).href, { waitUntil: "networkidle" });
      const target = shot.ready ? await shot.ready(page) : page;
      // Player photos come from FPL's CDN, after the data: wait until every
      // image on the page has finished loading, or failed to.
      await page.waitForFunction(() => Array.from(document.images).every((img) => img.complete));
      const path = join(OUT, `${shot.name}-${colorScheme}.png`);
      await target.screenshot({ path });
      console.log(path);
    }
    await page.close();
  }
} finally {
  await browser.close();
}
