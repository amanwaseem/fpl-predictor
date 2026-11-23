import { useState } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { type Schemas } from "../api/client";
import {
  ENTRY_LIMIT,
  POSITION_NAMES,
  POSITIONS,
  useEntry,
  useGameweek,
  useModels,
} from "../api/queries";
import { Chip, ClubBadge, Loading, PlayerPhoto, StatusPill, Stepper } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { clubName } from "../clubs";
import { opponentText } from "../fixtures";
import { formatLocal, formatUtc, positiveInt } from "../format";
import { entryHistoryUrl } from "../log";
import { byPreference, mainIn, modelName } from "../models";
import { NotFound } from "./NotFound";

type Row = Schemas["Prediction"];
type SortKey =
  "predicted_points" | "value" | "actual_points" | "price" | "expected_minutes" | "web_name";

// Rows shown at first, and added by each "show more".
const PAGE = 50;

// Short enough that all five fit on a phone (#197).
const SHORT: Record<string, string> = { GKP: "GK", DEF: "DEF", MID: "MID", FWD: "FWD" };

type Column = { key: SortKey; label: string; numeric: boolean; className?: string };
const PLAYER: Column = { key: "web_name", label: "Player", numeric: false };
const PRICE: Column = { key: "price", label: "Price", numeric: true, className: "hide-xs" };
const MINUTES: Column = {
  key: "expected_minutes",
  label: "Minutes",
  numeric: true,
  className: "hide-sm",
};
const PREDICTED: Column = { key: "predicted_points", label: "Predicted", numeric: true };
const VALUE: Column = { key: "value", label: "Per £m", numeric: true };
// Only once the gameweek is scored: before that it would be a column of dashes.
const ACTUAL: Column = { key: "actual_points", label: "Actual", numeric: true };
const SORTS: SortKey[] = [PLAYER, PRICE, MINUTES, PREDICTED, VALUE, ACTUAL].map((c) => c.key);

// Predicted points per £1m: what a manager buys with the money.
const value = (r: Row) => (r.price > 0 ? r.predicted_points / r.price : 0);

// The rows a page was expanded to earlier in this tab, or null.
function stored(key: string): number | null {
  try {
    return Number(sessionStorage.getItem(key)) || null;
  } catch {
    return null;
  }
}

function sortValue(r: Row, key: SortKey): number | string | null {
  return key === "value" ? value(r) : r[key];
}

// /gameweeks: the newest gameweek with an entry.
export function LatestGameweek() {
  useTitle("Predictions");
  const models = useModels();
  if (models.isError)
    return (
      <div className="wrap page">
        <Problem error={models.error} />
      </div>
    );
  if (!models.data)
    return (
      <div className="wrap page">
        <Loading />
      </div>
    );
  const latest = Math.max(0, ...models.data.models.flatMap((m) => m.gameweeks_logged));
  if (!latest)
    return (
      <div className="wrap page">
        <p>No predictions yet.</p>
      </div>
    );
  return <Navigate to={`/gameweeks/${latest}`} replace />;
}

export function Gameweek() {
  const gameweek = positiveInt(useParams().gameweek);
  return gameweek === null ? <NotFound /> : <Entry gameweek={gameweek} />;
}

