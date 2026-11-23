// Live points while a gameweek is played (#199): every match's score, the
// reader's own team against our prediction, and everyone's points so far
// beside what we predicted. FPL's data, a minute old at most, asked for again
// each minute while a match is on.
import { useState } from "react";
import { Link } from "react-router-dom";
import { type Schemas } from "../api/client";
import { useEntry, useGameweek, useLive, useModels, useWeek } from "../api/queries";
import { ClubBadge, Loading, PlayerPhoto } from "../components/bits";
import { Problem } from "../components/Problem";
import { formatLocal } from "../format";
import { byPreference } from "../models";
import { savedTeam } from "../remember";
import { useTitle } from "../useTitle";

type LiveData = Schemas["Live"];
type Prediction = Schemas["Prediction"];

// Rows of everyone shown at first, and added by each "show more".
const PAGE = 30;

export function Live() {
  useTitle("Live");
  const { status } = useWeek();
  const models = useModels();
  // Read once, so the page does not change under the reader.
  const [team] = useState(() => savedTeam()?.team ?? null);

  // The gameweek whose deadline has passed: in play, or the one just played.
  const s = status.data;
  const gameweek = s
    ? s.next_gameweek
      ? s.next_gameweek > 1
        ? s.next_gameweek - 1
        : null
      : s.latest_settled_gameweek
    : null;
  const live = useLive(gameweek);
  const mine = useLive(team ? gameweek : null, team);
  const model = gameweek
    ? byPreference(models.data?.models, gameweek)[0]?.model_version
    : undefined;
  const entry = useEntry(gameweek, model);
  const predicted = new Map((entry.data?.items ?? []).map((p) => [p.player_id, p]));

  if (status.isError)
    return (
      <div className="wrap page">
        <Problem error={status.error} />
      </div>
    );
  if (!s || (gameweek && live.isPending))
    return (
      <div className="wrap page">
        <Loading what="Reading the live scores" />
      </div>
    );
  if (!gameweek)
    return (
      <div className="wrap page">
        <p className="eyebrow">Live</p>
        <h1>Nothing is live yet</h1>
        <p className="lede">
          The season&rsquo;s first matches come after its first deadline,{" "}
          {formatLocal(s.next_deadline_utc)}.
        </p>
      </div>
    );

  const data = live.data;
  return (
    <div className="wrap page">
      <div className="page-head">
        <div>
          {data?.in_play ? (
            <p className="kicker kicker-live">
              <span className="pulse" aria-hidden="true" />
              Live now
            </p>
          ) : (
            <p className="eyebrow">Live</p>
          )}
          <h1>Gameweek {gameweek}</h1>
          <p className="muted small">
            Live points are provisional until FPL confirms them
            {data?.in_play ? ", and update every minute while a match is on" : ""}.
          </p>
        </div>
      </div>

      {live.isError && <Problem error={live.error} />}
      {data && (
        <>
          <Scores fixtures={data.fixtures} />
          {data.fixtures.every((f) => f.state === "finished") && s.next_gameweek && (
            <NextKickoff gameweek={s.next_gameweek} />
          )}
          {team !== null && (
            <YourTeam
              team={team}
              data={mine.data}
              error={mine.isError ? mine.error : null}
              predicted={predicted}
            />
          )}
          <Everyone data={data} predicted={predicted} gameweek={gameweek} />
        </>
      )}
    </div>
  );
}

function kickoff(iso: string | null) {
  if (!iso) return { time: "TBC", day: "" };
  const d = new Date(iso);
  return {
    time: d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }),
    day: d.toLocaleDateString("en-GB", { weekday: "short" }),
  };
}

