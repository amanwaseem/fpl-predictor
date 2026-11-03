// Club colours for badges and photo fallbacks, by FPL's short name. Plain
// colour pairs, not crests: a club not listed (promoted next season, say)
// gets the neutral pair rather than a wrong one.
const CLUBS: Record<string, { bg: string; fg: string; name: string }> = {
  ARS: { bg: "#db0007", fg: "#ffffff", name: "Arsenal" },
  AVL: { bg: "#670e36", fg: "#ffffff", name: "Aston Villa" },
  BHA: { bg: "#0057b8", fg: "#ffffff", name: "Brighton" },
  BOU: { bg: "#c8102e", fg: "#ffffff", name: "Bournemouth" },
  BRE: { bg: "#e30613", fg: "#ffffff", name: "Brentford" },
  CHE: { bg: "#034694", fg: "#ffffff", name: "Chelsea" },
  COV: { bg: "#7ccbea", fg: "#0b1f2a", name: "Coventry" },
  CRY: { bg: "#1b458f", fg: "#ffffff", name: "Crystal Palace" },
  EVE: { bg: "#003399", fg: "#ffffff", name: "Everton" },
  FUL: { bg: "#f5f5f5", fg: "#111111", name: "Fulham" },
  HUL: { bg: "#f5a12d", fg: "#111111", name: "Hull City" },
  IPS: { bg: "#3a64a3", fg: "#ffffff", name: "Ipswich" },
  LEE: { bg: "#f5f5f5", fg: "#1d428a", name: "Leeds" },
  LIV: { bg: "#c8102e", fg: "#ffffff", name: "Liverpool" },
  MCI: { bg: "#6cabdd", fg: "#0b1f3a", name: "Man City" },
  MUN: { bg: "#da291c", fg: "#ffffff", name: "Man Utd" },
  NEW: { bg: "#241f20", fg: "#ffffff", name: "Newcastle" },
  NFO: { bg: "#dd0000", fg: "#ffffff", name: "Nott'm Forest" },
  SUN: { bg: "#eb172b", fg: "#ffffff", name: "Sunderland" },
  TOT: { bg: "#132257", fg: "#ffffff", name: "Spurs" },
  WHU: { bg: "#7a263a", fg: "#ffffff", name: "West Ham" },
  WOL: { bg: "#fdb913", fg: "#111111", name: "Wolves" },
  BUR: { bg: "#6c1d45", fg: "#ffffff", name: "Burnley" },
  LEI: { bg: "#003090", fg: "#ffffff", name: "Leicester" },
  SOU: { bg: "#d71920", fg: "#ffffff", name: "Southampton" },
};

const NEUTRAL = { bg: "#5c6670", fg: "#ffffff" };

export function clubColours(team: string): { bg: string; fg: string } {
  return CLUBS[team] ?? NEUTRAL;
}

export function clubName(team: string): string {
  return CLUBS[team]?.name ?? team;
}