function Entry({ gameweek }: { gameweek: number }) {
  useTitle(`Gameweek ${gameweek} predictions`);
  const [params, setParams] = useSearchParams();
  const models = useModels();
  const detail = useGameweek(gameweek);

  // One set of predictions for users (#194): the model that was main that
  // gameweek, or for a gameweek before one ran, the baseline's. A link may
  // still ask for another by ?model=; one with no entry this gameweek falls back.
  const logged = byPreference(models.data?.models, gameweek);
  const asked = logged.find((m) => m.model_version === params.get("model"));
  const showing = asked ?? logged[0];
  const model = showing?.model_version;
  const main = mainIn(models.data?.models, gameweek);
  const allGameweeks = [
    ...new Set((models.data?.models ?? []).flatMap((m) => m.gameweeks_logged)),
  ].sort((a, b) => a - b);

  const entry = useEntry(gameweek, model);
  const items = entry.data?.items ?? [];
  const fixtures = detail.data?.fixtures;

  // A value a shared or hand-typed link may carry that no control can show
  // (a club from another season, a position or column that does not exist,
  // a price outside the entry's) is ignored rather than silently filtering
  // out every row.
  const asked_position = params.get("position");
  const position = POSITIONS.some((p) => p === asked_position) ? asked_position : null;
  const clubs = [...new Set(items.map((r) => r.team))].sort();
  const asked_teams = (params.get("team") ?? "").split(",").filter(Boolean);
  const teams = entry.data ? asked_teams.filter((t) => clubs.includes(t)) : asked_teams;
  const prices = items.map((r) => r.price);
  const cheapest = Math.floor(Math.min(...prices) * 2) / 2;
  const dearest = Math.ceil(Math.max(...prices) * 2) / 2;
  const asked_max = Number(params.get("max"));
  const maxPrice =
    params.get("max") !== null &&
    Number.isFinite(asked_max) &&
    (!entry.data || (asked_max >= cheapest && asked_max < dearest))
      ? asked_max
      : null;
  const fit = params.get("fit") === "1";
  const query = (params.get("q") ?? "").trim().toLowerCase();
  const asked_sort = params.get("sort");
  const sort = SORTS.find((k) => k === asked_sort) ?? "predicted_points";
  const ascending = params.get("dir") === "asc";

  // The link's parameters without those ignored above, so a control's change
  // does not carry them on to apply later, when they might match.
  const kept = () => {
    const next = new URLSearchParams(params);
    if (position === null) next.delete("position");
    if (teams.length) next.set("team", teams.join(","));
    else next.delete("team");
    if (maxPrice === null) next.delete("max");
    if (asked_sort !== null && asked_sort !== sort) next.delete("sort");
    return next;
  };
  const update = (key: string, value: string | null) => {
    const next = kept();
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const withoutModel = () => {
    const next = kept();
    next.delete("model");
    return next;
  };
  const toggleTeam = (t: string) => {
    const chosen = teams.includes(t) ? teams.filter((x) => x !== t) : [...teams, t];
    update("team", chosen.sort().join(","));
  };

  // How many kinds of filter are on: clubs, price, fitness.
  const filtering = [teams.length > 0, maxPrice !== null, fit].filter(Boolean).length;
  const [filtersOpen, setFiltersOpen] = useState(false);

  // How far the reader has expanded the list, kept per history entry and
  // address, so Back from a player returns to the same rows, and a filter or
  // sort, which changes the address, starts again. Not by history key alone:
  // every page loaded directly has the key "default", so one would inherit
  // another's, and the model resolving after the page loads would reset it.
  // The history key as well: a new visit to the same address starts again,
  // and Back returns to the entry's own count.
  const { key, pathname, search } = useLocation();
  const remembered = `shown:${key}:${pathname}${search}`;
  const [expanded, setExpanded] = useState<Record<string, number>>({});
  const shown = expanded[remembered] ?? stored(remembered) ?? PAGE;
  const setShown = (count: number) => {
    setExpanded((all) => ({ ...all, [remembered]: count }));
    try {
      sessionStorage.setItem(remembered, String(count));
    } catch {
      // Storage may be unavailable; the list just starts at the top again.
    }
  };

  const rows = items
    .filter((r) => !position || r.position === position)
    .filter((r) => !teams.length || teams.includes(r.team))
    .filter((r) => maxPrice === null || r.price <= maxPrice)
    .filter((r) => !fit || r.status === "a")
    .filter((r) => !query || r.web_name.toLowerCase().includes(query))
    .sort((a, b) => {
      const x = sortValue(a, sort);
      const y = sortValue(b, sort);
      // Unknowns (not yet scored) last, whichever way round.
      if (x === null || y === null) {
        if (x !== y) return x === null ? 1 : -1;
      } else {
        const order =
          typeof x === "string" && typeof y === "string"
            ? x.localeCompare(y)
            : x < y
              ? -1
              : x > y
                ? 1
                : 0;
        if (order) return ascending ? order : -order;
      }
      // Ties by player id, as the log orders them, whichever way round.
      return a.player_id - b.player_id;
    });
  const scored = items.some((r) => r.actual_points !== null);
  const top = Math.max(1, ...items.map((r) => r.predicted_points));
  // Who each player plays, once the fixtures are known. None at all means
  // the database has the log but no football data.
  const withOpponents = !!fixtures?.length;
  const columns = [
    PRICE,
    MINUTES,
    PREDICTED,
    ...(sort === "value" ? [VALUE] : []),
    ...(scored ? [ACTUAL] : []),
  ];

  const sortBy = (key: SortKey) => {
    const next = kept();
    next.set("sort", key);
    // A second click on the same column reverses it; names start A to Z.
    const flip = sort === key ? !ascending : key === "web_name";
    if (flip) next.set("dir", "asc");
    else next.delete("dir");
    setParams(next, { replace: true });
  };

  // The sort buttons: highest first, whatever the order was.
  const sortDown = (key: SortKey) => {
    const next = kept();
    if (key === "predicted_points") next.delete("sort");
    else next.set("sort", key);
    next.delete("dir");
    setParams(next, { replace: true });
  };

  // The arrows step through the gameweeks with an entry, filters kept; the
  // model is chosen afresh for each.
  const here = allGameweeks.indexOf(gameweek);
  const step = (gw: number | undefined) =>
    gw === undefined ? null : { to: `/gameweeks/${gw}?${withoutModel()}`, label: `Gameweek ${gw}` };
  const deadline = detail.data?.deadline_utc ?? entry.data?.deadline_utc;

  return (
    <div className="wrap page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Predictions</p>
          <Stepper
            previous={here > 0 ? step(allGameweeks[here - 1]) : null}
            next={here >= 0 ? step(allGameweeks[here + 1]) : null}
          >
            <h1>Gameweek {gameweek}</h1>
          </Stepper>
          {deadline && <p className="muted small">Deadline {formatLocal(deadline)}</p>}
          {showing && showing.model_version !== main?.model_version && (
            <p className="note small">
              {main && main.gameweeks_logged.includes(gameweek) ? (
                <>
                  These are the {modelName(showing.model_version)} model&rsquo;s predictions, not
                  our main ones.{" "}
                  <Link to={`/gameweeks/${gameweek}?${withoutModel()}`}>
                    See our main predictions
                  </Link>
                </>
              ) : main ? (
                <>
                  Our main model missed the gameweek {gameweek} deadline, so these are the{" "}
                  {modelName(showing.model_version)} model&rsquo;s predictions.
                </>
              ) : (
                <>
                  Our main model wasn&rsquo;t running yet in gameweek {gameweek}, so these are the{" "}
                  {modelName(showing.model_version)} model&rsquo;s predictions.
                </>
              )}
            </p>
          )}
        </div>
      </div>

      {models.isPending && <Loading />}
      {models.isError && <Problem error={models.error} />}
      {models.data && !logged.length && <p>Nothing was predicted for gameweek {gameweek}.</p>}

      {logged.length > 0 && (
        <>
          <div className="toolbar">
            <div className="segmented" role="group" aria-label="Position">
              <button
                type="button"
                aria-pressed={!position}
                onClick={() => update("position", null)}
              >
                All
              </button>
              {POSITIONS.map((p) => (
                <button
                  key={p}
                  type="button"
                  title={POSITION_NAMES[p]}
                  aria-pressed={position === p}
                  onClick={() => update("position", p)}
                >
                  {SHORT[p]}
                </button>
              ))}
            </div>
            <div className="controls">
              <label className="field">
                Search
                <input
                  type="search"
                  placeholder="Player name"
                  value={params.get("q") ?? ""}
                  onChange={(e) => update("q", e.target.value)}
                />
              </label>
              <button
                type="button"
                className="button button-ghost"
                aria-expanded={filtersOpen}
                aria-controls="filters"
                onClick={() => setFiltersOpen(!filtersOpen)}
              >
                Filters{filtering ? ` (${filtering})` : ""}
              </button>
            </div>
          </div>
          {filtersOpen && (
            <div className="card filters" id="filters">
              <div className="chips" role="group" aria-label="Clubs">
                {clubs.map((t) => (
                  <Chip key={t} pressed={teams.includes(t)} onClick={() => toggleTeam(t)}>
                    <span title={clubName(t)}>{t}</span>
                  </Chip>
                ))}
              </div>
              {prices.length > 0 && (
                <label className="field price-field">
                  <span>Price: {maxPrice === null ? "any" : `up to £${maxPrice.toFixed(1)}m`}</span>
                  <input
                    type="range"
                    min={cheapest}
                    max={dearest}
                    step={0.5}
                    value={maxPrice ?? dearest}
                    aria-valuetext={maxPrice === null ? "Any price" : `Up to £${maxPrice}m`}
                    onChange={(e) =>
                      update("max", Number(e.target.value) >= dearest ? null : e.target.value)
                    }
                  />
                </label>
              )}
              <label className="switch">
                <input
                  type="checkbox"
                  checked={fit}
                  onChange={(e) => update("fit", e.target.checked ? "1" : null)}
                />
                Fit players only
              </label>
              {filtering > 0 && (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => {
                    const next = kept();
                    for (const k of ["team", "max", "fit"]) next.delete(k);
                    setParams(next, { replace: true });
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>
          )}
          <div className="segmented sort-by" role="group" aria-label="Sort by">
            <button
              type="button"
              aria-pressed={sort === "predicted_points"}
              onClick={() => sortDown("predicted_points")}
            >
              Points
            </button>
            <button type="button" aria-pressed={sort === "value"} onClick={() => sortDown("value")}>
              Points per £m
            </button>
          </div>
        </>
      )}

      {entry.isPending && model && <Loading what="Loading predictions" />}
      {entry.isError && <Problem error={entry.error} />}
      {entry.data && (
        <>
          {/* The status first: the proof opens to a long line, which would push it aside. */}
          <div className="entry-meta">
            <span>{scored ? "Scored" : "Not scored yet"}</span>
            <Proof entry={entry.data} gameweek={gameweek} />
          </div>
          {entry.data.next_cursor && (
            <p role="alert" className="problem problem-warn">
              This entry has more than {ENTRY_LIMIT} players, and only the first {ENTRY_LIMIT} by
              predicted points are shown.
            </p>
          )}
          <div className="card card-flush table-scroll">
            <table aria-label="Predictions">
              <thead>
                <tr>
                  <SortHeader column={PLAYER} sort={sort} ascending={ascending} onSort={sortBy} />
                  {withOpponents && (
                    <th scope="col" className="opponent-col hide-sm">
                      Opponent
                    </th>
                  )}
                  {columns.map((c) => (
                    <SortHeader
                      key={c.key}
                      column={c}
                      sort={sort}
                      ascending={ascending}
                      onSort={sortBy}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((r) => (
                  <tr key={r.player_id} data-position={r.position} data-team={r.team}>
                    <th scope="row">
                      <span className="player">
                        <PlayerPhoto
                          code={r.player_code}
                          name={r.web_name}
                          team={r.team}
                          size={36}
                        />
                        <span className="player-text">
                          <Link className="player-name" to={`/players/${r.player_id}`}>
                            {r.web_name}
                          </Link>
                          <span className="player-meta">
                            <ClubBadge team={r.team} />
                            <span className="pos">{r.position}</span>
                            {/* On a phone the opponent sits here, not in its own column. */}
                            {withOpponents && (
                              <span className="opponent show-sm">
                                v {opponentText(fixtures, r.team)}
                              </span>
                            )}
                            <StatusPill status={r.status} />
                          </span>
                        </span>
                      </span>
                    </th>
                    {withOpponents && (
                      <td className="opponent-col hide-sm">{opponentText(fixtures, r.team)}</td>
                    )}
                    <td className="num price hide-xs">£{r.price.toFixed(1)}m</td>
                    <td className="num hide-sm">{r.expected_minutes.toFixed(0)}′</td>
                    <td className="num">
                      <span className="points">
                        <span className="bar" aria-hidden="true">
                          <span
                            style={{ width: `${(Math.max(0, r.predicted_points) / top) * 100}%` }}
                          />
                        </span>
                        <strong>{r.predicted_points.toFixed(2)}</strong>
                      </span>
                    </td>
                    {sort === "value" && <td className="num">{value(r).toFixed(2)}</td>}
                    {scored && (
                      // Mint where he scored what we predicted or more, coral where less.
                      <td
                        className={
                          r.actual_points === null
                            ? "num"
                            : r.actual_points >= r.predicted_points
                              ? "num better"
                              : "num worse"
                        }
                      >
                        {r.actual_points ?? "–"}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && (
              <p className="muted" style={{ padding: "1rem" }}>
                No players match.
              </p>
            )}
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
          <p className="note small after">
            Showing {Math.min(shown, rows.length)} of {rows.length} players
            {rows.length !== items.length ? ` (${items.length} in all)` : ""}. Minutes and points
            are what we expected for the whole gameweek.
            {scored ? " Actual is in green where a player scored what we predicted or more." : ""}
          </p>
        </>
      )}
    </div>
  );
}

// The proof that the predictions came before the deadline (#194), folded into
// one line: tap it for when they were made, the deadline, and the entry's
// history in the public log.
function Proof({ entry, gameweek }: { entry: Schemas["PredictionPage"]; gameweek: number }) {
  const early = Date.parse(entry.generated_at_utc) < Date.parse(entry.deadline_utc);
  const href = entryHistoryUrl(entry.season, gameweek, entry.model_version);
  return (
    <details className={early ? "proof proof-early" : "proof"}>
      <summary>{early ? "✓ Published before the deadline" : "Published"}</summary>
      <p>
        Made{" "}
        <span title={formatUtc(entry.generated_at_utc)}>{formatLocal(entry.generated_at_utc)}</span>
        , deadline{" "}
        <span title={formatUtc(entry.deadline_utc)}>{formatLocal(entry.deadline_utc)}</span>.{" "}
        {href && <a href={href}>See when it was committed to the public log</a>}
      </p>
    </details>
  );
}

function SortHeader({
  column,
  sort,
  ascending,
  onSort,
}: {
  column: Column;
  sort: SortKey;
  ascending: boolean;
  onSort: (key: SortKey) => void;
}) {
  const active = sort === column.key;
  return (
    <th
      scope="col"
      className={
        [column.numeric ? "num" : "", column.className ?? ""].join(" ").trim() || undefined
      }
      aria-sort={active ? (ascending ? "ascending" : "descending") : "none"}
    >
      <button type="button" className="sort" onClick={() => onSort(column.key)}>
        {column.label}
        {active ? (ascending ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
}
