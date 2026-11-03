// The full scorecard, for anyone checking the numbers: every model's pooled
// metrics, then each gameweek, each figure linked to its file in the log.
import { Link } from "react-router-dom";
import { fixed } from "../format";
import { entryHistoryUrl } from "../log";
import { modelName } from "../models";
import { againstBaseline, cell, gameweeks, judged, type Model } from "../scorecard";

const list = (gws: number[]) => (gws.length ? gws.map((g) => `GW${g}`).join(", ") : "none");

export function ScorecardTables({ season, models }: { season: string; models: Model[] }) {
  if (!models.length) return <p>No model has an entry yet.</p>;
  const baseline = models.find((m) => m.role === "baseline");
  const weeks = gameweeks(models);

  return (
    <>
      <p className="note">
        Error is predicted minus actual points, for every player in every scored gameweek. The
        average error (MAE) is the headline: lower is better. A model is compared with the baseline
        only on the gameweeks both were scored, so the comparison is between models, not weeks.
      </p>
      <div className="card card-flush table-scroll">
        <table aria-label="Pooled">
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Scored</th>
              <th scope="col">Missed</th>
              <th scope="col" className="num" title="Mean absolute error, points">Avg error</th>
              <th scope="col" className="num" title="Root mean squared error: big misses count more">RMSE</th>
              <th scope="col" className="num" title="Spearman rank correlation: how well the order of players matched">Rank match</th>
              <th scope="col" className="num" title="Average error over players the model expected to play">Avg error, starters</th>
              <th scope="col" className="num" title="Average error in predicted minutes">Minutes error</th>
              <th scope="col" className="num">Avg error, shared weeks</th>
              <th scope="col" className="num">Baseline, same weeks</th>
              <th scope="col" className="num">Difference</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const vs = againstBaseline(m, baseline);
              return (
                <tr key={m.model_version}>
                  <th scope="row">
                    {modelName(m.model_version)}{" "}
                    <code className="muted">{m.model_version}</code>
                  </th>
                  <td>{list(m.gameweeks_scored)}</td>
                  <td className={m.gameweeks_missed.length ? "missed" : undefined}>
                    {list(m.gameweeks_missed)}
                  </td>
                  <td className="num">{fixed(m.pooled.all?.mae)}</td>
                  <td className="num">{fixed(m.pooled.all?.rmse)}</td>
                  <td className="num">{fixed(m.pooled.all?.spearman)}</td>
                  <td className="num">{fixed(m.pooled.predicted_to_play?.mae)}</td>
                  <td className="num">{fixed(m.pooled.minutes_mae, 1)}</td>
                  <td className="num">{vs ? fixed(vs.mae) : "–"}</td>
                  <td className="num">{vs ? fixed(vs.baselineMae) : "–"}</td>
                  <td className="num">
                    <Difference value={vs?.difference} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h3 className="section">By gameweek</h3>
      <p className="note">
        Average error for each set of predictions, and its difference from the baseline that week.
        Each figure links to the file's history in the log, which shows when it was committed. A
        missed gameweek had nothing published before its deadline, and stays missed.
      </p>
      <div className="card card-flush table-scroll">
        <table aria-label="By gameweek">
          <thead>
            <tr>
              <th scope="col">Gameweek</th>
              {models.map((m) => (
                <th scope="col" className="num" key={m.model_version}>
                  {modelName(m.model_version)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks.map((gw) => (
              <tr key={gw}>
                <th scope="row">
                  {/* A week no model logged has no entry to open. */}
                  {models.some((m) => m.gameweeks_logged.includes(gw)) ? (
                    <Link to={`/gameweeks/${gw}`}>GW{gw}</Link>
                  ) : (
                    `GW${gw}`
                  )}
                </th>
                {models.map((m) => {
                  const c = cell(m, gw, baseline);
                  const key = m.model_version;
                  if (c.kind === "missed") return <td key={key} className="num missed">missed</td>;
                  if (c.kind === "none") return <td key={key} className="num muted">–</td>;
                  const href = entryHistoryUrl(season, gw, m.model_version);
                  const text = c.kind === "pending" ? "pending" : fixed(c.week.mae);
                  const diff =
                    c.kind === "scored" && c.baseline && m !== baseline
                      ? c.week.mae - c.baseline.mae
                      : undefined;
                  return (
                    <td key={key} className="num">
                      {href ? <a href={href}>{text}</a> : text}
                      {diff !== undefined && (
                        <span className="diff">
                          <Difference value={diff} />
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Difference({ value }: { value: number | undefined }) {
  if (value === undefined) return <>–</>;
  const { text, verdict } = judged(value);
  return <span className={verdict}>{text}</span>;
}
