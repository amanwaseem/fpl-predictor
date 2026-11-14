import { useState } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { type Schemas } from "../api/client";
import { ENTRY_LIMIT, POSITION_NAMES, POSITIONS, useEntry, useModels } from "../api/queries";
import { ClubBadge, Loading, PlayerPhoto, StatusPill } from "../components/bits";
import { useTitle } from "../useTitle";
import { Problem } from "../components/Problem";
import { clubName } from "../clubs";
import { formatLocal, formatUtc, positiveInt, signed } from "../format";
import { entryHistoryUrl } from "../log";
import { modelAbout, modelName } from "../models";
import { NotFound } from "./NotFound";

type Row = Schemas["Prediction"];
type SortKey =
  "predicted_points" | "actual_points" | "error" | "price" | "expected_minutes" | "web_name";

// Rows shown at first, and added by each "show more".
const PAGE = 50;

const COLUMNS: { key: SortKey; label: string; numeric: boolean; className?: string }[] = [
  { key: "web_name", label: "Player", numeric: false },
  { key: "price", label: "Price", numeric: true },
  { key: "expected_minutes", label: "Minutes", numeric: true, className: "hide-sm" },
  { key: "predicted_points", label: "Predicted", numeric: true },
  { key: "actual_points", label: "Actual", numeric: true },
  { key: "error", label: "Error", numeric: true, className: "hide-sm" },
];

const error = (r: Row) => (r.actual_points === null ? null : r.predicted_points - r.actual_points);

// The rows a page was expanded to earlier in this tab, or null.
function stored(key: string): number | null {
  try {
    return Number(sessionStorage.getItem(key)) || null;
  } catch {
    return null;
  }
}

