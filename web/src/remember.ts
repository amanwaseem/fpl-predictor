// The reader's own FPL team, remembered in this browser only (#176): no
// account, nothing sent anywhere. Storage can be missing or refuse (a private
// window, blocked site data), so every access is guarded and the site works
// exactly as before without it.

const KEY = "fpl-predictor:my-team";
const MAX_TEAM_ID = 100_000_000;

export type MyTeam = { team: number; free?: number };

export function savedTeam(): MyTeam | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const { team, free } = value as Record<string, unknown>;
    if (!Number.isInteger(team) || (team as number) < 1 || (team as number) > MAX_TEAM_ID) {
      return null;
    }
    const ok = Number.isInteger(free) && (free as number) >= 0 && (free as number) <= 5;
    return ok ? { team: team as number, free: free as number } : { team: team as number };
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
