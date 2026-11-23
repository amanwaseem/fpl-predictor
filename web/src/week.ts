// What the week is doing, for the front page (#196). Before a deadline people
// want advice, during matches they want points, and between gameweeks they
// want to know how the last one went and when the next predictions come.
import type { Schemas } from "./api/client";
import type { Fixture } from "./fixtures";

// The entry workflow makes the entry from 30 hours before a deadline, once the
// previous gameweek has settled (ADR 0028).
export const ENTRY_WINDOW_HOURS = 30;
// A gameweek is taken to be in play until this long after its last kick-off:
// long enough for stoppage time and a late start. The finished flags come
// from the daily ingest, too late to end it sooner.
const PLAY_HOURS = 2.5;
const HOUR = 3_600_000;

export type WeekState =
  | { kind: "live"; gameweek: number }
  | { kind: "predictions"; gameweek: number; deadline: string }
  | {
      kind: "between";
      // The gameweek whose deadline has passed, if any, and the next one.
      last: number | null;
      next: number | null;
      deadline: string | null;
      // When the next predictions should arrive, if that is still ahead.
      arrives: string | null;
    };

// `fixtures` are the fixtures of the gameweek before the next one: the one in
// play, if any is.
export function weekState(
  status: Schemas["Status"],
  fixtures: Fixture[] | undefined,
  now: Date,
): WeekState {
  const next = status.next_gameweek;
  const last = next ? (next > 1 ? next - 1 : null) : status.latest_settled_gameweek;
  const kickoffs = (fixtures ?? [])
    .map((f) => (f.kickoff_utc ? Date.parse(f.kickoff_utc) : NaN))
    .filter((t) => !Number.isNaN(t));
  const allFinished = !!fixtures?.length && fixtures.every((f) => f.finished);
  if (
    last &&
    kickoffs.length &&
    !allFinished &&
    Math.min(...kickoffs) <= now.getTime() &&
    now.getTime() < Math.max(...kickoffs) + PLAY_HOURS * HOUR
  )
    return { kind: "live", gameweek: last };

  const deadline = status.next_deadline_utc;
  const ahead = !!deadline && Date.parse(deadline) > now.getTime();
  if (next && ahead && status.next_gameweek_entries.length)
    return { kind: "predictions", gameweek: next, deadline: deadline! };

  const arrives = ahead ? Date.parse(deadline!) - ENTRY_WINDOW_HOURS * HOUR : null;
  return {
    kind: "between",
    last,
    next: ahead ? next : null,
    deadline: ahead ? deadline : null,
    arrives: arrives && arrives > now.getTime() ? new Date(arrives).toISOString() : null,
  };
}
