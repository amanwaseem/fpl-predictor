import { useQuery } from "@tanstack/react-query";
import type { CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { POSITION_NAMES, useGameweek, useModels, useNews } from "../api/queries";
import { ClubBadge, Loading, PlayerPhoto } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { clubColours, clubName } from "../clubs";
import { opponentText } from "../fixtures";
import { fixed, formatLocal, positiveInt } from "../format";
import { pickPrediction } from "../models";
import { chanceText, isOut } from "../news";
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
  // The roles say which prediction to show (#194). Waited for, so the figures
  // don't change under the reader when they arrive; without them, any model's.
  const models = useModels();
  // His doubt, if FPL has one now (#200), and the newest predicted gameweek's
  // fixtures, for who he plays.
  const news = useNews().data?.items.find((n) => n.player_id === playerId);
  const predictions = player.data?.predictions ?? [];
  const newestGameweek = predictions.length
    ? Math.max(...predictions.map((x) => x.gameweek))
    : null;
  const newestFixtures = useGameweek(newestGameweek).data?.fixtures;

  if (player.isPending || models.isPending)
    return (
      <div className="wrap page">
        <Loading />
      </div>
    );
  if (player.isError)
    return (
      <div className="wrap page">
        <Problem error={player.error} />
      </div>
    );
  const p = player.data;

  // A fixture with no gameweek (postponed, not yet rescheduled) has no row here.
  const played = new Map<number, Fixture[]>();
  for (const f of p.history) {
    if (f.gameweek !== null) played.set(f.gameweek, [...(played.get(f.gameweek) ?? []), f]);
  }
  const weeks = [...new Set([...played.keys(), ...p.predictions.map((x) => x.gameweek)])].sort(
    (a, b) => b - a,
  );
  // One prediction a week: that week's main model's where it was logged, else
  // the baseline's.
  const predicted = (gw: number) =>
    pickPrediction(
      p.predictions.filter((x) => x.gameweek === gw),
      models.data?.models,
      gw,
    );
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
  // This week: the newest prediction, who he plays, and, while it is still
  // to be played, his chance of playing.
  const thisWeek = newestGameweek === null ? undefined : predicted(newestGameweek);
  const thisWeekPlayed = thisWeek ? played.has(thisWeek.gameweek) : false;
  const opponent = !thisWeek
    ? null
    : newestFixtures?.length
      ? opponentText(newestFixtures, club)
      : played.has(thisWeek.gameweek)
        ? played
            .get(thisWeek.gameweek)!
            .map((f) => `${f.opponent} (${f.was_home ? "H" : "A"})`)
            .join(", ")
        : p.upcoming
            .filter((u) => u.gameweek === thisWeek.gameweek)
            .map((u) => `${u.opponent} (${u.was_home ? "H" : "A"})`)
            .join(", ") || null;

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
                    .map(
                      (c) => `${clubName(c.team)} GW${c.first_gameweek}–${c.last_gameweek ?? ""}`,
                    )
                    .join(", then ")}
            </span>
            {p.price !== null && <span className="player-price">£{p.price.toFixed(1)}m</span>}
          </p>
          {news && (
            <p className={isOut(news) ? "player-news player-news-out" : "player-news"}>
              <strong>{chanceText(news)}</strong> {news.news || "No details given"}
            </p>
          )}
        </div>
      </section>

      {thisWeek && (
        <section className="section" aria-labelledby="this-week">
          <h2 id="this-week">Gameweek {thisWeek.gameweek}</h2>
          <dl className="card stat-strip this-week">
            <div className="stat">
              <dt>Our prediction</dt>
              <dd>{thisWeek.predicted_points.toFixed(1)}</dd>
            </div>
            <div className="stat">
              <dt>Opponent</dt>
              <dd className="stat-text">{opponent ?? "–"}</dd>
            </div>
            {!thisWeekPlayed && (
              <div className="stat">
                <dt>Chance of playing</dt>
                <dd className="stat-text">{news ? chanceText(news) : "Fit"}</dd>
              </div>
            )}
          </dl>
        </section>
      )}

      <section className="section" aria-labelledby="next">
        <h2 id="next">Next five matches</h2>
        {p.upcoming.length === 0 ? (
          <p className="muted">No matches to come are known yet.</p>
        ) : (
          <ol className="card upcoming" aria-label="Next matches">
            {p.upcoming.map((u) => (
              <li key={`${u.gameweek}-${u.kickoff_utc}`}>
                <span className="upcoming-gw">{u.gameweek ? `GW${u.gameweek}` : "TBC"}</span>
                <ClubBadge team={u.opponent} />
                <span>{u.was_home ? "Home" : "Away"}</span>
                <span className="muted small" title={formatLocal(u.kickoff_utc)}>
                  {new Date(u.kickoff_utc).toLocaleDateString("en-GB", {
                    weekday: "short",
                    day: "numeric",
                    month: "short",
                  })}
                </span>
                {/* Predicted only for the gameweek we have predicted. */}
                {thisWeek && !thisWeekPlayed && u.gameweek === thisWeek.gameweek && (
                  <strong className="upcoming-points">
                    {thisWeek.predicted_points.toFixed(1)} pts
                  </strong>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="section" aria-labelledby="season">
        <h2 id="season">Season so far</h2>
        <dl className="card stat-strip">
          <div className="stat">
            <dt>Points</dt>
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
                  <th scope="col" className="num">
                    Minutes
                  </th>
                  <th scope="col" className="num">
                    Points
                  </th>
                  <th scope="col" className="num">
                    Predicted
                  </th>
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
                          ? fixtures
                              .map((f) => `${f.opponent} (${f.was_home ? "H" : "A"})`)
                              .join(", ")
                          : "–"}
                      </td>
                      <td className="num">{minutes(gw, fixtures) ?? "–"}</td>
                      <td className="num">
                        <strong>{points(gw, fixtures) ?? "–"}</strong>
                      </td>
                      <td className="num">{fixed(predicted(gw)?.predicted_points, 2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="note small after">
          Points are what the player scored in FPL that gameweek. Predicted is what we predicted
          before its deadline.
        </p>
      </section>
    </div>
  );
}
