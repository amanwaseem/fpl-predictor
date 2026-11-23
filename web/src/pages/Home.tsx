import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import {
  POSITION_NAMES,
  POSITIONS,
  useEntry,
  useGameweek,
  useLive,
  useModels,
  useNews,
  useWeek,
} from "../api/queries";
import { ClubBadge, Countdown, Loading, PlayerPhoto, StatusPill } from "../components/bits";
import { Problem } from "../components/Problem";
import { opponentText } from "../fixtures";
import { formatLocal, signed } from "../format";
import { byPreference, liveModels } from "../models";
import { chanceText, isOut } from "../news";
import { savedTeam } from "../remember";
import { useTitle } from "../useTitle";
import { type WeekState } from "../week";

type Row = Schemas["Prediction"];

// Players a manager could pick this week: available, or doubtful at worst.
const pickable = (r: Row) => r.status === "a" || r.status === "d";

// How many doubts the team news shows: the most owned, which touch most teams.
const NEWS_SHOWN = 8;

// The front page knows what week it is (#196): one sentence and one filled
// button for what matters now, the reader's own team if one is remembered,
// then the top picks, the team news and a line of proof.
export function Home() {
  useTitle(null);
  // Read once, so the page does not change under the reader.
  const [team] = useState(() => savedTeam()?.team ?? null);
  const { status, week } = useWeek();
  const models = useModels();

  // The newest gameweek with an entry, and the main model's entry for it.
  const all = models.data?.models ?? [];
  const latest = Math.max(0, ...all.flatMap((m) => m.gameweeks_logged)) || null;
  const featured = latest ? byPreference(all, latest)[0]?.model_version : undefined;
  const live = liveModels(all);
  const main = (live.find((m) => m.role === "main") ?? live[0])?.model_version;

  return (
    <>
      <section className="hero">
        <div className="wrap hero-grid">
          <div>
            <Kicker week={week} />
            <h1>
              Know who will score <em>before</em> the deadline.
            </h1>
            {week ? (
              <WeekLine week={week} />
            ) : status.isError ? (
              <p className="lede">
                Points predictions for every Premier League player, published before each deadline.
              </p>
            ) : (
              <Loading />
            )}
          </div>
          <DeadlineCard status={status.data} error={status.isError ? status.error : null} />
        </div>
      </section>

      <div className="wrap">
        {models.isError && <Problem error={models.error} />}

        <section className="section" aria-label={team ? "Your team" : "Where to start"}>
          {team ? <TeamCard team={team} week={week} model={main} /> : <Doors />}
        </section>

        {latest && featured && (
          <section className="section" aria-labelledby="top-picks">
            <div className="section-head">
              <h2 id="top-picks">Top picks for gameweek {latest}</h2>
              <Link to={`/gameweeks/${latest}`}>All predictions →</Link>
            </div>
            <TopPicks gameweek={latest} model={featured} />
          </section>
        )}

        <TeamNews />

        <p className="proof-line">
          How have our predictions done, week by week?{" "}
          <Link to="/track-record">See the track record →</Link>
        </p>
      </div>
    </>
  );
}

function Kicker({ week }: { week: WeekState | null }) {
  if (!week) return <p className="kicker">&nbsp;</p>;
  if (week.kind === "live")
    return (
      <p className="kicker kicker-live">
        <span className="pulse" aria-hidden="true" />
        Gameweek {week.gameweek} is live
      </p>
    );
  if (week.kind === "predictions")
    return <p className="kicker">Gameweek {week.gameweek} predictions are out</p>;
  return <p className="kicker">{week.next ? `Before gameweek ${week.next}` : "Between seasons"}</p>;
}

