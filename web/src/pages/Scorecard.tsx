import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "../api/client";
import { Problem } from "../components/Problem";
import { formatUtc } from "../format";
import { entryHistoryUrl } from "../log";
import { againstBaseline, cell, gameweeks, judged, type Model } from "../scorecard";

const fixed = (x: number | null | undefined, digits = 3) =>
  x === null || x === undefined ? "–" : x.toFixed(digits);
const list = (gws: number[]) => (gws.length ? gws.map((g) => `GW${g}`).join(", ") : "none");

export function Scorecard() {
  const status = useQuery({ queryKey: ["status"], queryFn: () => unwrap(api.GET("/v1/status")) });
  const scorecard = useQuery({
    queryKey: ["scorecard"],
    queryFn: () => unwrap(api.GET("/v1/scorecard")),
  });

  return (
    <>
      <section>
        <h1>FPL points, predicted in public</h1>
        <p className="lede">
          Each gameweek, every model's predictions for every player are committed before the
          deadline, then scored against what happened. Every gameweek is shown here, good or
          bad, beside the baseline.
        </p>
        {status.isPending && <p aria-busy="true">Loading…</p>}
        {/* One error is enough when the whole API is down. */}
        {status.isError && !scorecard.isError && <Problem error={status.error} />}
        {status.data && (
          <dl className="facts" aria-label="Status">
            <div>
              <dt>Season</dt>
              <dd>{status.data.season}</dd>
            </div>
            <div>
              <dt>Next deadline</dt>
              <dd>
                {status.data.next_gameweek
                  ? `GW${status.data.next_gameweek}, ${formatUtc(status.data.next_deadline_utc)}`
                  : "none scheduled"}
              </dd>
            </div>
            {status.data.next_gameweek !== null && (
              <div>
                <dt>Entries for it</dt>
                <dd>{status.data.next_gameweek_entries.join(", ") || "none yet"}</dd>
              </div>
            )}
            <div>
              <dt>Latest settled gameweek</dt>
              <dd>{status.data.latest_settled_gameweek ?? "none yet"}</dd>
            </div>
          </dl>
        )}
      </section>

      <section aria-labelledby="scorecard">
        <h2 id="scorecard">Scorecard</h2>
        {scorecard.isPending && <p aria-busy="true">Loading…</p>}
        {scorecard.isError && <Problem error={scorecard.error} />}
        {scorecard.data && (
          <ScorecardTables season={scorecard.data.season} models={scorecard.data.models} />
        )}
      </section>
    </>
  );
}

function ScorecardTables({ season, models }: { season: string; models: Model[] }) {
  if (!models.length) return <p>No model has an entry yet.</p>;
  const baseline = models.find((m) => m.role === "baseline");
  const weeks = gameweeks(models);

  return (
    <>
      <p className="note">
        Mean absolute error (MAE) of predicted against actual points, pooled over every player
        in every scored gameweek. Lower is better. Each model is compared with the baseline on
        the gameweeks both were scored, since pooled figures over different weeks would compare
        weeks, not models.
      </p>
      <div className="table-scroll">
        <table aria-label="Pooled">
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Role</th>
              <th scope="col">Scored</th>
              <th scope="col">Missed</th>
              <th scope="col" className="num">MAE</th>
              <th scope="col" className="num">RMSE</th>
              <th scope="col" className="num">Spearman</th>
              <th scope="col" className="num" title="Players the model expected to play">
                MAE, expected to play
              </th>
              <th scope="col" className="num">Minutes MAE</th>
              <th scope="col" className="num">MAE, weeks the baseline was scored</th>
              <th scope="col" className="num">Baseline MAE, same weeks</th>
              <th scope="col" className="num">Difference</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const vs = againstBaseline(m, baseline);
              return (
                <tr key={m.model_version}>
                  <th scope="row">
                    <code>{m.model_version}</code>
                  </th>
                  <td>
                    {m.role ?? "–"}
                    {m.retired_after ? ` after GW${m.retired_after}` : ""}
                  </td>
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

      <h3>By gameweek</h3>
      <p className="note">
        MAE for each entry, and its difference from the baseline's that week. Each figure links to
        its file's history in the log, which shows when it was committed. A missed gameweek had no
        entry before its deadline and stays missed.
      </p>
      <div className="table-scroll">
        <table aria-label="By gameweek">
          <thead>
            <tr>
              <th scope="col">Gameweek</th>
              {models.map((m) => (
                <th scope="col" className="num" key={m.model_version}>
                  <code>{m.model_version}</code>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks.map((gw) => (
              <tr key={gw}>
                <th scope="row">GW{gw}</th>
                {models.map((m) => {
                  const c = cell(m, gw, baseline);
                  const key = m.model_version;
                  if (c.kind === "missed") return <td key={key} className="num missed">missed</td>;
                  if (c.kind === "none") return <td key={key} className="num">–</td>;
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