function sortValue(r: Row, key: SortKey): number | string | null {
  return key === "error" ? error(r) : r[key];
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
  const navigate = useNavigate();
  const models = useModels();

  const logged = (models.data?.models ?? []).filter((m) => m.gameweeks_logged.includes(gameweek));
  const preferred = logged.find((m) => m.role === "main") ?? logged[0];
  // A ?model= with no entry this gameweek falls back, so the select shows
  // the model whose entry is on screen.
  const asked = logged.find((m) => m.model_version === params.get("model"));
  const model = (asked ?? preferred)?.model_version;
  const allGameweeks = [
    ...new Set((models.data?.models ?? []).flatMap((m) => m.gameweeks_logged)),
  ].sort((a, b) => b - a);

  const entry = useEntry(gameweek, model);

  const update = (key: string, value: string | null) => {
    const next = kept();
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const items = entry.data?.items ?? [];
  // A value a shared or hand-typed link may carry that no control can show
  // (a club from another season, a position or column that does not exist)
  // is ignored rather than silently filtering out every row.
  const asked_position = params.get("position");
  const position = POSITIONS.some((p) => p === asked_position) ? asked_position : null;
  const asked_team = params.get("team");
  const team = entry.data && !items.some((r) => r.team === asked_team) ? null : asked_team;
  const query = (params.get("q") ?? "").trim().toLowerCase();
  const asked_sort = params.get("sort");
  const sort = COLUMNS.find((c) => c.key === asked_sort)?.key ?? "predicted_points";
  const ascending = params.get("dir") === "asc";

  // The link's parameters without those ignored above, so a control's change
  // does not carry them on to apply later, when they might match.
  const kept = () => {
    const next = new URLSearchParams(params);
    if (position === null) next.delete("position");
    if (team === null) next.delete("team");
    if (asked_sort !== null && asked_sort !== sort) next.delete("sort");
    return next;
  };

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
    .filter((r) => !team || r.team === team)
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
  const teams = [...new Set(items.map((r) => r.team))].sort();
  const scored = items.some((r) => r.actual_points !== null);
  const top = Math.max(1, ...items.map((r) => r.predicted_points));

  const sortBy = (key: SortKey) => {
    const next = kept();
    next.set("sort", key);
    // A second click on the same column reverses it; names start A to Z.
    const flip = sort === key ? !ascending : key === "web_name";
    if (flip) next.set("dir", "asc");
    else next.delete("dir");
    setParams(next, { replace: true });
  };

  return (
    <div className="wrap page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Predictions</p>
          <h1>Gameweek {gameweek}</h1>
          {model && <p className="muted">{modelAbout(model) ?? model}</p>}
        </div>
        {logged.length > 0 && (
          <div className="controls">
            <label className="field">
              Gameweek
              <select
                value={gameweek}
                // Filters carry over; the model is chosen afresh for that gameweek.
                onChange={(e) => {
                  const next = new URLSearchParams(params);
                  next.delete("model");
                  navigate(`/gameweeks/${e.target.value}?${next}`);
                }}
              >
                {allGameweeks.map((gw) => (
                  <option key={gw} value={gw}>
                    Gameweek {gw}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Model
              <select value={model} onChange={(e) => update("model", e.target.value)}>
                {logged.map((m) => (
                  <option key={m.model_version} value={m.model_version}>
                    {modelName(m.model_version)}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
      </div>

      {models.isPending && <Loading />}
      {models.isError && <Problem error={models.error} />}
      {models.data && !logged.length && <p>Nothing was predicted for gameweek {gameweek}.</p>}

      {logged.length > 0 && (
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Position">
            <button type="button" aria-pressed={!position} onClick={() => update("position", null)}>
              All
            </button>
            {POSITIONS.map((p) => (
              <button
                key={p}
                type="button"
                aria-pressed={position === p}
                onClick={() => update("position", p)}
              >
                {POSITION_NAMES[p]}
              </button>
            ))}
          </div>
          <div className="controls">
            <label className="field">
              Club
              <select value={team ?? ""} onChange={(e) => update("team", e.target.value)}>
                <option value="">All clubs</option>
                {teams.map((t) => (
                  <option key={t} value={t}>
                    {clubName(t)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Search
              <input
                type="search"
                placeholder="Player name"
                value={params.get("q") ?? ""}
                onChange={(e) => update("q", e.target.value)}
              />
            </label>
          </div>
        </div>
      )}

      {entry.isPending && model && <Loading what="Loading predictions" />}
      {entry.isError && <Problem error={entry.error} />}
      {entry.data && (
        <>
          <div className="entry-meta">
            <span>
              {modelName(entry.data.model_version)} <code>{entry.data.model_version}</code>
            </span>
            <span title={formatUtc(entry.data.generated_at_utc)}>
              Made {formatLocal(entry.data.generated_at_utc)}
            </span>
            <span title={formatUtc(entry.data.deadline_utc)}>
              Deadline {formatLocal(entry.data.deadline_utc)}
            </span>
            <span>
              Snapshot <code>{entry.data.snapshot_id}</code>
            </span>
            <span>{scored ? "Scored: error is predicted minus actual" : "Not scored yet"}</span>
            {(() => {
              const href = entryHistoryUrl(entry.data.season, gameweek, entry.data.model_version);
              return href ? <a href={href}>Proof it was published in time</a> : null;
            })()}
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
                  {/* Before scoring, Actual and Error are dashes: a phone does without them. */}
                  {COLUMNS.map((c) => (
                    <SortHeader
                      key={c.key}
                      column={
                        !scored && c.key === "actual_points" ? { ...c, className: "hide-sm" } : c
                      }
                      sort={sort}
                      ascending={ascending}
                      onSort={sortBy}
                    />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((r) => {
                  const e = error(r);
                  return (
                    <tr key={r.player_id} data-position={r.position}>
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
                              <StatusPill status={r.status} />
                            </span>
                          </span>
                        </span>
                      </th>
                      <td className="num price">£{r.price.toFixed(1)}m</td>
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
                      <td className={scored ? "num" : "num hide-sm"}>{r.actual_points ?? "–"}</td>
                      {/* Uncoloured: the sign says over or under, not better or worse,
                          which is what the scorecard's colours mean. */}
                      <td className="num hide-sm">{e === null ? "–" : signed(e, 2)}</td>
                    </tr>
                  );
                })}
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
            are what the model expected for the whole gameweek.
          </p>
        </>
      )}
    </div>
  );
}

function SortHeader({
  column,
  sort,
  ascending,
  onSort,
}: {
  column: (typeof COLUMNS)[number];
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
