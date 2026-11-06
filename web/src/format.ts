// Times are UTC throughout the project, and shown as UTC so they match the log.
export function formatUtc(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "unknown";
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

// A time as a manager reads it: "Fri 6 Nov, 18:30 GMT", in the reader's own
// time zone, named. The log and the API stay in UTC (.claude/CLAUDE.md).
export function formatLocal(iso: string | null | undefined): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return "unknown";
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `${day}, ${time}`;
}

// A number to `digits` places, or a dash for none.
export function fixed(x: number | null | undefined, digits = 3): string {
  return x === null || x === undefined ? "–" : x.toFixed(digits);
}

// A signed number to `digits` places, rounded before the sign is chosen, so
// -0.0004 is ±0.000 and never -0.000.
export function signed(x: number, digits = 3): string {
  const rounded = Number(x.toFixed(digits));
  const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "±";
  return `${sign}${Math.abs(rounded).toFixed(digits)}`;
}

// A route parameter that must be a positive whole number, or null.
export function positiveInt(value: string | undefined): number | null {
  return value && /^\d+$/.test(value) && Number(value) > 0 ? Number(value) : null;
}