function Scores({ fixtures }: { fixtures: LiveData["fixtures"] }) {
  return (
    <section className="section" aria-labelledby="scores">
      <h2 id="scores">Scores</h2>
      <ul className="scores card" aria-label="Matches">
        {fixtures.map((f) => {
          const k = kickoff(f.kickoff_utc);
          return (
            <li key={f.fixture_id} className={`match match-${f.state}`}>
              <span className="match-home">{f.home}</span>
              <span className="match-score">
                {f.state === "not_started" ? k.time : `${f.home_score ?? 0}–${f.away_score ?? 0}`}
              </span>
              <span className="match-away">{f.away}</span>
              <span className="match-state">
                {f.state === "finished" ? "FT" : f.state === "live" ? `${f.minutes}′` : k.day}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function NextKickoff({ gameweek }: { gameweek: number }) {
  const fixtures = useGameweek(gameweek).data?.fixtures ?? [];
  const first = fixtures
    .map((f) => f.kickoff_utc)
    .filter((k): k is string => !!k)
    .sort()[0];
  if (!first) return null;
  return (
    <p className="note">
      Every match is over. Gameweek {gameweek} kicks off {formatLocal(first)}.{" "}
      <Link to={`/gameweeks/${gameweek}`}>See its predictions</Link>
    </p>
  );
}

// The reader's own team: its points so far against what we predicted for the
// same eleven, captain doubled.
function YourTeam({
  team,
  data,
  error,
  predicted,
}: {
  team: number;
  data: LiveData | undefined;
  error: Error | null;
  predicted: Map<number, Prediction>;
}) {
  if (error)
    return (
      <section className="section" aria-labelledby="yours">
        <h2 id="yours">Your team</h2>
        <Problem error={error} />
      </section>
    );
  if (!data?.team) return <Loading what="Reading your team" />;
  const points = new Map(data.players.map((p) => [p.player_id, p]));
  const picks = data.team.picks;
  const ours = picks.reduce(
    (sum, p) => sum + (predicted.get(p.player_id)?.predicted_points ?? 0) * p.multiplier,
    0,
  );
  return (
    <section className="section" aria-labelledby="yours">
      <div className="section-head">
        <h2 id="yours">Your team</h2>
        <Link to="/optimise">Team {team}</Link>
      </div>
      <div className="live-total card" aria-label="Your total">
        <div>
          <span className="eyebrow">Live</span>
          <strong>{data.team.points}</strong>
        </div>
        <div>
          <span className="eyebrow">We predicted</span>
          <strong>{ours.toFixed(1)}</strong>
        </div>
        {data.team.transfers_cost > 0 && (
          <p className="muted small">Live includes −{data.team.transfers_cost} for hits.</p>
        )}
      </div>
      <ol className="live-picks card" aria-label="Your players">
        {picks.map((p) => {
          const who = predicted.get(p.player_id);
          const live = points.get(p.player_id);
          return (
            <li key={p.player_id} className={p.multiplier === 0 ? "benched" : undefined}>
              <span className="player">
                <PlayerPhoto
                  code={who?.player_code}
                  name={who?.web_name ?? String(p.player_id)}
                  team={who?.team ?? ""}
                  size={32}
                />
                <span className="player-text">
                  <Link className="player-name" to={`/players/${p.player_id}`}>
                    {who?.web_name ?? `Player ${p.player_id}`}
                  </Link>
                  <span className="player-meta">
                    {who && <ClubBadge team={who.team} />}
                    {p.is_captain && <span className="pill pill-neutral">C</span>}
                    {p.multiplier === 0 && <span className="pos">Bench</span>}
                  </span>
                </span>
              </span>
              <span className="live-figures">
                <span className="muted small">{who ? who.predicted_points.toFixed(1) : "–"}</span>
                <strong>{(live?.total_points ?? 0) * Math.max(1, p.multiplier)}</strong>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

type Sort = "points" | "surprises";

// Everyone who has played so far: our prediction beside their points, by
// points or by how far they are from what we predicted.
function Everyone({
  data,
  predicted,
  gameweek,
}: {
  data: LiveData;
  predicted: Map<number, Prediction>;
  gameweek: number;
}) {
  const [sort, setSort] = useState<Sort>("points");
  const [shown, setShown] = useState(PAGE);
  // A player's matches are over once every fixture his club plays is.
  const over = (club: string) =>
    data.fixtures
      .filter((f) => f.home === club || f.away === club)
      .every((f) => f.state === "finished");
  const rows = data.players
    .filter((p) => p.minutes > 0 && predicted.has(p.player_id))
    .map((p) => {
      const who = predicted.get(p.player_id)!;
      return { ...p, who, gap: p.total_points - who.predicted_points };
    })
    .sort((a, b) =>
      sort === "points"
        ? b.total_points - a.total_points || a.player_id - b.player_id
        : Math.abs(b.gap) - Math.abs(a.gap) || a.player_id - b.player_id,
    );
  return (
    <section className="section" aria-labelledby="everyone">
      <div className="section-head">
        <h2 id="everyone">Everyone</h2>
        <div className="segmented" role="group" aria-label="Sort by">
          <button type="button" aria-pressed={sort === "points"} onClick={() => setSort("points")}>
            Points
          </button>
          <button
            type="button"
            aria-pressed={sort === "surprises"}
            onClick={() => setSort("surprises")}
          >
            Biggest surprises
          </button>
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="muted">No one has played yet.</p>
      ) : (
        <div className="card card-flush table-scroll">
          <table aria-label="Live points">
            <thead>
              <tr>
                <th scope="col">Player</th>
                <th scope="col" className="num">
                  Predicted
                </th>
                <th scope="col" className="num">
                  Live
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, shown).map((r) => (
                <tr key={r.player_id}>
                  <th scope="row">
                    <span className="player">
                      <PlayerPhoto
                        code={r.who.player_code}
                        name={r.who.web_name}
                        team={r.who.team}
                        size={32}
                      />
                      <span className="player-text">
                        <Link className="player-name" to={`/players/${r.player_id}`}>
                          {r.who.web_name}
                        </Link>
                        <span className="player-meta">
                          <ClubBadge team={r.who.team} />
                          <span className="pos">{r.minutes}′</span>
                        </span>
                      </span>
                    </span>
                  </th>
                  <td className="num">{r.who.predicted_points.toFixed(1)}</td>
                  {/* Coloured once his matches are over: mid-match, behind is expected. */}
                  <td
                    className={
                      !over(r.who.team)
                        ? "num live-points"
                        : r.gap >= 0
                          ? "num better"
                          : "num worse"
                    }
                  >
                    <strong>{r.total_points}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > shown && (
            <div className="show-more">
              <button
                type="button"
                className="button button-ghost"
                onClick={() => setShown(shown + PAGE)}
              >
                Show more ({rows.length - shown} left)
              </button>
            </div>
          )}
        </div>
      )}
      <p className="note small after">
        Predicted is our gameweek {gameweek} prediction, made before the deadline, for the whole
        gameweek. Live points include bonus once FPL adds it.
      </p>
    </section>
  );
}
