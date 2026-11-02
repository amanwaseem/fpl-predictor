// Where an entry lives in the committed log, for links that prove when it was
// made: its file's history on GitHub. 2026/27 is predictions/, later seasons
// predictions-<season>/ (ADR 0023), as fpl.log.contract.season_directory names
// them. A season in any other shape gets no link rather than a wrong one.
export const REPOSITORY = "https://github.com/amanwaseem/fpl-predictor";

export function logDirectory(season: string): string | null {
  const match = /^(\d{4})\/(\d{2})$/.exec(season);
  if (!match) return null;
  return season === "2026/27" ? "predictions" : `predictions-${match[1]}-${match[2]}`;
}

export function entryHistoryUrl(season: string, gameweek: number, model: string): string | null {
  const directory = logDirectory(season);
  if (!directory) return null;
  const name = `gw${String(gameweek).padStart(2, "0")}_${model}.csv`;
  return `${REPOSITORY}/commits/main/${directory}/${name}`;
}
