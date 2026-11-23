// FPL's team news as the site words it (#196, #200): "75%", "Doubt" or "Out".
import type { Schemas } from "./api/client";

export type News = Schemas["PlayerNews"];

// Out: no chance of playing, or injured, suspended or unavailable with no
// figure given.
export const isOut = (p: News) =>
  p.chance_of_playing === 0 || (p.chance_of_playing === null && p.status !== "d");

// "75%", or a word when FPL gives no figure.
export function chanceText(p: News): string {
  if (isOut(p)) return "Out";
  return p.chance_of_playing === null ? "Doubt" : `${p.chance_of_playing}%`;
}
