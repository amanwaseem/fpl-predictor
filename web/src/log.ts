// Where an entry lives in the committed log, for links that prove when it was
// made: its file's history on GitHub. 2026/27 is predictions/, later seasons
// predictions-<season>/ (ADR 0023).
export const REPOSITORY = "https://github.com/amanwaseem/fpl-predictor";

export function logDirectory(season: string): string {
  if (season === "2026/27") return "predictions";
  const [start, end] = season.split("/");
  return `predictions-${start}-${end}`;
}

export function entryHistoryUrl(season: string, gameweek: number, model: string): string {
  const name = `gw${String(gameweek).padStart(2, "0")}_${model}.csv`;
  return `${REPOSITORY}/commits/main/${logDirectory(season)}/${name}`;
}
