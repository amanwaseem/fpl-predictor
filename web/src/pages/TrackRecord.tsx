import { useScorecard } from "../api/queries";
import { Loading } from "../components/bits";
import { MissChart, type Series } from "../components/MissChart";
import { Problem } from "../components/Problem";
import { ScorecardTables } from "../components/ScorecardTables";
import { andList } from "../format";
import { logDirectory, REPOSITORY } from "../log";
import { modelAbout, modelName, roleName } from "../models";
import {
  againstBaseline,
  gameweekList,
  missedGameweeks,
  percentBetter,
  retiredNote,
  type Model,
} from "../scorecard";
import { useTitle } from "../useTitle";

const WHY_MISSED = `${REPOSITORY}/blob/main/docs/adr/0004-a-missed-deadline-stays-missed.md`;

// Can these predictions be trusted? The answer first, then the record week by
// week and how it is kept honest, with every model and every figure folded
// away at the end (#195). This is the one page that names the models.
export function TrackRecord() {
  useTitle("Track record");
  const scorecard = useScorecard();
  return (
    <div className="wrap page">
      <p className="eyebrow">Track record</p>
      <h1>Every prediction, scored</h1>
      {scorecard.isPending && <Loading />}
      {scorecard.isError && <Problem error={scorecard.error} />}
      {scorecard.data && <Record season={scorecard.data.season} models={scorecard.data.models} />}
    </div>
  );
}

function Record({ season, models }: { season: string; models: Model[] }) {
  // Our predictions are the main model's (#194). Going by form is the
  // baseline, the yardstick every model has to beat (ADR 0020).
  const form = models.find((m) => m.role === "baseline");
  const ours = models.find((m) => m.role === "main") ?? form;
  const missed = missedGameweeks(models);
  const series: Series[] = [];
  if (ours) series.push({ name: "Our predictions", className: "ours", weeks: ours.per_gameweek });
  if (form && form !== ours)
    series.push({ name: "Going by form", className: "form", weeks: form.per_gameweek });
  const directory = logDirectory(season);

  return (
    <>
      <Answer ours={ours} form={form} />

      {ours && ours.per_gameweek.length > 0 && (
        <section className="section" aria-labelledby="by-week">
          <h2 id="by-week">Week by week</h2>
          <p className="note">
            The average miss per player in each gameweek, so lower is better. Going by form is a
            simple prediction from each player&rsquo;s recent minutes and points, and nothing else.
            Choose a week to see its predictions.
          </p>
          <div className="card">
            <MissChart series={series} missed={missed} />
          </div>
        </section>
      )}

      <section className="section" aria-labelledby="honest">
        <h2 id="honest">How we keep it honest</h2>
        <div className="grid grid-3">
          <article className="card">
            <h3>Published before every deadline</h3>
            <p className="muted small">
              Each gameweek&rsquo;s predictions go into a{" "}
              <a href={directory ? `${REPOSITORY}/tree/main/${directory}` : REPOSITORY}>
                public log on GitHub
              </a>{" "}
              before its deadline, and the log&rsquo;s history shows when.
            </p>
          </article>
          <article className="card">
            <h3>Never edited</h3>
            <p className="muted small">
              Once a prediction is in the log, a check refuses any change to it. A bad week stays
              exactly as bad as it was.
            </p>
          </article>
          <article className="card">
            <h3>Misses included</h3>
            <p className="muted small">
              {missed.length ? (
                <>
                  Nothing was published before the deadline for{" "}
                  <span className="missed">
                    {missed.length > 1 ? "gameweeks" : "gameweek"} {andList(missed)}
                  </span>
                  , so {missed.length > 1 ? "those weeks stay" : "that week stays"} missed.
                </>
              ) : (
                "We haven't missed a deadline yet. If we ever do, that week stays missed."
              )}{" "}
              A prediction made after a deadline can&rsquo;t be told apart from one made knowing the
              results. <a href={WHY_MISSED}>Why we never fill a gap</a>
            </p>
          </article>
        </div>
      </section>

      <section className="section" aria-labelledby="detail">
        <h2 id="detail">For the curious</h2>
        <details className="fold">
          <summary>Every model and the full scorecard</summary>
          <p className="note">
            Our predictions come from the main model. Going by form is the baseline model, the
            yardstick the others have to beat. A challenger runs beside them, and takes over from
            the main model if it does better over at least three of the same gameweeks.
          </p>
          <ModelCards models={models} />
          <h3 className="section">The full scorecard</h3>
          <ScorecardTables season={season} models={models} />
        </details>
      </section>
    </>
  );
}

// The answer in one line: how far off our predictions are on average, and how
// that compares with going by form on the same gameweeks.
function Answer({ ours, form }: { ours: Model | undefined; form: Model | undefined }) {
  const mae = ours?.pooled.all?.mae;
  if (!ours || mae === undefined || mae === null)
    return (
      <p className="lede">
        {ours?.gameweeks_logged.length
          ? `Our first predictions are scored once gameweek ${Math.min(...ours.gameweeks_logged)} is over.`
          : "Nothing has been published yet."}
      </p>
    );
  const better = form && form !== ours ? percentBetter(againstBaseline(ours, form)) : null;
  const weeks = ours.gameweeks_scored;
  return (
    <>
      <p className="lede answer">
        On average our predictions are <strong>{mae.toFixed(2)} points</strong> off per player
        {better === null ? (
          "."
        ) : Math.abs(better) < 0.05 ? (
          ", level with going by form."
        ) : (
          <>
            ,{" "}
            <strong>
              {Math.abs(better).toFixed(1)}% {better > 0 ? "more" : "less"} accurate
            </strong>{" "}
            than going by form.
          </>
        )}
      </p>
      <p className="note small">
        Over every player in {weeks.length > 1 ? "gameweeks" : "gameweek"} {andList(weeks)}, the
        ones scored so far.
      </p>
    </>
  );
}

// Each model's card: what it does, its average error, and how it compares
// with the baseline on the gameweeks both were scored.
function ModelCards({ models }: { models: Model[] }) {
  if (!models.length) return <p>No model has an entry yet.</p>;
  const baseline = models.find((m) => m.role === "baseline");
  return (
    <div className="grid grid-3">
      {models.map((m) => {
        const mae = m.pooled.all?.mae;
        // Positive is better.
        const better = percentBetter(againstBaseline(m, baseline));
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
