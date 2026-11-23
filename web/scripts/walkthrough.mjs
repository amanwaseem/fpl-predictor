// Every page and error state, at phone and desktop width, light and dark, for a
// review by eye before a release (#176). Full-page shots, into
// scratch/walkthrough/<name>-<width>-<scheme>.png, which git ignores.
//
//   BASE_URL=http://127.0.0.1:8000 npm --prefix web run walkthrough
//   SCHEMES=light,dark WIDTHS=390 npm --prefix web run walkthrough
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const BASE_URL = process.env.BASE_URL ?? "http://127.0.0.1:8000";
const OUT = fileURLToPath(new URL("../../scratch/walkthrough/", import.meta.url));
const WIDTHS = (process.env.WIDTHS ?? "390,1280").split(",").map(Number);
const SCHEMES = (process.env.SCHEMES ?? "light").split(",");

// The newest gameweek with an entry, and its top player, so every page has data.
const list = await (await fetch(new URL("/v1/gameweeks", BASE_URL))).json();
const logged = list.gameweeks.filter((g) => g.models_logged.length > 0).at(-1);
const newest = logged.gameweek;
const rows = await (
  await fetch(
    new URL(
      `/v1/gameweeks/${newest}/predictions?model=${logged.models_logged[0]}&limit=1`,
      BASE_URL,
    ),
  )
).json();
const player = rows.items[0].player_id;

const PAGES = [
  { name: "home", path: "/" },
  { name: "gameweek", path: `/gameweeks/${newest}` },
  { name: "player", path: `/players/${player}` },
  { name: "optimise", path: "/optimise" },
  { name: "optimise-team", path: "/optimise?team=1" },
  { name: "track-record", path: "/track-record" },
  { name: "about", path: "/about" },
  { name: "missing-gameweek", path: "/gameweeks/99" },
  { name: "missing-player", path: "/players/999999" },
  { name: "missing-team", path: "/optimise?team=999999999" },
  { name: "not-found", path: "/no-such-page" },
];

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
try {
  for (const colorScheme of SCHEMES) {
    for (const width of WIDTHS) {
      const page = await browser.newPage({
        viewport: { width, height: 900 },
        deviceScaleFactor: 1,
        colorScheme,
        locale: "en-GB",
        timezoneId: "Europe/London",
      });
      for (const shot of PAGES) {
        await page.goto(new URL(shot.path, BASE_URL).href, { waitUntil: "networkidle" });
        await page.waitForFunction(() => Array.from(document.images).every((i) => i.complete));
        const path = join(OUT, `${shot.name}-${width}-${colorScheme}.png`);
        await page.screenshot({ path, fullPage: true });
        console.log(path);
      }
      await page.close();
    }
  }
} finally {
  await browser.close();
}
