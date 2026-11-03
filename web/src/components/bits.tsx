// Small pieces every page uses: player photos, club badges, availability,
// the deadline countdown and the page title.
import { useEffect, useState } from "react";
import { SITE_NAME } from "../brand";
import { clubColours, clubName } from "../clubs";

// FPL's own photo CDN, keyed by the player's code (#136). These are Premier
// League images, hotlinked: fine for a fan site, to be revisited before
// anything commercial. Turning photos off is this constant.
const PHOTOS = true;
const photoUrl = (code: number) =>
  `https://resources.premierleague.com/premierleague25/photos/players/110x140/${code}.png`;

const initials = (name: string) =>
  name
    .split(/[\s.'-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join("");

// A player's photo, or their initials in club colours when there is no code
// or the image does not load.
export function PlayerPhoto({
  code,
  name,
  team,
  size = 40,
}: {
  code: number | null | undefined;
  name: string;
  team: string;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const { bg, fg } = clubColours(team);
  const showPhoto = PHOTOS && code && !failed;
  return (
    <span
      className="photo"
      style={{ width: size, height: size, background: showPhoto ? undefined : bg, color: fg }}
      aria-hidden="true"
    >
      {showPhoto ? (
        <img src={photoUrl(code)} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <span style={{ fontSize: size * 0.36 }}>{initials(name)}</span>
      )}
    </span>
  );
}

export function ClubBadge({ team }: { team: string }) {
  const { bg, fg } = clubColours(team);
  return (
    <span className="club" style={{ background: bg, color: fg }} title={clubName(team)}>
      {team}
    </span>
  );
}

// FPL's availability codes; "a", available, shows nothing.
const STATUS: Record<string, { label: string; tone: "warn" | "bad" }> = {
  d: { label: "Doubtful", tone: "warn" },
  i: { label: "Injured", tone: "bad" },
  s: { label: "Suspended", tone: "bad" },
  u: { label: "Unavailable", tone: "bad" },
  n: { label: "Not in squad", tone: "bad" },
};

export function StatusPill({ status }: { status: string | null | undefined }) {
  const s = status ? STATUS[status] : undefined;
  return s ? <span className={`pill pill-${s.tone}`}>{s.label}</span> : null;
}

// "3d 14h", "5h 20m", "12m": the time left, ticking each half minute.
export function Countdown({ to }: { to: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  const left = new Date(to).getTime() - now;
  if (Number.isNaN(left)) return null;
  if (left <= 0) return <>passed</>;
  const minutes = Math.floor(left / 60_000);
  const d = Math.floor(minutes / 1440);
  const h = Math.floor((minutes % 1440) / 60);
  const m = minutes % 60;
  return <>{d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`}</>;
}

// The tab's title: "<page> · <site>", or the site alone.
export function useTitle(page: string | null) {
  useEffect(() => {
    document.title = page ? `${page} · ${SITE_NAME}` : SITE_NAME;
  }, [page]);
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="loading" aria-busy="true">
      <span className="spinner" aria-hidden="true" />
      {what}…
    </p>
  );
}
