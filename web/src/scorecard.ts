// The scorecard's arithmetic, kept out of the page so it can be read on its own.
import type { Schemas } from "./api/client";
import { signed } from "./format";

// A difference from the baseline, as shown: to three places, and a tie when it
// rounds to zero, so a tie is never shown as a win.
export function judged(difference: number) {
  const rounded = Number(difference.toFixed(3));
  const verdict = rounded < 0 ? "better" : rounded > 0 ? "worse" : "tie";
  return { text: signed(difference), verdict };
}

export type Model = Schemas["ScorecardModel"];

// "GW5, GW7", or "none".
export const gameweekList = (gws: number[]) => (gws.length ? gws.map((g) => `GW${g}`).join(", ") : "none");

// "Retired after GW6", the role otherwise, as the scorecard shows it.
export function retiredNote(model: Model): string | null {
  return model.retired_after ? `Retired after GW${model.retired_after}` : null;
}
export type Week = Schemas["GameweekMetrics"];

// What a model's column says for one gameweek.
export type Cell =
  | { kind: "scored"; week: Week; baseline: Week | undefined }
  | { kind: "missed" }
  | { kind: "pending" }
  | { kind: "none" }; // before its first entry, or after it retired

export function cell(model: Model, gameweek: number, baseline: Model | undefined): Cell {
  const week = model.per_gameweek.find((w) => w.gameweek === gameweek);
  if (week) {
    return { kind: "scored", week, baseline: baseline?.per_gameweek.find((w) => w.gameweek === gameweek) };
  }
  if (model.gameweeks_missed.includes(gameweek)) return { kind: "missed" };
  if (model.gameweeks_logged.includes(gameweek)) return { kind: "pending" };
  return { kind: "none" };
}

// Every gameweek any model has logged, scored or missed, newest first.
export function gameweeks(models: Model[]): number[] {
  const all = new Set<number>();
  for (const m of models) {
    for (const gw of [...m.gameweeks_logged, ...m.gameweeks_missed]) all.add(gw);
  }
  return [...all].sort((a, b) => b - a);
}

// MAE pooled over player rows, for the gameweeks given.
function pooledMae(weeks: Week[]): number | null {
  const n = weeks.reduce((sum, w) => sum + w.n, 0);
  return n ? weeks.reduce((sum, w) => sum + w.mae * w.n, 0) / n : null;
}

// The model against the baseline on the gameweeks both have scored: pooled
// figures over different gameweeks would compare different weeks, not models.
export function againstBaseline(model: Model, baseline: Model | undefined) {
  if (!baseline || baseline === model) return null;
  const baselineWeeks = new Map(baseline.per_gameweek.map((w) => [w.gameweek, w]));
  const shared = model.per_gameweek.filter((w) => baselineWeeks.has(w.gameweek));
  const mine = pooledMae(shared);
  const base = pooledMae(shared.map((w) => baselineWeeks.get(w.gameweek)!));
  if (mine === null || base === null) return null;
  return { gameweeks: shared.length, mae: mine, baselineMae: base, difference: mine - base };
}
