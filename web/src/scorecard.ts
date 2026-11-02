// The scorecard's arithmetic, kept out of the page so it can be read on its own.
import type { Schemas } from "./api/client";

export type Model = Schemas["ScorecardModel"];
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
  const shared = model.per_gameweek.filter((w) =>
    baseline.gameweeks_scored.includes(w.gameweek),
  );
  const theirs = baseline.per_gameweek.filter((w) =>
    shared.some((s) => s.gameweek === w.gameweek),
  );
  const mine = pooledMae(shared);
  const base = pooledMae(theirs);
  if (mine === null || base === null) return null;
  return { gameweeks: shared.length, mae: mine, baselineMae: base, difference: mine - base };
}
