// Who a club plays, from a gameweek's fixtures (#196). The predictions page,
// the front page's top picks and the player page all show it this way.
import type { Schemas } from "./api/client";

export type Fixture = Schemas["Fixture"];

// A club's opponents in a gameweek, in kick-off order: "LIV (A)", two in a
// double gameweek, none in a blank.
export function opponents(fixtures: Fixture[] | undefined, team: string): string[] {
  return (fixtures ?? [])
    .filter((f) => f.home === team || f.away === team)
    .sort((a, b) => (a.kickoff_utc ?? "").localeCompare(b.kickoff_utc ?? ""))
    .map((f) => (f.home === team ? `${f.away} (H)` : `${f.home} (A)`));
}

// The same as a short phrase: "LIV (A), EVE (H)", or "No match".
export function opponentText(fixtures: Fixture[] | undefined, team: string): string {
  const found = opponents(fixtures, team);
  return found.length ? found.join(", ") : "No match";
}