// The week's one sentence and its one filled button, with at most one quieter
// link beside it.
function WeekLine({ week }: { week: WeekState }) {
  let sentence: string;
  let action: { to: string; label: string };
  let other: { to: string; label: string } | null = null;
  if (week.kind === "live") {
    sentence = `Gameweek ${week.gameweek} is live. See the points as they come, against what we predicted.`;
    action = { to: "/live", label: `Follow GW${week.gameweek} live` };
    other = { to: `/gameweeks/${week.gameweek}`, label: `GW${week.gameweek} predictions` };
  } else if (week.kind === "predictions") {
    sentence = `GW${week.gameweek} predictions are in. Deadline ${formatLocal(week.deadline)}.`;
    action = { to: "/optimise", label: "Get advice for my team" };
    other = { to: `/gameweeks/${week.gameweek}`, label: `See GW${week.gameweek} predictions` };
  } else {
    const day = week.arrives
      ? new Date(week.arrives).toLocaleDateString("en-GB", { weekday: "long" })
      : null;
    sentence = !week.next
      ? "The season is over. Next season's predictions come before its first deadline."
      : day
        ? `GW${week.next} predictions arrive ${day}, before the ${formatLocal(week.deadline)} deadline.`
        : `GW${week.next} predictions arrive before the ${formatLocal(week.deadline)} deadline.`;
    action = week.last
      ? { to: `/gameweeks/${week.last}`, label: `See how GW${week.last} went` }
      : { to: "/track-record", label: "See the track record" };
  }
  return (
    <>
      <p className="lede">{sentence}</p>
      <div className="hero-actions">
        <Link className="button" to={action.to}>
          {action.label}
        </Link>
        {other && (
          <Link className="button button-on-ink" to={other.to}>
            {other.label}
          </Link>
        )}
      </div>
    </>
  );
}

// The reader's remembered team. Before a deadline, the advice waiting for it,
// asked under the same key as My team, so going there shows it at once.
function TeamCard({
  team,
  week,
  model,
}: {
  team: number;
  week: WeekState | null;
  model: string | undefined;
}) {
  const ahead = week?.kind === "predictions";
  // During matches, the team's live total (#199).
  const live = useLive(week?.kind === "live" ? week.gameweek : null, team);
  const advice = useQuery({
    // My team's key for the same request: no free transfers given, hits allowed.
    queryKey: ["advice", team, model, undefined, undefined],
    enabled: ahead && !!model,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET("/v1/entries/{fpl_team_id}/advice", {
          params: { path: { fpl_team_id: team }, query: { model } },
        }),
      ),
  });
  const plan = advice.data?.plan;
  const n = plan?.transfers.length ?? 0;
  const line = live.data?.team
    ? `${live.data.team.points} points so far, live`
    : !ahead
      ? "See your advice and best eleven"
      : advice.isPending
        ? "Working out your advice…"
        : advice.isError || !plan
          ? "Your advice is waiting on My team"
          : n === 0
            ? "Hold: no transfer is worth making this week"
            : `${n} transfer${n === 1 ? "" : "s"} suggested, ${signed(plan.xi_gain, 1)} points`;
  return (
    <Link className="card team-card" to={live.data?.team ? "/live" : "/optimise"}>
      <span>
        <span className="eyebrow">Your team · {team}</span>
        <span className="team-card-line">{line}</span>
      </span>
      <span className="team-card-go" aria-hidden="true">
        →
      </span>
    </Link>
  );
}

// A first visit: the three places to go, one line each.
function Doors() {
  const doors = [
    {
      to: "/optimise",
      title: "My team",
      line: "Your transfers, captain and best eleven, from your FPL team ID. No sign-in.",
    },
    {
      to: "/gameweeks",
      title: "Predictions",
      line: "Every player's expected points, with who they play and what they cost.",
    },
    {
      to: "/live",
      title: "Live",
      line: "Points as the matches happen, against what we predicted.",
    },
  ];
  return (
    <div className="grid grid-3 doors">
      {doors.map((d) => (
        <Link className="card door" to={d.to} key={d.to}>
          <h3>{d.title}</h3>
          <p className="muted small">{d.line}</p>
        </Link>
      ))}
    </div>
  );
}

