import { Link } from "react-router-dom";
import { type Schemas } from "../api/client";
import {
  POSITION_NAMES,
  POSITIONS,
  useEntry,
  useModels,
  useScorecard,
  useStatus,
} from "../api/queries";
import { ClubBadge, Countdown, Loading, PlayerPhoto, StatusPill } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { ScorecardTables } from "../components/ScorecardTables";
import { REPOSITORY } from "../log";
import { formatLocal } from "../format";
import { modelAbout, modelName, roleName } from "../models";
import { againstBaseline, gameweekList, retiredNote, type Model } from "../scorecard";

type Row = Schemas["Prediction"];

// Players a manager could pick this week: available, or doubtful at worst.
const pickable = (r: Row) => r.status === "a" || r.status === "d";

export function Home() {
  useTitle(null);
  const status = useStatus();
  const models = useModels();
  const scorecard = useScorecard();

  // The newest gameweek with an entry, and the main model's entry for it.
  const all = models.data?.models ?? [];
  const latest = Math.max(0, ...all.flatMap((m) => m.gameweeks_logged)) || null;
  const logged = all.filter((m) => latest && m.gameweeks_logged.includes(latest));
  const featured = (logged.find((m) => m.role === "main") ?? logged[0])?.model_version;

  return (
    <>
      <section className="hero">
        <div className="wrap hero-grid">
          <div>
            <h1>
              Know who will score <em>before</em> the deadline.
            </h1>
            <p className="lede">
              Points predictions for every Premier League player, published before each deadline,
              never edited, and scored against what really happened.
            </p>
            <div className="hero-actions">
              <Link className="button button-lime" to="/gameweeks">
                {latest ? `See GW${latest} predictions` : "See predictions"}
              </Link>
              <Link className="button button-on-ink" to="/optimise">
                Optimise my team
              </Link>
            </div>
          </div>
          <DeadlineCard status={status.data} error={status.isError ? status.error : null} />
        </div>
      </section>

      <div className="wrap">
        {models.isError && <Problem error={models.error} />}

        {latest && featured && (
          <section className="section" aria-labelledby="top-picks">
            <div className="section-head">
              <h2 id="top-picks">Top picks for gameweek {latest}</h2>
              <Link to={`/gameweeks/${latest}?model=${featured}`}>
                All {modelName(featured)} predictions →
              </Link>
            </div>
            <TopPicks gameweek={latest} model={featured} />
          </section>
        )}

        <section className="section" aria-labelledby="record">
          <div className="section-head">
            <h2 id="record">Track record</h2>
            <a href="#scorecard">Full scorecard ↓</a>
          </div>
          {scorecard.isPending && <Loading />}
          {scorecard.isError && <Problem error={scorecard.error} />}
          {scorecard.data && <Record models={scorecard.data.models} />}
        </section>

        <section className="section" aria-labelledby="how">
          <h2 id="how">How it works</h2>
          <div className="grid grid-3 steps">
            <div className="card step">
              <h3>Predict</h3>
              <p className="muted small">
                Before each deadline, every model predicts points for all ~670 players from data
                available at that moment, and nothing after it.
              </p>
            </div>
            <div className="card step">
              <h3>Publish</h3>
              <p className="muted small">
                The predictions are committed to a <a href={REPOSITORY}>public log on GitHub</a>.
                Git's history shows they came before the deadline, and a check refuses any later
                edit.
              </p>
            </div>
            <div className="card step">
              <h3>Score</h3>
              <p className="muted small">
                Once the gameweek's results are final, every prediction is scored. Good weeks and
                bad ones, and missed ones too, all stay on the record below.
              </p>
            </div>
          </div>
        </section>

        <section className="section" aria-labelledby="scorecard">
          <h2 id="scorecard">The full scorecard</h2>
          {scorecard.data && (
            <ScorecardTables season={scorecard.data.season} models={scorecard.data.models} />
          )}
        </section>
      </div>
    </>
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
        {next && status.next_deadline_utc ? <Countdown to={status.next_deadline_utc} /> : "–"}
      </div>
      <dl aria-label="Status">
        {next && (
          <>
            <dt>Deadline</dt>
            <dd>{formatLocal(status.next_deadline_utc)}</dd>
            <dt>Predictions</dt>
            <dd>
              {status.next_gameweek_entries.length
                ? status.next_gameweek_entries.map(modelName).join(", ")
                : "Coming before the deadline"}
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
  if (entry.isPending) return <Loading />;
  if (entry.isError) return <Problem error={entry.error} />;
  const rows = entry.data.items.filter(pickable);
  return (
    <div className="grid grid-4">
      {POSITIONS.map((position) => (
        <div className="card pick-group" key={position}>
          <h3>
            {POSITION_NAMES[position]}
            <Link
              className="small"
              to={`/gameweeks/${gameweek}?model=${model}&position=${position}`}
            >
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
                        <span className="pos">£{r.price.toFixed(1)}m</span>
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

function Record({ models }: { models: Model[] }) {
  if (!models.length) return <p>No model has an entry yet.</p>;
  const baseline = models.find((m) => m.role === "baseline");
  return (
    <div className="grid grid-3">
      {models.map((m) => {
        const vs = againstBaseline(m, baseline);
        const mae = m.pooled.all?.mae;
        // Relative to the baseline on the weeks both were scored: positive is better.
        const better = vs ? ((vs.baselineMae - vs.mae) / vs.baselineMae) * 100 : null;
        return (
          <article className="card record-card" key={m.model_version}>
            <div className="row">
              <h3>{modelName(m.model_version)}</h3>
              {retiredNote(m) ? (
                <span className="pill pill-neutral">{retiredNote(m)}</span>
              ) : (
                roleName(m.role) &&
                roleName(m.role) !== modelName(m.model_version) && (
                  <span className="tag">{roleName(m.role)}</span>
                )
              )}
            </div>
            <p className="muted small">{modelAbout(m.model_version)}</p>
            {mae !== undefined && mae !== null ? (
              <>
                <div className="big">
                  {mae.toFixed(2)}
                  <span className="muted small"> pts</span>
                </div>
                <p className="muted small">
                  average miss per player, over {m.gameweeks_scored.length} scored gameweek
                  {m.gameweeks_scored.length === 1 ? "" : "s"}
                  {better !== null && (
                    <>
                      {" "}
                      ·{" "}
                      <span
                        className={better > 0.05 ? "better" : better < -0.05 ? "worse" : undefined}
                      >
                        {Math.abs(better) < 0.05
                          ? "level with the baseline"
                          : `${Math.abs(better).toFixed(1)}% ${better > 0 ? "more" : "less"} accurate than the baseline`}
                      </span>
                    </>
                  )}
                </p>
              </>
            ) : (
              <p className="muted small">
                {m.gameweeks_logged.length
                  ? `First predictions in GW${Math.min(...m.gameweeks_logged)}; scored once that gameweek settles.`
                  : "No predictions yet."}
              </p>
            )}
            {m.gameweeks_missed.length > 0 && (
              <p className="small missed">Missed {gameweekList(m.gameweeks_missed)}</p>
            )}
            <p className="small muted">
              <code>{m.model_version}</code>
            </p>
          </article>
        );
      })}
    </div>
  );
}
