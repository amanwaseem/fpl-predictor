import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { Problem } from "../components/Problem";
import { fixed, positiveInt } from "../format";
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

  if (player.isPending) return <p aria-busy="true">Loading…</p>;
  if (player.isError) return <Problem error={player.error} />;
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

  return (
    <section>
      <h1>
        {p.first_name} {p.second_name}
      </h1>
      <p className="lede">
        {p.position}, {p.season}.{" "}
        {/* Spells only when they moved clubs mid-season (ADR 0013). */}
        {p.clubs.length === 1
          ? p.clubs[0].team
          : p.clubs
              .map((c) => `${c.team} GW${c.first_gameweek}–${c.last_gameweek ?? ""}`)
              .join(", then ")}
        .
      </p>
      {weeks.length === 0 ? (
        <p>No matches or predictions yet this season.</p>
      ) : (
        <div className="table-scroll">
          <table aria-label="By gameweek">
            <thead>
              <tr>
                <th scope="col">Gameweek</th>
                <th scope="col">Opponent</th>
                <th scope="col" className="num">Minutes</th>
                <th scope="col" className="num">Points</th>
                {models.map((m) => (
                  <th scope="col" className="num" key={m}>
                    <code>{m}</code>
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
                    <td className="num">{points(gw, fixtures) ?? "–"}</td>
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
    </section>
  );
}
