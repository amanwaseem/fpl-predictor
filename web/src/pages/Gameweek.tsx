import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, unwrap, type Schemas } from "../api/client";
import { POSITIONS, useModels } from "../api/queries";
import { Problem } from "../components/Problem";
import { fixed, formatUtc, positiveInt, signed } from "../format";
import { entryHistoryUrl } from "../log";
import { NotFound } from "./NotFound";

type Row = Schemas["Prediction"];
type SortKey = "predicted_points" | "actual_points" | "error" | "price" | "expected_minutes" | "web_name";

// FPL's availability codes; "a", available, is shown as nothing.
const STATUS: Record<string, string> = {
  d: "doubtful",
  i: "injured",
  s: "suspended",
  u: "unavailable",
  n: "not in the squad",
};
// An entry covers every player in the game, about 700; one page holds them all.
const ENTRY_LIMIT = 1000;

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "web_name", label: "Player", numeric: false },
  { key: "price", label: "Price", numeric: true },
  { key: "expected_minutes", label: "Expected minutes", numeric: true },
  { key: "predicted_points", label: "Predicted", numeric: true },
  { key: "actual_points", label: "Actual", numeric: true },
  { key: "error", label: "Error", numeric: true },
];

const error = (r: Row) => (r.actual_points === null ? null : r.predicted_points - r.actual_points);

function sortValue(r: Row, key: SortKey): number | string | null {
  return key === "error" ? error(r) : r[key];
}

// /gameweeks: the newest gameweek with an entry.
export function LatestGameweek() {
  const models = useModels();
  if (models.isError) return <Problem error={models.error} />;
  if (!models.data) return <p aria-busy="true">Loading…</p>;
  const latest = Math.max(0, ...models.data.models.flatMap((m) => m.gameweeks_logged));
  if (!latest) return <p>No entries yet.</p>;
  return <Navigate to={`/gameweeks/${latest}`} replace />;
}

export function Gameweek() {
  const gameweek = positiveInt(useParams().gameweek);
  return gameweek === null ? <NotFound /> : <Entry gameweek={gameweek} />;
}

function Entry({ gameweek }: { gameweek: number }) {
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
  ].sort((a, b) => a - b);

  const entry = useQuery({
    queryKey: ["predictions", gameweek, model],
    enabled: !!model,
    queryFn: () =>
      unwrap(
        api.GET("/v1/gameweeks/{gameweek}/predictions", {
          params: { path: { gameweek }, query: { model: model!, limit: ENTRY_LIMIT } },
        }),
      ),
  });

  const update = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const position = params.get("position");
  const team = params.get("team");
  const query = (params.get("q") ?? "").trim().toLowerCase();
  const sort = (params.get("sort") as SortKey | null) ?? "predicted_points";
  const ascending = params.get("dir") === "asc";

  const rows = (entry.data?.items ?? [])
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
          typeof x === "string" && typeof y === "string" ? x.localeCompare(y) : x < y ? -1 : x > y ? 1 : 0;
        if (order) return ascending ? order : -order;
      }
      // Ties by player id, as the log orders them, whichever way round.
      return a.player_id - b.player_id;
    });
  const teams = [...new Set((entry.data?.items ?? []).map((r) => r.team))].sort();
  const scored = entry.data?.items.some((r) => r.actual_points !== null);

  const sortBy = (key: SortKey) => {
    const next = new URLSearchParams(params);
    next.set("sort", key);
    // A second click on the same column reverses it; names start A to Z.
    const flip = sort === key ? !ascending : key === "web_name";
    if (flip) next.set("dir", "asc");
    else next.delete("dir");
    setParams(next, { replace: true });
  };

  return (
    <section>
      <h1>Gameweek {gameweek} predictions</h1>
      {models.isPending && <p aria-busy="true">Loading…</p>}
      {models.isError && <Problem error={models.error} />}
      {models.data && !logged.length && <p>No model has an entry for gameweek {gameweek}.</p>}

      {logged.length > 0 && (
        <div className="controls">
          <label>
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
                  GW{gw}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model
            <select value={model} onChange={(e) => update("model", e.target.value)}>
              {logged.map((m) => (
                <option key={m.model_version} value={m.model_version}>
                  {m.model_version}
                </option>
              ))}
            </select>
          </label>
          <label>
            Position
            <select value={position ?? ""} onChange={(e) => update("position", e.target.value)}>
              <option value="">All</option>
              {POSITIONS.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            Club
            <select value={team ?? ""} onChange={(e) => update("team", e.target.value)}>
              <option value="">All</option>
              {teams.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            Name
            <input
              type="search"
              value={params.get("q") ?? ""}
              onChange={(e) => update("q", e.target.value)}
            />
          </label>
        </div>
      )}

      {entry.isPending && model && <p aria-busy="true">Loading…</p>}
      {entry.isError && <Problem error={entry.error} />}
      {entry.data && (
        <>
          <p className="note">
            <code>{entry.data.model_version}</code>, made {formatUtc(entry.data.generated_at_utc)}{" "}
            from snapshot <code>{entry.data.snapshot_id}</code>, for the deadline{" "}
            {formatUtc(entry.data.deadline_utc)}.{" "}
            {(() => {
              const href = entryHistoryUrl(entry.data.season, gameweek, entry.data.model_version);
              return href ? <a href={href}>Its history in the log.</a> : null;
            })()}{" "}
            {scored ? "Error is predicted minus actual." : "Not scored yet."} {rows.length} of{" "}
            {entry.data.items.length} players.
          </p>
          {entry.data.next_cursor && (
            <p role="alert" className="problem">
              This entry has more than {ENTRY_LIMIT} players, and only the first {ENTRY_LIMIT} by
              predicted points are shown.
            </p>
          )}
          <div className="table-scroll">
            <table aria-label="Predictions">
              <thead>
                <tr>
                  {COLUMNS.slice(0, 1).map((c) => (
                    <SortHeader key={c.key} column={c} sort={sort} ascending={ascending} onSort={sortBy} />
                  ))}
                  <th scope="col">Club</th>
                  <th scope="col">Pos</th>
                  {COLUMNS.slice(1).map((c) => (
                    <SortHeader key={c.key} column={c} sort={sort} ascending={ascending} onSort={sortBy} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const e = error(r);
                  return (
                    <tr key={r.player_id}>
                      <th scope="row">
                        <Link to={`/players/${r.player_id}`}>{r.web_name}</Link>
                        {STATUS[r.status] && <span className="flag"> {STATUS[r.status]}</span>}
                      </th>
                      <td>{r.team}</td>
                      <td>{r.position}</td>
                      <td className="num">£{r.price.toFixed(1)}m</td>
                      <td className="num">{r.expected_minutes.toFixed(0)}</td>
                      <td className="num">{fixed(r.predicted_points, 2)}</td>
                      <td className="num">{r.actual_points ?? "–"}</td>
                      <td className="num">{e === null ? "–" : signed(e, 2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
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
      className={column.numeric ? "num" : undefined}
      aria-sort={active ? (ascending ? "ascending" : "descending") : "none"}
    >
      <button type="button" className="sort" onClick={() => onSort(column.key)}>
        {column.label}
        {active ? (ascending ? " ↑" : " ↓") : ""}
      </button>
    </th>
  );
}
