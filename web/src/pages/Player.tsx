import { useQuery } from "@tanstack/react-query";
import type { CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { POSITION_NAMES } from "../api/queries";
import { ClubBadge, Loading, PlayerPhoto, useTitle } from "../components/bits";
import { Problem } from "../components/Problem";
import { clubColours, clubName } from "../clubs";
import { fixed, positiveInt } from "../format";
import { modelName } from "../models";
import { NotFound } from "./NotFound";

type Fixture = Schemas["PlayedFixture"];

export function Player() {
  const playerId = positiveInt(useParams().playerId);
  return playerId === null ? <NotFound /> : <PlayerPage playerId={playerId} />;
}

function PlayerPage({ playerId }: { playerId: number }) {
  const player = useQuery({
    queryKey: ["player", playerId],
    queryFn: () =>
      unwrap(api.GET("/v1/players/{player_id}", { params: { path: { player_id: playerId } } })),
  });
  useTitle(player.data ? `${player.data.first_name} ${player.data.second_name}` : "Player");

  if (player.isPending) return <div className="wrap page"><Loading /></div>;
  if (player.isError) return <div className="wrap page"><Problem error={player.error} /></div>;
  const p = player.data;

  const models = [...new Set(p.predictions.map((x) => x.model_version))].sort();
  // A fixture with no gameweek (postponed, not yet rescheduled) has no row here.
  const played = new Map<number, Fixture[]>();
  for (const f of p.history) {
    if (f.gameweek !== null) played.set(f.gameweek, [...(played.get(f.gameweek) ?? []), f]);
  }
  const weeks = [...new Set([...played.keys(), ...p.predictions.map((x) => x.gameweek)])].sort(
    (a, b) => b - a,
  );
  const predicted = (gw: number, model: string) =>
    p.predictions.find((x) => x.gameweek === gw && x.model_version === model);
  // The points a week's entries were scored against, where they were; the
  // match data otherwise. The two agree once bonus is final.
  const points = (gw: number, fixtures: Fixture[]) =>
    p.predictions.find((x) => x.gameweek === gw && x.actual_points !== null)?.actual_points ??
    (fixtures.length ? fixtures.reduce((s, f) => s + f.total_points, 0) : null);
  const minutes = (gw: number, fixtures: Fixture[]) =>
    p.predictions.find((x) => x.gameweek === gw && x.actual_minutes !== null)?.actual_minutes ??
    (fixtures.length ? fixtures.reduce((s, f) => s + f.minutes, 0) : null);

  const club = p.clubs.at(-1)?.team ?? "";
  const sum = (key: "total_points" | "minutes" | "goals_scored" | "assists" | "clean_sheets") =>
    p.history.reduce((s, f) => s + f[key], 0);
  // The newest prediction, for the "this gameweek" figure.
  const newest = p.predictions.length ? Math.max(...p.predictions.map((x) => x.gameweek)) : null;
  const next = newest === null ? [] : p.predictions.filter((x) => x.gameweek === newest);

  return (
    <div className="wrap page">
      <section
        className="card player-hero"
        style={{ "--club": clubColours(club).bg } as CSSProperties}
      >
        <PlayerPhoto code={p.player_code} name={p.web_name} team={club} size={96} />
        <div>
          <p className="eyebrow">{POSITION_NAMES[p.position]?.replace(/s$/, "") ?? p.position}</p>
          <h1>
            {p.first_name} {p.second_name}
          </h1>
          <p className="player-meta">
            <ClubBadge team={club} />
            <span className="muted small">
              {/* Spells only when they moved clubs mid-season (ADR 0013). */}
              {p.clubs.length <= 1
                ? `${clubName(club)}, ${p.season}`
                : p.clubs
                    .map((c) => `${clubName(c.team)} GW${c.first_gameweek}–${c.last_gameweek ?? ""}`)
                    .join(", then ")}
            </span>
          </p>
        </div>
        <dl className="stat-strip" style={{ flexBasis: "100%" }}>
          {next.map((x) => (
            <div className="stat" key={x.model_version}>
              <dt>GW{x.gameweek}, {modelName(x.model_version)}</dt>
              <dd>{x.predicted_points.toFixed(1)}</dd>
            </div>
          ))}
          <div className="stat">
            <dt>Season points</dt>
            <dd>{sum("total_points")}</dd>
          </div>
          <div className="stat">
            <dt>Minutes</dt>
            <dd>{sum("minutes")}</dd>
          </div>
          <div className="stat">
            <dt>Goals</dt>
            <dd>{sum("goals_scored")}</dd>
          </div>
          <div className="stat">
            <dt>Assists</dt>
            <dd>{sum("assists")}</dd>
          </div>
        </dl>
      </section>

      <section className="section" aria-labelledby="weeks">
        <h2 id="weeks">Predicted and actual, week by week</h2>
        {weeks.length === 0 ? (
          <p>No matches or predictions yet this season.</p>
        ) : (
          <div className="card card-flush table-scroll">
            <table aria-label="By gameweek">
              <thead>
                <tr>
                  <th scope="col">Gameweek</th>
                  <th scope="col">Opponent</th>
                  <th scope="col" className="num">Minutes</th>
                  <th scope="col" className="num">Points</th>
                  {models.map((m) => (
                    <th scope="col" className="num" key={m}>
                      {modelName(m)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {weeks.map((gw) => {
                  const fixtures = played.get(gw) ?? [];
                  return (
                    <tr key={gw}>
                      <th scope="row">
                        {p.predictions.some((x) => x.gameweek === gw) ? (
                          <Link to={`/gameweeks/${gw}`}>GW{gw}</Link>
                        ) : (
                          `GW${gw}`
                        )}
                      </th>
                      <td>
                        {fixtures.length
                          ? fixtures.map((f) => `${f.opponent} (${f.was_home ? "H" : "A"})`).join(", ")
                          : "–"}
                      </td>
                      <td className="num">{minutes(gw, fixtures) ?? "–"}</td>
                      <td className="num">
                        <strong>{points(gw, fixtures) ?? "–"}</strong>
                      </td>
                      {models.map((m) => {
                        const x = predicted(gw, m);
                        return (
                          <td className="num" key={m}>
                            {fixed(x?.predicted_points, 2)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="note small" style={{ marginTop: "0.75rem" }}>
          Points are FPL points for the gameweek; each model's column is what it predicted before
          the deadline.
        </p>
      </section>
    </div>
  );
}
