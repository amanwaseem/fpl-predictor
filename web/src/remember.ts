// The reader's own FPL team, remembered in this browser only (#176): no
// account, nothing sent anywhere. Storage can be missing or refuse (a private
// window, blocked site data), so every access is guarded and the site works
// exactly as before without it.
//
// The team ID alone: free transfers change every gameweek, so a count given
// once would be wrong the next week, and the API estimates them anyway.

const KEY = "fpl-predictor:my-team";
// The advice route's own limit on a team ID (fpl/api/routes.py, get_advice).
const MAX_TEAM_ID = 100_000_000;

export type MyTeam = { team: number };

export function savedTeam(): MyTeam | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const { team } = value as Record<string, unknown>;
    if (typeof team !== "number" || !Number.isInteger(team) || team < 1 || team > MAX_TEAM_ID) {
      return null;
    }
    return { team };
  } catch {
    return null;
  }
}

// True if it was saved.
export function saveTeam(myTeam: MyTeam): boolean {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(myTeam));
    return true;
  } catch {
    // Not remembered: the page still works from its link.
    return false;
  }
}

export function forgetTeam() {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing could have been saved either.
  }
}