function DeadlineCard({
  status,
  error,
}: {
  status: Schemas["Status"] | undefined;
  error: Error | null;
}) {
  if (error) return <Problem error={error} />;
  if (!status)
    return (
      <div className="deadline-card">
        <Loading />
      </div>
    );
  const next = status.next_gameweek;
  return (
    <div className="deadline-card">
      <p className="eyebrow">{next ? `Gameweek ${next} deadline` : "Next deadline"}</p>
      <div className="big">
        {next && status.next_deadline_utc ? (
          <DeadlineCountdown to={status.next_deadline_utc} />
        ) : (
          "–"
        )}
      </div>
      <dl aria-label="Status">
        {next && (
          <>
            <dt>Deadline</dt>
            <dd>{formatLocal(status.next_deadline_utc)}</dd>
            <dt>Predictions</dt>
            <dd>
              {status.next_gameweek_entries.length ? "Out now" : "Coming before the deadline"}
            </dd>
          </>
        )}
        <dt>Season</dt>
        <dd>{status.season}</dd>
        <dt>Last scored</dt>
        <dd>
          {status.latest_settled_gameweek
            ? `Gameweek ${status.latest_settled_gameweek}`
            : "None yet"}
        </dd>
      </dl>
    </div>
  );
}

function TopPicks({ gameweek, model }: { gameweek: number; model: string }) {
  const entry = useEntry(gameweek, model);
  const fixtures = useGameweek(gameweek).data?.fixtures;
  if (entry.isPending) return <Loading />;
  if (entry.isError) return <Problem error={entry.error} />;
  const rows = entry.data.items.filter(pickable);
  return (
    <div className="grid grid-4">
      {POSITIONS.map((position) => (
        <div className="card pick-group" key={position}>
          <h3>
            {POSITION_NAMES[position]}
            <Link className="small" to={`/gameweeks/${gameweek}?position=${position}`}>
              More
            </Link>
          </h3>
          <ol className="pick-list" aria-label={`Top ${POSITION_NAMES[position]?.toLowerCase()}`}>
            {rows
              .filter((r) => r.position === position)
              .slice(0, 3)
              .map((r) => (
                <li key={r.player_id}>
                  <span className="player">
                    <PlayerPhoto code={r.player_code} name={r.web_name} team={r.team} size={38} />
                    <span className="player-text">
                      <Link className="player-name" to={`/players/${r.player_id}`}>
                        {r.web_name}
                      </Link>
                      <span className="player-meta">
                        <ClubBadge team={r.team} />
                        {/* None at all: the database has the log but no football data. */}
                        {!!fixtures?.length && (
                          <span className="opponent">{opponentText(fixtures, r.team)}</span>
                        )}
                        <StatusPill status={r.status} />
                      </span>
                    </span>
                  </span>
                  <span className="pick-points">
                    {r.predicted_points.toFixed(1)}
                    <small>pts</small>
                  </span>
                </li>
              ))}
          </ol>
        </div>
      ))}
    </div>
  );
}

// Who may miss the next gameweek, from FPL's latest news: the most owned few.
function TeamNews() {
  const news = useNews();
  if (news.isPending) return null;
  const items = news.data?.items.slice(0, NEWS_SHOWN) ?? [];
  return (
    <section className="section" aria-labelledby="news">
      <div className="section-head">
        <h2 id="news">Team news</h2>
      </div>
      {news.isError ? (
        <p className="muted small">FPL&rsquo;s team news couldn&rsquo;t be read just now.</p>
      ) : items.length === 0 ? (
        <p className="muted small">No one is doubtful, injured or suspended.</p>
      ) : (
        <>
          <ul className="news-list" aria-label="Players in doubt">
            {items.map((p) => (
              <li key={p.player_id}>
                <span className={`chance ${isOut(p) ? "chance-out" : "chance-doubt"}`}>
                  {chanceText(p)}
                </span>
                <Link className="player-name" to={`/players/${p.player_id}`}>
                  {p.web_name}
                </Link>
                <ClubBadge team={p.team} />
                <span className="muted small news-text">{p.news || "No details given"}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">
            Our predictions use FPL&rsquo;s chance of playing as it stood when they were made.
          </p>
        </>
      )}
    </section>
  );
}

// Counts down to the deadline, then asks for the status again: the API names
// the next gameweek by the clock, so the card moves on to it rather than read
// "passed" until a reload.
function DeadlineCountdown({ to }: { to: string }) {
  const queryClient = useQueryClient();
  return (
    <Countdown
      to={to}
      onPassed={() => void queryClient.invalidateQueries({ queryKey: ["status"] })}
    />
  );
}
